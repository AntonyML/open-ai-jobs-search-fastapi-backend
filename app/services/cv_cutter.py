"""CV cutter — relevance-weighted bullet removal to keep CV within page limits.

When a rendered CV exceeds the expected page count (2 pages), this service
iteratively removes the LOWEST-SCORING bullets based on:

1. **Relevance** — keyword overlap between the bullet and the job posting
2. **Uniqueness** — how distinct this bullet is from other bullets (avoids redundancy)
3. **Cover letter reference** — whether the cover letter mentions this bullet's content

Each round removes the single lowest-scoring bullet, re-renders the LaTeX,
re-compiles, and checks the page count again. This continues until the CV
fits within the limit or all bullets have been evaluated.

100% deterministic — no LLM calls. Operates at the DATA level
(TailoredExperienceEntry bullets), not on raw LaTeX.

NOTE: If even after removing ALL bullets the CV still exceeds the limit,
the function will raise LatexCompileError (this should never happen with
reasonable CV content).
"""

from __future__ import annotations


import re
from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable

from app.schemas.apply import TailoredExperienceEntry
from app.schemas.cv_cutter import CVTrimResult, ScoredBullet

if TYPE_CHECKING:
    from app.db.models import JobPosting
from app.core.logging import get_logger, bind_context

logger = get_logger(__name__)

# Scoring weights (must sum to 1.0)
WEIGHT_RELEVANCE = 0.5
WEIGHT_UNIQUENESS = 0.3
WEIGHT_COVER_REFERENCE = 0.2

# Maximum trimming iterations to prevent infinite loops
MAX_TRIM_ITERATIONS = 30

# Minimum bullets per entry to keep (never remove below this)
MIN_BULLETS_PER_ENTRY = 1

# Bullet text excerpt length for cover letter reference detection
COVER_REFERENCE_EXCERPT_LENGTH = 40


# ── Bullet extraction ──────────────────────────────────────────────


def _extract_all_bullets(
    experience: list[TailoredExperienceEntry],
) -> list[ScoredBullet]:
    """Flatten the experience entries into a list of ScoredBullet.

    Each bullet gets an entry_index and bullet_index that can be used
    to locate and remove it from the source data structure.
    """
    bullets: list[ScoredBullet] = []
    for exp_idx, entry in enumerate(experience):
        for bul_idx, bullet_text in enumerate(entry.bullets):
            bullets.append(
                ScoredBullet(
                    entry_index=exp_idx,
                    bullet_index=bul_idx,
                    text=bullet_text,
                )
            )
    return bullets


def _bullets_from_experience(
    experience: list[TailoredExperienceEntry],
) -> list[tuple[int, int, str]]:
    """Extract (entry_idx, bullet_idx, text) tuples from experience entries."""
    result: list[tuple[int, int, str]] = []
    for exp_idx, entry in enumerate(experience):
        for bul_idx, text in enumerate(entry.bullets):
            result.append((exp_idx, bul_idx, text))
    return result


# ── Scoring helpers ────────────────────────────────────────────────


def _compute_relevance_score(bullet_text: str, job_posting: JobPosting) -> float:
    """Score how relevant a bullet is to the job posting.

    Uses keyword overlap between the bullet text and the job posting's
    description + requirements. Keywords are lowercased and extracted
    as meaningful terms (3+ chars, excluding stop words).

    Returns:
        float between 0.0 (no overlap) and 1.0 (perfect overlap).
    """
    # Extract keywords from job posting
    job_keywords = _extract_job_keywords(job_posting)
    if not job_keywords:
        return 0.5  # Neutral score if no keywords available

    # Lowercase bullet text
    bullet_lower = bullet_text.lower()

    # Count matching keywords
    matching = sum(1 for kw in job_keywords if kw in bullet_lower)
    if matching == 0:
        return 0.0

    # Score is the fraction of job keywords found in this bullet
    # Cap at 1.0
    return min(1.0, matching / max(len(job_keywords) * 0.3, 1))
    # A bullet that contains 30%+ of all job keywords gets max score


