"""Comparison harness: LaTeX vs Typst CV generation paths.

Produces a structured side-by-side report for human review of the
two pipelines.  Run with a live provider key to execute both paths:

    python -m app.scripts.compare_paths <user_id> <job_id> <eval_id>

Or load a cached comparison from a previous run:

    python -m app.scripts.compare_paths --load <report.json>

All comparison logic (scoring heuristics, report formatting) is
deterministic and testable without an LLM.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any


# ═══════════════════════════════════════════════════════════════════════
# Data structures for the comparison report
# ═══════════════════════════════════════════════════════════════════════


@dataclass
class PathResult:
    """Output and metrics from one generation path."""

    path_name: str  # "LaTeX" or "Typst"
    pipeline_stage: str = ""
    cv_pages: int = 0
    cover_pages: int = 0
    cv_compiled: bool = False
    cover_compiled: bool = False

    # ATS check results
    ats_pass: bool | None = None
    ats_keyword_coverage: float = 0.0
    ats_missing_keywords: list[str] = field(default_factory=list)
    ats_has_cid: bool = False
    ats_has_email: bool = False
    ats_has_name: bool = False

    # Content samples
    cv_bullets: list[str] = field(default_factory=list)
    cover_letter_samples: list[str] = field(default_factory=list)
    profile_statement: str = ""

    # Heuristic scores (deterministic)
    avg_bullet_length: float = 0.0
    xyz_formula_count: int = 0
    total_bullets: int = 0
    generic_phrases: list[str] = field(default_factory=list)
    keyword_matches: dict[str, bool] = field(default_factory=dict)

    # Error / fallback info
    error: str | None = None


@dataclass
class ComparisonReport:
    """Full side-by-side comparison report."""

    generated_at: str = field(default_factory=lambda: datetime.now().isoformat())

    # Input metadata
    profile_name: str = ""
    job_title: str = ""
    job_company: str = ""
    job_language: str = ""

    # Results from each path
    latex: PathResult = field(default_factory=lambda: PathResult(path_name="LaTeX"))
    typst: PathResult = field(default_factory=lambda: PathResult(path_name="Typst"))

    # Summary scores
    latex_wins: list[str] = field(default_factory=list)
    typst_wins: list[str] = field(default_factory=list)
    ties: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return json.loads(json.dumps(asdict(self), default=str))

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, ensure_ascii=False)


# ═══════════════════════════════════════════════════════════════════════
# Heuristic scoring functions (deterministic, no LLM)
# ═══════════════════════════════════════════════════════════════════════

XYZ_PATTERN = re.compile(
    r"\b(by|through|using|via|resulting in|leading to|achieving|"
    r"delivering|improving|reducing|increasing|enabling)\b",
    re.IGNORECASE,
)

GENERIC_PHRASES = [
    "responsible for",
    "tasked with",
    "duties included",
    "duties include",
    "worked on",
    "involved in",
    "participated in",
    "helped with",
    "was part of",
    "role included",
    "handled",
    "managed",
    "was responsible",
]


def score_xyz_usage(text: str) -> int:
    """Count X-Y-Z formula markers in a text string."""
    return len(XYZ_PATTERN.findall(text))


def detect_generic_phrases(text: str) -> list[str]:
    """Return generic/responsibility-based phrases found in text."""
    lower = text.lower()
    return [p for p in GENERIC_PHRASES if p in lower]


def score_keyword_coverage(
    bullet_texts: list[str],
    target_keywords: set[str],
) -> dict[str, bool]:
    """Map each keyword to whether it appears in any bullet."""
    combined = " ".join(bullet_texts).lower()
    return {kw: kw.lower() in combined for kw in sorted(target_keywords)}


def build_path_result_from_application(
    app: Any,
    path_name: str,
    job_keywords: set[str] | None = None,
) -> PathResult:
    """Build a PathResult from an Application record (dict or object).

    Works with both DB model instances and plain dicts (for cached data).
    """
    r = PathResult(path_name=path_name)

    if app is None:
        r.error = "No application data"
        return r

    if isinstance(app, dict):
        r.pipeline_stage = app.get("pipeline_stage", "")
        r.cv_pages = app.get("cv_pages", 0) or 0
        r.cover_pages = app.get("cover_letter_pages", 0) or 0
        r.cv_compiled = app.get("cv_compiled", False)
        r.cover_compiled = app.get("cover_letter_compiled", False)
        r.ats_pass = app.get("ats_pass")
        r.ats_keyword_coverage = app.get("ats_score", 0.0) or 0.0
        r.ats_missing_keywords = app.get("ats_missing_keywords") or []
        r.error = app.get("error")
        experience = app.get("tailored_experience") or []
        r.cv_bullets = _extract_bullets_from_experience(experience)
        r.cover_letter_samples = _extract_cover_samples(app)
        r.profile_statement = _extract_profile_statement(app)
    else:
        r.pipeline_stage = getattr(app, "pipeline_stage", "")
        r.cv_pages = getattr(app, "cv_pages", 0) or 0
        r.cover_pages = getattr(app, "cover_letter_pages", 0) or 0
        r.cv_compiled = getattr(app, "cv_compiled", False)
        r.cover_compiled = getattr(app, "cover_letter_compiled", False)
        r.ats_pass = getattr(app, "ats_pass", None)
        r.ats_keyword_coverage = getattr(app, "ats_score", 0.0) or 0.0
        r.ats_missing_keywords = getattr(app, "ats_missing_keywords") or []
        r.error = getattr(app, "error", None)

        tex_content = getattr(app, "draft_cv_tex", "") or ""
        if tex_content:
            r.cv_bullets = _extract_bullets_from_latex(tex_content)
        experience = getattr(app, "tailored_experience") or []
        if not r.cv_bullets and experience:
            r.cv_bullets = _extract_bullets_from_experience(experience)

        cl_tex = getattr(app, "draft_cover_letter_tex", "") or ""
        if cl_tex:
            r.cover_letter_samples = _extract_cover_samples_from_latex(cl_tex)
        r.profile_statement = (
            getattr(app, "draft_profile_statement", None)
            or ""
        )

    if r.cv_bullets:
        all_text = " ".join(r.cv_bullets)
        r.total_bullets = len(r.cv_bullets)
        r.avg_bullet_length = sum(len(b) for b in r.cv_bullets) / len(r.cv_bullets)
        r.xyz_formula_count = score_xyz_usage(all_text)
        r.generic_phrases = detect_generic_phrases(all_text)
        if job_keywords:
            r.keyword_matches = score_keyword_coverage(r.cv_bullets, job_keywords)

    return r


def _extract_bullets_from_experience(experience: list[Any]) -> list[str]:
    """Extract bullet texts from tailored_experience (dicts or objects)."""
    bullets: list[str] = []
    for entry in experience:
        if isinstance(entry, dict):
            bullets.extend(entry.get("bullets") or [])
        else:
            bullets.extend(getattr(entry, "bullets", []) or [])
    return bullets


def _extract_bullets_from_latex(tex: str) -> list[str]:
    """Extract \\item content from LaTeX."""
    items = re.findall(r"\\item\s+(.+?)(?=\\item|\n\n|$)", tex, re.DOTALL)
    return [i.strip() for i in items if i.strip()]


def _extract_cover_samples(app: dict | Any) -> list[str]:
    """Extract a few sample lines from cover letter."""
    if isinstance(app, dict):
        tex = app.get("draft_cover_letter_tex", "") or ""
        cv_output = app.get("cv_output")
        if isinstance(cv_output, dict):
            cl = cv_output.get("cover_letter") or cv_output.get("cv", {}).get("cover_letter")
            if cl and isinstance(cl, dict):
                paras = cl.get("body_paragraphs", [])
                return paras[:2] if isinstance(paras, list) else [str(paras)]
    else:
        tex = getattr(app, "draft_cover_letter_tex", "") or ""
    return _extract_cover_samples_from_latex(tex)


def _extract_cover_samples_from_latex(tex: str) -> list[str]:
    """Extract first few paragraph texts from cover letter LaTeX."""
    if not tex:
        return []
    paras = re.findall(r"\\(?:textbf{)?(.*?)(?:}|$)", tex)
    clean = [p.strip() for p in paras if len(p.strip()) > 20]
    return clean[:3]


def _extract_profile_statement(app: dict | Any) -> str:
    """Extract profile statement from application data."""
    if isinstance(app, dict):
        tex = app.get("draft_cv_tex", "") or ""
        if tex:
            m = re.search(r"profile[a-z\s]*statement[^}]*}\s*{(.+?)}", tex, re.IGNORECASE)
            if m:
                return m.group(1).strip()
        return app.get("profile_statement", "") or ""
    tex = getattr(app, "draft_cv_tex", "") or ""
    m = re.search(r"profile[a-z\s]*statement[^}]*}\s*{(.+?)}", tex, re.IGNORECASE)
    return m.group(1).strip() if m else ""


# ═══════════════════════════════════════════════════════════════════════
# Comparison logic
# ═══════════════════════════════════════════════════════════════════════


def _cmp(left, right, higher_is_better: bool = True) -> int:
    """Compare two values.  Returns -1 (left better), 0 (tie), 1 (right better)."""
    try:
        lv, rv = float(left or 0), float(right or 0)
    except (TypeError, ValueError):
        return 0
    if lv == rv:
        return 0
    if higher_is_better:
        return -1 if lv > rv else 1
    return -1 if lv < rv else 1


def compute_comparison(report: ComparisonReport) -> ComparisonReport:
    """Fill in the summary scores (latex_wins, typst_wins, ties)."""
    L, T = report.latex, report.typst

    # Pipeline stage (deeper = better)
    stage_order = {"draft": 0, "reviewed": 1, "revised": 2, "compiled": 3, "verified": 4}
    if _cmp(stage_order.get(L.pipeline_stage, -1), stage_order.get(T.pipeline_stage, -1)) == 0:
        report.ties.append("Pipeline stage reached")
    elif _cmp(stage_order.get(L.pipeline_stage, -1), stage_order.get(T.pipeline_stage, -1)) < 0:
        report.latex_wins.append("Pipeline stage (LaTeX further)")
    else:
        report.typst_wins.append("Pipeline stage (Typst further)")

    # CV compiled
    if L.cv_compiled and not T.cv_compiled:
        report.latex_wins.append("CV successfully compiled")
    elif T.cv_compiled and not L.cv_compiled:
        report.typst_wins.append("CV successfully compiled")
    elif L.cv_compiled and T.cv_compiled:
        report.ties.append("Both CVs compiled")

    # Page count (closer to 2 is better — target is 2 pages)
    target = 2
    l_dist = abs((L.cv_pages or 0) - target)
    t_dist = abs((T.cv_pages or 0) - target)
    if l_dist < t_dist:
        report.latex_wins.append("CV page count (closer to target 2)")
    elif t_dist < l_dist:
        report.typst_wins.append("CV page count (closer to target 2)")
    else:
        report.ties.append(f"CV pages (both at {L.cv_pages})")

    # ATS keyword coverage (higher = better)
    cmp_kw = _cmp(L.ats_keyword_coverage, T.ats_keyword_coverage)
    if cmp_kw < 0:
        report.latex_wins.append(f"ATS keyword coverage ({L.ats_keyword_coverage:.0%} vs {T.ats_keyword_coverage:.0%})")
    elif cmp_kw > 0:
        report.typst_wins.append(f"ATS keyword coverage ({T.ats_keyword_coverage:.0%} vs {L.ats_keyword_coverage:.0%})")
    else:
        report.ties.append(f"ATS keyword coverage (both {L.ats_keyword_coverage:.0%})")

    # ATS pass/fail
    if L.ats_pass and not T.ats_pass:
        report.latex_wins.append("ATS parseability check passed")
    elif T.ats_pass and not L.ats_pass:
        report.typst_wins.append("ATS parseability check passed")
    elif L.ats_pass and T.ats_pass:
        report.ties.append("Both passed ATS parseability check")

    # X-Y-Z formula usage
    cmp_xyz = _cmp(L.xyz_formula_count, T.xyz_formula_count)
    if cmp_xyz < 0:
        report.latex_wins.append(f"X-Y-Z formula markers ({L.xyz_formula_count} vs {T.xyz_formula_count})")
    elif cmp_xyz > 0:
        report.typst_wins.append(f"X-Y-Z formula markers ({T.xyz_formula_count} vs {L.xyz_formula_count})")
    else:
        report.ties.append(f"X-Y-Z formula markers (both {L.xyz_formula_count})")

    # Generic phrases (fewer = better)
    cmp_gen = _cmp(len(L.generic_phrases), len(T.generic_phrases), higher_is_better=False)
    if cmp_gen < 0:
        report.latex_wins.append("Fewer generic/responsibility phrases")
    elif cmp_gen > 0:
        report.typst_wins.append("Fewer generic/responsibility phrases")
    else:
        report.ties.append("Generic phrase count (equal)")

    # Total bullet count (more is better — shows more detail)
    cmp_bul = _cmp(L.total_bullets, T.total_bullets)
    if cmp_bul < 0:
        report.latex_wins.append(f"Bullet count ({L.total_bullets} vs {T.total_bullets})")
    elif cmp_bul > 0:
        report.typst_wins.append(f"Bullet count ({T.total_bullets} vs {L.total_bullets})")
    else:
        report.ties.append(f"Bullet count (both {L.total_bullets})")

    return report


# ═══════════════════════════════════════════════════════════════════════
# Report formatting
# ═══════════════════════════════════════════════════════════════════════


def format_report(report: ComparisonReport) -> str:
    """Format the comparison report as a human-readable markdown string."""
    L, T = report.latex, report.typst
    lines: list[str] = []
    lines.append("# CV Pipeline Comparison: LaTeX vs Typst")
    lines.append("")
    lines.append(f"- **Generated**: {report.generated_at}")
    lines.append(f"- **Profile**: {report.profile_name}")
    lines.append(f"- **Job**: {report.job_title} @ {report.job_company}")
    lines.append(f"- **Language**: {report.job_language}")
    lines.append("")

    # ── Quick verdict ──────────────────────────────────────────
    lines.append("## Quick Verdict")
    lines.append("")
    total_l = len(report.latex_wins)
    total_t = len(report.typst_wins)
    total_tie = len(report.ties)
    lines.append(
        f"**LaTeX wins**: {total_l}  |  "
        f"**Typst wins**: {total_t}  |  "
        f"**Ties**: {total_tie}"
    )
    lines.append("")

    if report.latex_wins:
        lines.append("**LaTeX advantages:**")
        for w in report.latex_wins:
            lines.append(f"  - {w}")
    if report.typst_wins:
        lines.append("**Typst advantages:**")
        for w in report.typst_wins:
            lines.append(f"  - {w}")
    lines.append("")

    # ── Side-by-side metrics ───────────────────────────────────
    lines.append("## Side-by-Side Metrics")
    lines.append("")
    lines.append("| Metric | LaTeX | Typst | Winner |")
    lines.append("|--------|-------|-------|--------|")

    metrics = [
        ("Pipeline stage", L.pipeline_stage, T.pipeline_stage),
        ("CV compiled", _yn(L.cv_compiled), _yn(T.cv_compiled)),
        ("CV pages", str(L.cv_pages), str(T.cv_pages)),
        ("Cover compiled", _yn(L.cover_compiled), _yn(T.cover_compiled)),
        ("Cover pages", str(L.cover_pages), str(T.cover_pages)),
        ("ATS pass", _yn(L.ats_pass), _yn(T.ats_pass)),
        ("Keyword coverage", f"{L.ats_keyword_coverage:.0%}", f"{T.ats_keyword_coverage:.0%}"),
        ("CID markers", _yn(L.ats_has_cid), _yn(T.ats_has_cid)),
        ("Email found", _yn(L.ats_has_email), _yn(T.ats_has_email)),
        ("Name found", _yn(L.ats_has_name), _yn(T.ats_has_name)),
        ("Total bullets", str(L.total_bullets), str(T.total_bullets)),
        ("X-Y-Z markers", str(L.xyz_formula_count), str(T.xyz_formula_count)),
        ("Avg bullet length", f"{L.avg_bullet_length:.0f}", f"{T.avg_bullet_length:.0f}"),
        ("Generic phrases", str(len(L.generic_phrases)), str(len(T.generic_phrases))),
    ]

    for name, lv, tv in metrics:
        winner = _pick_winner(name, report)
        lines.append(f"| {name} | {lv} | {tv} | {winner} |")

    lines.append("")

    # ── Generic phrases detail ─────────────────────────────────
    if L.generic_phrases or T.generic_phrases:
        lines.append("## Generic/Responsibility-Based Phrases")
        lines.append("")
        if L.generic_phrases:
            lines.append(f"**LaTeX ({len(L.generic_phrases)}):**")
            for p in L.generic_phrases:
                lines.append(f"  - \"{p}\"")
        if T.generic_phrases:
            lines.append(f"**Typst ({len(T.generic_phrases)}):**")
            for p in T.generic_phrases:
                lines.append(f"  - \"{p}\"")
        lines.append("")

    # ── Missing keywords ───────────────────────────────────────
    all_missing = set(L.ats_missing_keywords) | set(T.ats_missing_keywords)
    if all_missing:
        lines.append("## Missing Keywords (ATS flagged)")
        lines.append("")
        lines.append("| Keyword | LaTeX | Typst |")
        lines.append("|---------|-------|-------|")
        for kw in sorted(all_missing):
            l_present = "✅" if L.keyword_matches.get(kw) else "❌"
            t_present = "✅" if T.keyword_matches.get(kw) else "❌"
            lines.append(f"| {kw} | {l_present} | {t_present} |")
        lines.append("")

    # ── CV Content (first few bullets) ──────────────────────────
    lines.append("## CV Content Samples")
    lines.append("")
    lines.append("### LaTeX")
    lines.append("")
    if L.profile_statement:
        lines.append(f"> **Profile**: {L.profile_statement[:200]}")
    lines.append("")
    for i, b in enumerate(L.cv_bullets[:6], 1):
        lines.append(f"{i}. {b[:150]}")
    if len(L.cv_bullets) > 6:
        lines.append(f"   *... and {len(L.cv_bullets) - 6} more*")
    lines.append("")
    lines.append("### Typst")
    lines.append("")
    if T.profile_statement:
        lines.append(f"> **Profile**: {T.profile_statement[:200]}")
    lines.append("")
    for i, b in enumerate(T.cv_bullets[:6], 1):
        lines.append(f"{i}. {b[:150]}")
    if len(T.cv_bullets) > 6:
        lines.append(f"   *... and {len(T.cv_bullets) - 6} more*")
    lines.append("")

    # ── Cover letter samples ────────────────────────────────────
    lines.append("## Cover Letter Samples")
    lines.append("")
    if L.cover_letter_samples:
        lines.append("### LaTeX")
        lines.append("")
        for s in L.cover_letter_samples[:3]:
            lines.append(f"> {s[:200]}")
    lines.append("")
    if T.cover_letter_samples:
        lines.append("### Typst")
        lines.append("")
        for s in T.cover_letter_samples[:3]:
            lines.append(f"> {s[:200]}")
    lines.append("")

    # ── Errors / warnings ───────────────────────────────────────
    if L.error or T.error:
        lines.append("## Errors & Warnings")
        lines.append("")
        if L.error:
            lines.append(f"- **LaTeX**: {L.error}")
        if T.error:
            lines.append(f"- **Typst**: {T.error}")
        lines.append("")

    # ── Notes ───────────────────────────────────────────────────
    lines.append("## Interpretation Notes")
    lines.append("")
    lines.append("- **X-Y-Z markers** count X-Y-Z formula indicators (by, through, using, etc.).")
    lines.append("  Higher is generally better — means bullets are accomplishment-oriented.")
    lines.append("- **Generic phrases** indicate responsibility-based bullets rather than")
    lines.append("  achievement-based bullets. Fewer is better.")
    lines.append("- **Keyword coverage** measures overlap between CV bullets and job posting keywords.")
    lines.append("- This report is a **human decision support tool**, not an automated pass/fail.")
    lines.append("  The final quality judgment requires reading both CVs.")

    return "\n".join(lines)


def _yn(val: Any) -> str:
    return "Yes" if val else "No"


def _pick_winner(metric_name: str, report: ComparisonReport) -> str:
    if metric_name in str(report.latex_wins):
        return "LaTeX"
    if metric_name in str(report.typst_wins):
        return "Typst"
    return "Tie"


# ═══════════════════════════════════════════════════════════════════════
# Main execution (requires live DB + LLM provider)
# ═══════════════════════════════════════════════════════════════════════


async def run_comparison(
    db_session: Any,
    user_id: str,
    job_posting_id: str,
    rank_evaluation_id: str,
    output_dir: str | Path = "comparison_output",
) -> ComparisonReport:
    """Run both LaTeX and Typst pipelines and produce a comparison report.

    Requires an active database session and LLM provider credentials.
    """
    from app.db.models import Application, CandidateProfile, JobPosting, RankEvaluation
    from app.services import apply
    from sqlalchemy import select

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Load input data
    job = (
        await db_session.execute(
            select(JobPosting).where(
                JobPosting.id == job_posting_id, JobPosting.user_id == user_id
            )
        )
    ).scalar_one_or_none()
    candidate = (
        await db_session.execute(
            select(CandidateProfile).where(CandidateProfile.user_id == user_id)
        )
    ).scalar_one_or_none()
    evaluation = (
        await db_session.execute(
            select(RankEvaluation).where(
                RankEvaluation.id == rank_evaluation_id,
                RankEvaluation.job_posting_id == job_posting_id,
            )
        )
    ).scalar_one_or_none()

    if not job or not candidate:
        raise ValueError("Job or candidate not found")

    report = ComparisonReport(
        profile_name=candidate.full_name or "Unknown",
        job_title=job.title,
        job_company=job.company or "",
        job_language=job.language or "en",
    )

    # ── LaTeX path ─────────────────────────────────────────────
    latex_result = PathResult(path_name="LaTeX")
    try:
        la = await apply.execute_apply(
            db=db_session,
            user_id=user_id,
            job_posting_id=job_posting_id,
            rank_evaluation_id=rank_evaluation_id,
            use_typst=False,
        )
        # Reload application to get persisted fields
        app_record = (
            await db_session.execute(
                select(Application).where(
                    Application.job_posting_id == job_posting_id,
                    Application.user_id == user_id,
                ).order_by(Application.created_at.desc())
            )
        ).scalar()
        job_keywords = _extract_job_keywords_from_posting(job)
        latex_result = build_path_result_from_application(
            app_record, "LaTeX", job_keywords
        )
    except Exception as e:
        latex_result.error = str(e)

    report.latex = latex_result

    # ── Typst path ─────────────────────────────────────────────
    typst_result = PathResult(path_name="Typst")
    try:
        ta = await apply.execute_apply(
            db=db_session,
            user_id=user_id,
            job_posting_id=job_posting_id,
            rank_evaluation_id=rank_evaluation_id,
            use_typst=True,
        )
        app_record = (
            await db_session.execute(
                select(Application).where(
                    Application.job_posting_id == job_posting_id,
                    Application.user_id == user_id,
                ).order_by(Application.created_at.desc())
            )
        ).scalar()
        job_keywords = _extract_job_keywords_from_posting(job)
        typst_result = build_path_result_from_application(
            app_record, "Typst", job_keywords
        )
    except Exception as e:
        typst_result.error = str(e)

    report.typst = typst_result

    # Compute comparison scores
    report = compute_comparison(report)

    # Save report
    report_path = output_path / f"comparison_{job_posting_id}.json"
    report_path.write_text(report.to_json(), encoding="utf-8")

    md_path = output_path / f"comparison_{job_posting_id}.md"
    md_path.write_text(format_report(report), encoding="utf-8")

    return report


def _extract_job_keywords_from_posting(job: Any) -> set[str]:
    """Extract keywords from a JobPosting."""
    keywords: set[str] = set()
    if isinstance(job, dict):
        reqs = job.get("requirements") or []
        desc = job.get("description") or ""
    else:
        reqs = job.requirements or []
        desc = job.description or ""
    for req in reqs:
        for word in re.findall(r"\b[a-zA-Z]{3,}\b", req):
            if word.lower() not in _STOP_WORDS:
                keywords.add(word)
    for word in re.findall(r"\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})*\b", desc):
        keywords.add(word.lower())
    return keywords


_STOP_WORDS = {
    "the", "and", "for", "with", "this", "that", "from", "have",
    "will", "your", "what", "about", "which", "their", "would",
    "could", "should", "been", "were", "also", "than", "into",
    "over", "such", "only", "other", "more", "very", "just",
    "our", "its", "has", "had", "but", "not", "are", "all",
}


# ═══════════════════════════════════════════════════════════════════════
# CLI entry point
# ═══════════════════════════════════════════════════════════════════════


def main():
    parser = argparse.ArgumentParser(
        description="Compare LaTeX vs Typst CV generation paths."
    )
    parser.add_argument("user_id", nargs="?", help="User ID")
    parser.add_argument("job_id", nargs="?", help="Job posting ID")
    parser.add_argument("eval_id", nargs="?", help="Rank evaluation ID")
    parser.add_argument(
        "--output", "-o", default="comparison_output",
        help="Output directory for report files (default: comparison_output)",
    )
    parser.add_argument(
        "--load", "-l",
        help="Load cached report from JSON file and print markdown summary",
    )
    parser.add_argument(
        "--json", action="store_true",
        help="Output report as JSON instead of markdown",
    )

    args = parser.parse_args()

    if args.load:
        path = Path(args.load)
        if not path.exists():
            print(f"Error: report file not found: {path}", file=sys.stderr)
            sys.exit(1)
        data = json.loads(path.read_text(encoding="utf-8"))
        report = ComparisonReport(**data)
        # Recompute from dict data
        report.latex = PathResult(**report.latex) if isinstance(report.latex, dict) else report.latex
        report.typst = PathResult(**report.typst) if isinstance(report.typst, dict) else report.typst
        if args.json:
            print(report.to_json())
        else:
            print(format_report(report))
        return

    if not args.user_id or not args.job_id:
        parser.print_help()
        print("\nError: user_id and job_id are required when not using --load", file=sys.stderr)
        sys.exit(1)

    print(
        "This script requires an active database session and LLM provider credentials.\n"
        "Run with --load <report.json> to view a cached comparison, or provide\n"
        "user_id, job_id, and eval_id to execute both pipelines live.",
    )


if __name__ == "__main__":
    main()