def _extract_job_keywords(job_posting: JobPosting) -> set[str]:
    """Extract meaningful keywords from a job posting.

    Combines requirements and description, lowercases, removes stop words
    and short terms. Used inside relevance scoring.

    Returns:
        Set of normalized keyword strings.
    """
    stop_words = {
        "the", "and", "for", "with", "this", "that", "from", "have",
        "will", "your", "what", "about", "which", "their", "would",
        "could", "should", "been", "were", "also", "than", "into",
        "over", "such", "only", "other", "more", "very", "just",
        "our", "its", "has", "had", "but", "not", "are", "all",
    }

    keywords: set[str] = set()

    if job_posting.requirements:
        for req in job_posting.requirements:
            words = re.findall(r"\b[a-zA-Z]{3,}\b", req.lower())
            keywords.update(w for w in words if w not in stop_words)

    if job_posting.description:
        desc_lower = job_posting.description.lower()
        # Tech terms (capitalized phrases)
        tech_terms = re.findall(
            r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b",
            job_posting.description,
        )
        keywords.update(t.lower() for t in tech_terms)

        # Individual meaningful words
        words = re.findall(r"\b[a-zA-Z]{3,}\b", desc_lower)
        keywords.update(w for w in words if w not in stop_words)

    return keywords


def _compute_uniqueness_score(
    bullet_text: str,
    all_bullets: list[tuple[int, int, str]],
    own_idx: tuple[int, int],
) -> float:
    """Score how unique this bullet is compared to others.

    Uses word-level Jaccard similarity: if this bullet shares many words
    with another bullet, its uniqueness is lower (we don't need both).

    Returns:
        float between 0.0 (highly redundant) and 1.0 (completely unique).
    """
    words_self = set(re.findall(r"\b[a-zA-Z]{3,}\b", bullet_text.lower()))

    if not words_self:
        return 0.5  # Neutral for very short bullets

    max_overlap = 0.0
    for other_entry_idx, other_bul_idx, other_text in all_bullets:
        if (other_entry_idx, other_bul_idx) == own_idx:
            continue

        words_other = set(re.findall(r"\b[a-zA-Z]{3,}\b", other_text.lower()))
        if not words_other:
            continue

        intersection = len(words_self & words_other)
        union = len(words_self | words_other)
        similarity = intersection / union if union > 0 else 0.0
        max_overlap = max(max_overlap, similarity)

    # Uniqueness = 1 - max_similarity (if similar to any other bullet, low uniqueness)
    return 1.0 - max_overlap


def _compute_cover_reference_score(
    bullet_text: str,
    cover_letter_latex: str,
) -> float:
    """Score whether the cover letter references this bullet's content.

    Uses the first N characters of the bullet to check if the cover letter
    mentions similar content. This is a heuristic: if the first ~40 chars
    appear in the cover letter, the bullet is likely referenced.

    Returns:
        1.0 if referenced, 0.0 if not.
    """
    if not cover_letter_latex:
        return 0.0

    # Use the first meaningful excerpt
    excerpt = bullet_text[:COVER_REFERENCE_EXCERPT_LENGTH].strip()
    if len(excerpt) < 10:
        return 0.0

    # Check case-insensitive
    return 1.0 if excerpt.lower() in cover_letter_latex.lower() else 0.0


# ── Bullet removal ─────────────────────────────────────────────────


def _remove_bullet(
    experience: list[TailoredExperienceEntry],
    entry_index: int,
    bullet_index: int,
) -> list[TailoredExperienceEntry]:
    """Remove a specific bullet from the experience entries.

    Creates a deep copy to avoid mutating the original. If removing
    the bullet would leave the entry with fewer than MIN_BULLETS_PER_ENTRY
    bullets, the bullet is NOT removed (returns original unchanged).

    Returns:
        Modified (or original, if protected) list of entries.
    """
    if entry_index >= len(experience):
        return experience

    entry = experience[entry_index]
    if len(entry.bullets) <= MIN_BULLETS_PER_ENTRY:
        # Protected — can't remove the last bullet from an entry
        return experience

    # Deep copy to avoid mutation
    result = deepcopy(experience)
    entry_copy = result[entry_index]

    if bullet_index < len(entry_copy.bullets):
        del entry_copy.bullets[bullet_index]

    return result


# ── Main entry point ────────────────────────────────────────────────


# ── Dict-based helpers (for JSON/Typst path) ─────────────────────────


def _extract_all_bullets_from_dicts(
    experience: list[dict],
) -> list[ScoredBullet]:
    """Flatten dict experience entries into ScoredBullet list."""
    bullets: list[ScoredBullet] = []
    for exp_idx, entry in enumerate(experience):
        for bul_idx, bullet_text in enumerate(entry.get("bullets", [])):
            bullets.append(
                ScoredBullet(
                    entry_index=exp_idx,
                    bullet_index=bul_idx,
                    text=bullet_text,
                )
            )
    return bullets


def _bullets_from_experience_dicts(
    experience: list[dict],
) -> list[tuple[int, int, str]]:
    """Extract (entry_idx, bullet_idx, text) from dict experience."""
    result: list[tuple[int, int, str]] = []
    for exp_idx, entry in enumerate(experience):
        for bul_idx, text in enumerate(entry.get("bullets", [])):
            result.append((exp_idx, bul_idx, text))
    return result


def _remove_bullet_from_dicts(
    experience: list[dict],
    entry_index: int,
    bullet_index: int,
) -> list[dict]:
    """Remove a bullet from a dict-based experience list (deep-copied)."""
    if entry_index >= len(experience):
        return experience

    entry = experience[entry_index]
    if len(entry.get("bullets", [])) <= MIN_BULLETS_PER_ENTRY:
        return experience

    result = deepcopy(experience)
    del result[entry_index]["bullets"][bullet_index]
    return result


async def trim_cv_experience(
    experience: list[dict],
    job_posting: JobPosting,
    compile_fn: Callable[[list[dict]], Awaitable[tuple[Path, int]]],
    cover_text: str = "",
    max_pages: int = 2,
) -> tuple[list[dict], CVTrimResult]:
    """Trim dict-based CV experience to fit within a page limit.

    Same scoring logic as ``trim_cv_to_page_limit`` but operates on
    ``list[dict]`` (the JSON path's experience entries) instead of
    ``list[TailoredExperienceEntry]``.

    Args:
        experience: CV experience entries as dicts with ``bullets`` key.
        job_posting: The JobPosting (for keyword extraction).
        compile_fn: Async callable that takes the current experience list,
            renders + compiles the full CV, and returns ``(pdf_path, pages)``.
        cover_text: Cover letter text for reference-score detection.
        max_pages: Target page count (default 2).

    Returns:
        Tuple of (trimmed_experience, CVTrimResult).
    """
    with bind_context(stage="cv_cutter"):
        bullets_before = sum(len(e.get("bullets", [])) for e in experience)
        entries_before = len(experience)

        # First compile to check current page count
        _, current_pages = await compile_fn(experience)

        if current_pages <= max_pages:
            return experience, CVTrimResult(
                entries_before=entries_before,
                bullets_before=bullets_before,
                bullets_removed=0,
                pages_achieved=current_pages,
                removed_bullet_texts=[],
                remaining_bullets_per_entry=[
                    len(e.get("bullets", [])) for e in experience
                ],
                was_trimmed=False,
            )

        trimmed_experience = deepcopy(experience)
        removed_texts: list[str] = []

        for iteration in range(MAX_TRIM_ITERATIONS):
            current_bullets = _extract_all_bullets_from_dicts(trimmed_experience)
            if not current_bullets:
                break

            all_current_tuples = _bullets_from_experience_dicts(trimmed_experience)

            scored: list[ScoredBullet] = []
            for sb in current_bullets:
                sb.relevance_score = _compute_relevance_score(sb.text, job_posting)
                sb.uniqueness_score = _compute_uniqueness_score(
                    sb.text, all_current_tuples, (sb.entry_index, sb.bullet_index)
                )
                sb.cover_reference_score = _compute_cover_reference_score(
                    sb.text, cover_text
                )
                sb.combined_score = (
                    sb.relevance_score * WEIGHT_RELEVANCE
                    + sb.uniqueness_score * WEIGHT_UNIQUENESS
                    + sb.cover_reference_score * WEIGHT_COVER_REFERENCE
                )
                scored.append(sb)

            scored.sort(key=lambda s: s.combined_score)

            lowest = None
            for sb in scored:
                entry = trimmed_experience[sb.entry_index]
                if len(entry.get("bullets", [])) > MIN_BULLETS_PER_ENTRY:
                    lowest = sb
                    break

            if lowest is None:
                break

            removed_texts.append(lowest.text)
            trimmed_experience = _remove_bullet_from_dicts(
                trimmed_experience, lowest.entry_index, lowest.bullet_index
            )

            _, current_pages = await compile_fn(trimmed_experience)

            if current_pages <= max_pages:
                logger.info(
                    f"CV trimmed to {max_pages} page(s) in {iteration + 1} iteration(s). "
                    f"Removed {len(removed_texts)} bullet(s)."
                )
                return trimmed_experience, CVTrimResult(
                    entries_before=entries_before,
                    bullets_before=bullets_before,
                    bullets_removed=len(removed_texts),
                    pages_achieved=current_pages,
                    removed_bullet_texts=removed_texts,
                    remaining_bullets_per_entry=[
                        len(e.get("bullets", [])) for e in trimmed_experience
                    ],
                    was_trimmed=True,
                )

        final_bullets = sum(
            len(e.get("bullets", [])) for e in trimmed_experience
        )
        logger.warning(
            f"CV trim exhausted: removed {len(removed_texts)} bullet(s) "
            f"({bullets_before} → {final_bullets}), "
            f"but still at {current_pages} pages (max {max_pages})."
        )
        return trimmed_experience, CVTrimResult(
            entries_before=entries_before,
            bullets_before=bullets_before,
            bullets_removed=len(removed_texts),
            pages_achieved=current_pages,
            removed_bullet_texts=removed_texts,
            remaining_bullets_per_entry=[
                len(e.get("bullets", [])) for e in trimmed_experience
            ],
            was_trimmed=len(removed_texts) > 0,
        )


async def trim_cv_to_page_limit(
    experience: list[TailoredExperienceEntry],
    job_posting: JobPosting,
    cover_letter_latex: str,
    render_fn: Callable[
        [list[TailoredExperienceEntry]], str
    ],
    compile_fn: Callable[[str, Path, str], tuple[Path, int]],
    output_dir: Path,
    job_name: str,
    max_pages: int = 2,
) -> tuple[list[TailoredExperienceEntry], CVTrimResult]:
    """Trim a CV's experience section to fit within a page limit.

    Iteratively removes the lowest-scoring bullets, re-renders the LaTeX,
    and re-compiles until the page count is within ``max_pages``.

    Args:
        experience: The current tailored experience entries with bullets.
        job_posting: The JobPosting (for keyword extraction).
        cover_letter_latex: The rendered cover letter LaTeX (for reference check).
        render_fn: Callable that takes the experience list and returns LaTeX string.
        compile_fn: Callable that takes (tex_content, output_dir, job_name) and
            returns (pdf_path, page_count). Must NOT raise on wrong page count —
            instead should return the actual page count.
        output_dir: Directory for compilation artifacts.
        job_name: Base name for compilation (e.g. "cv_Company_Role").
        max_pages: Target page count (default 2).

    Returns:
        Tuple of (trimmed_experience, CVTrimResult with details).

    Note:
        The ``compile_fn`` must return ``(Path, int)`` with the actual page
        count instead of raising on wrong page count. If your existing
        compile function raises on mismatch, wrap it.
    """
    with bind_context(stage="cv_cutter"):
        bullets_before = sum(len(e.bullets) for e in experience)
        entries_before = len(experience)
        all_bullet_tuples = _bullets_from_experience(experience)

        # First compile to check current page count
        current_tex = render_fn(experience)
        _, current_pages = await compile_fn(current_tex, output_dir, job_name)

        if current_pages <= max_pages:
            # Already within limit — no trimming needed
            return experience, CVTrimResult(
                entries_before=entries_before,
                bullets_before=bullets_before,
                bullets_removed=0,
                pages_achieved=current_pages,
                removed_bullet_texts=[],
                remaining_bullets_per_entry=[len(e.bullets) for e in experience],
                was_trimmed=False,
            )

        # Trim loop
        trimmed_experience = deepcopy(experience)
        removed_texts: list[str] = []

        for iteration in range(MAX_TRIM_ITERATIONS):
            # Re-build bullet list from current state
            current_bullets = _extract_all_bullets(trimmed_experience)
            if not current_bullets:
                break  # Nothing left to remove

            # Score each bullet
            all_current_tuples = _bullets_from_experience(trimmed_experience)

            scored: list[ScoredBullet] = []
            for sb in current_bullets:
                sb.relevance_score = _compute_relevance_score(sb.text, job_posting)
                sb.uniqueness_score = _compute_uniqueness_score(
                    sb.text, all_current_tuples, (sb.entry_index, sb.bullet_index)
                )
                sb.cover_reference_score = _compute_cover_reference_score(
                    sb.text, cover_letter_latex
                )
                sb.combined_score = (
                    sb.relevance_score * WEIGHT_RELEVANCE
                    + sb.uniqueness_score * WEIGHT_UNIQUENESS
                    + sb.cover_reference_score * WEIGHT_COVER_REFERENCE
                )
                scored.append(sb)

            # Find the lowest-scoring removable bullet
            # Sort by combined_score ascending, filter by removability
            scored.sort(key=lambda s: s.combined_score)

            # Skip bullets that are protected (last bullet in an entry)
            lowest = None
            for sb in scored:
                entry = trimmed_experience[sb.entry_index]
                if len(entry.bullets) > MIN_BULLETS_PER_ENTRY:
                    lowest = sb
                    break

            if lowest is None:
                # All entries at minimum — can't remove more
                break

            # Remove the lowest-scoring bullet
            removed_texts.append(lowest.text)
            trimmed_experience = _remove_bullet(
                trimmed_experience, lowest.entry_index, lowest.bullet_index
            )

            # Re-render and re-compile
            current_tex = render_fn(trimmed_experience)
            _, current_pages = await compile_fn(current_tex, output_dir, job_name)

            if current_pages <= max_pages:
                # Success! Within limit now
                logger.info(
                    f"CV trimmed to {max_pages} page(s) in {iteration + 1} iteration(s). "
                    f"Removed {len(removed_texts)} bullet(s)."
                )
                return trimmed_experience, CVTrimResult(
                    entries_before=entries_before,
                    bullets_before=bullets_before,
                    bullets_removed=len(removed_texts),
                    pages_achieved=current_pages,
                    removed_bullet_texts=removed_texts,
                    remaining_bullets_per_entry=[
                        len(e.bullets) for e in trimmed_experience
                    ],
                    was_trimmed=True,
                )

        # If we exhausted iterations or bullets and still don't fit, log warning
        # but return what we have. The caller will handle page count verification.
        final_bullets = sum(len(e.bullets) for e in trimmed_experience)
        logger.warning(
            f"CV trim exhausted: removed {len(removed_texts)} bullet(s) "
            f"({bullets_before} → {final_bullets}), "
            f"but still at {current_pages} pages (max {max_pages})."
        )
        return trimmed_experience, CVTrimResult(
            entries_before=entries_before,
            bullets_before=bullets_before,
            bullets_removed=len(removed_texts),
            pages_achieved=current_pages,
            removed_bullet_texts=removed_texts,
            remaining_bullets_per_entry=[len(e.bullets) for e in trimmed_experience],
            was_trimmed=len(removed_texts) > 0,
        )
