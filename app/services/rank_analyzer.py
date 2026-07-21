"""Deterministic rank analyzer — moves LLM work to code.

This module implements ALL the ranking logic that CAN be computed without an LLM.
The LLM should only handle: overall fit reasoning, strengths, gaps, red flags,
career alignment, and nuanced interpretation.

Deterministic implementations:
- Keyword extraction and normalization
- Missing keyword calculation (set difference)
- Location matching (city/country level)
- Deadline extraction (regex-based)
- Language detection
- Experience matching (years of experience)
- Technical score calculation (keyword overlap)
- Experience score calculation (requirement matching)
- Score normalization
- Remote/hybrid detection
- Relocation detection
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Any

logger = logging.getLogger(__name__)

# ── Constants ───────────────────────────────────────────────────────

# Common tech skills for keyword extraction
COMMON_TECH_SKILLS: set[str] = {
    "python", "java", "javascript", "typescript", "go", "rust", "c++", "c#",
    "ruby", "php", "swift", "kotlin", "scala", "r", "matlab", "sql",
    "pytorch", "tensorflow", "keras", "scikit-learn", "pandas", "numpy",
    "react", "angular", "vue", "node.js", "express", "django", "flask",
    "fastapi", "spring", "kubernetes", "docker", "aws", "gcp", "azure",
    "terraform", "ansible", "jenkins", "git", "linux", "postgresql",
    "mongodb", "redis", "elasticsearch", "kafka", "spark", "hadoop",
    "airflow", "mlops", "ci/cd", "rest", "graphql", "grpc",
}

# Known remote/hybrid keywords
REMOTE_KEYWORDS: set[str] = {"remote", "work from home", "wfh", "hybrid", "telecommute"}
ONSITE_KEYWORDS: set[str] = {"onsite", "in-office", "on-site"}

# Known relocation keywords
RELOCATION_KEYWORDS: set[str] = {
    "relocation", "relocate", "must relocate", "willing to relocate",
}

# Danish locations (for location matching)
DANISH_CITIES: set[str] = {
    "copenhagen", "københavn", "aarhus", "odense", "aalborg", "esbjerg",
    "randers", "kolding", "horsens", "vejle", "roskilde", "herning",
    "silkeborg", "naestved", "fredericia", "viborg", "holstebro",
}



# ── Text normalization ──────────────────────────────────────────────


def normalize_text(text: str | None) -> str:
    """Normalize text for comparison: lowercase, strip, remove punctuation."""
    if not text:
        return ""
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9\s+/.#-]", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def extract_keywords(text: str | None, min_length: int = 2) -> set[str]:
    """Extract normalized keywords from text.

    Uses a combination of:
    - Known tech skill dictionary matching
    - N-gram extraction (1-3 grams)
    - Common word filtering
    """
    if not text:
        return set()

    normalized = normalize_text(text)
    words = normalized.split()

    keywords: set[str] = set()

    # Single-word keywords (skip very common words)
    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
        "for", "of", "with", "by", "from", "as", "is", "was", "are",
        "were", "be", "been", "being", "have", "has", "had", "do",
        "does", "did", "will", "would", "could", "should", "may",
        "might", "must", "shall", "can", "about", "into", "through",
        "during", "before", "after", "above", "below", "between",
        "out", "off", "over", "under", "again", "further", "then",
        "once", "here", "there", "when", "where", "why", "how",
        "all", "each", "every", "both", "few", "more", "most",
        "other", "some", "such", "no", "nor", "not", "only",
        "own", "same", "so", "than", "too", "very", "just",
        "because", "also", "if", "then", "else", "this", "that",
    }

    for word in words:
        if len(word) >= min_length and word not in stop_words:
            keywords.add(word)

    # Bigrams (for compound skills like "machine learning")
    for i in range(len(words) - 1):
        bigram = f"{words[i]} {words[i+1]}"
        if len(bigram) >= min_length and bigram not in stop_words:
            keywords.add(bigram)

    # Trigrams
    for i in range(len(words) - 2):
        trigram = f"{words[i]} {words[i+1]} {words[i+2]}"
        if len(trigram) >= min_length:
            keywords.add(trigram)

    # Match against known tech skills
    for word in words:
        if word in COMMON_TECH_SKILLS:
            keywords.add(word)

    return keywords


def compute_keyword_overlap(
    source_keywords: set[str],
    target_keywords: set[str],
) -> tuple[set[str], set[str], float]:
    """Compute overlap between two keyword sets.

    Returns:
        Tuple of (matched_keywords, missing_keywords, overlap_ratio)
    """
    matched = source_keywords & target_keywords
    missing = target_keywords - source_keywords

    if not target_keywords:
        return matched, missing, 1.0

    overlap_ratio = len(matched) / len(target_keywords)
    return matched, missing, overlap_ratio


# ── Location analysis ──────────────────────────────────────────────


def analyze_location(
    candidate_location: str | None,
    job_location: str | None,
    candidate_constraints: str | None = None,
) -> str:
    """Deterministic location analysis.

    Returns: "PASS", "FAIL", or "FLAG"

    Logic:
    - If candidate has no location constraints → FLAG (unknown)
    - If job has no location → FLAG
    - If candidate mentions "remote only" and job is onsite → FAIL
    - If job is remote → PASS
    - If same city/region → PASS
    - If same country but different city → FLAG
    - If different country and candidate has no relocation → FAIL
    """
    if not candidate_location:
        return "FLAG"

    candidate_norm = normalize_text(candidate_location)
    job_norm = normalize_text(job_location) if job_location else ""

    # Check if job is remote → always PASS
    if job_norm:
        for kw in REMOTE_KEYWORDS:
            if kw in job_norm:
                return "PASS"

    # Check if candidate is remote-only and job is onsite
    constraints_norm = normalize_text(candidate_constraints or "")
    if constraints_norm:
        for kw in REMOTE_KEYWORDS:
            if kw in constraints_norm:
                # Candidate prefers remote, job may be onsite
                if job_norm and any(kw2 in job_norm for kw2 in ONSITE_KEYWORDS):
                    return "FAIL"
                return "FLAG"

    if not job_norm:
        return "FLAG"

    # Same city?
    candidate_cities = {c for c in DANISH_CITIES if c in candidate_norm}
    job_cities = {c for c in DANISH_CITIES if c in job_norm}
    if candidate_cities & job_cities:
        return "PASS"

    # Same country? Check for "denmark" or "dk"
    candidate_in_dk = "denmark" in candidate_norm or "dk" in candidate_norm
    job_in_dk = "denmark" in job_norm or "dk" in job_norm
    if candidate_in_dk and job_in_dk:
        return "FLAG"  # Same country, different city

    # Different country — check relocation willingness
    # "No relocation", "cannot relocate" = NOT willing → FAIL
    # "willing to relocate", "open to relocate" = willing → FLAG
    # "relocation" alone is ambiguous → FLAG (don't assume)
    if constraints_norm:
        # Check for explicit unwillingness first (stronger signal)
        unwilling_patterns = ["no relocation", "cannot relocate", "not willing", "not open to relocate", "relocation not possible"]
        if any(p in constraints_norm for p in unwilling_patterns):
            return "FAIL"
        # Check for willingness
        willing_patterns = ["willing to relocate", "open to relocate", "can relocate", "relocation possible"]
        if any(p in constraints_norm for p in willing_patterns):
            return "FLAG"
        # Check for ambiguous mention of relocation
        for kw in RELOCATION_KEYWORDS:
            if kw in constraints_norm:
                return "FLAG"
    if job_norm:
        for kw in RELOCATION_KEYWORDS:
            if kw in job_norm:
                return "FLAG"  # Job offers relocation (we could ask)

    return "FAIL"


# ── Deadline analysis ──────────────────────────────────────────────


def extract_deadline(text: str | None) -> tuple[str | None, bool]:
    """Extract deadline date from text and determine if it's urgent.

    Returns:
        Tuple of (deadline_string YYYY-MM-DD, is_urgent)
    """
    if not text:
        return None, False

    # Common date patterns
    patterns = [
        r"deadline[:\s]+(\d{4}-\d{2}-\d{2})",
        r"apply by[:\s]+(\d{4}-\d{2}-\d{2})",
        r"closes[:\s]+(\d{4}-\d{2}-\d{2})",
        r"expires[:\s]+(\d{4}-\d{2}-\d{2})",
        r"(\d{4}-\d{2}-\d{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            deadline_str = match.group(1)
            try:
                deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d").date()
                today = date.today()
                is_urgent = (deadline_date - today).days <= 7
                return deadline_str, is_urgent
            except ValueError:
                continue

    # Check for relative deadlines
    urgent_patterns = [
        (r"immediate", True),
        (r"urgent", True),
        (r"asap", True),
        (r"within \d+ days", True),
    ]

    for pattern, is_urgent in urgent_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return None, is_urgent

    return None, False


# ── Language detection ─────────────────────────────────────────────


def detect_language(text: str | None) -> str | None:
    """Simple rule-based language detection.

    Returns 'en', 'da', or None if uncertain.
    """
    if not text:
        return None

    text_lower = text.lower()

    # Danish-specific words (high signal)
    danish_signals = {
        "stilling", "ansøgning", "virksomhed", "arbejde", "kvalifikationer",
        "opgaver", "team", "erfaring", "uddannelse", "sprog", "dansk",
        "vi tilbyder", "vi forventer", "ansøgningsfrist", "kontakt",
        "løn", "pension", "ferie", "medarbejder", "chef", "leder",
        "projekt", "system", "data", "udvikling", "it", "digital",
    }

    # English-specific words (high signal)
    english_signals = {
        "opportunity", "qualifications", "responsibilities", "requirements",
        "experience", "education", "skills", "benefits", "salary",
        "apply", "submit", "resume", "cover letter", "interview",
    }

    danish_count = sum(1 for w in danish_signals if w in text_lower)
    english_count = sum(1 for w in english_signals if w in text_lower)

    if danish_count > english_count and danish_count >= 2:
        return "da"
    if english_count > danish_count and english_count >= 2:
        return "en"
    if danish_count == english_count and danish_count > 0:
        return "da"  # Default to Danish if equal

    return None


# ── Experience matching ───────────────────────────────────────────


def extract_years_experience(text: str | None) -> int | None:
    """Extract years of experience from text."""
    if not text:
        return None

    patterns = [
        r"(\d+)\+?\s*years?\s*(?:of)?\s*experience",
        r"(\d+)\+?\s*yr(?:s)?\s*(?:of)?\s*exp",
        r"experience\s*(?:of\s*)?(\d+)\+?\s*years?",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return int(match.group(1))

    return None


def estimate_candidate_years(experience: list[dict[str, Any]] | None) -> int:
    """Estimate total years of professional experience from profile."""
    if not experience:
        return 0

    total_years = 0
    for exp in experience:
        start = exp.get("start_date", "")
        end = exp.get("end_date", "Present")

        # Parse years
        start_year = None
        end_year = None

        # Try YYYY-MM format
        match = re.match(r"(\d{4})", str(start))
        if match:
            start_year = int(match.group(1))

        match = re.match(r"(\d{4})", str(end))
        if match:
            end_year = int(match.group(1))

        if start_year and end_year:
            total_years += end_year - start_year
        elif start_year:
            total_years += date.today().year - start_year

    return total_years


# ── Technical score calculation ────────────────────────────────────


def compute_technical_score(
    candidate_skills: set[str],
    job_keywords: set[str],
    job_requirements: list[str] | None = None,
) -> int:
    """Compute technical score (0-100) based on keyword overlap.

    Higher is better. Uses keyword density and requirement matching.
    """
    if not job_keywords and not job_requirements:
        return 50  # Neutral score when no data

    matched, missing, overlap = compute_keyword_overlap(
        candidate_skills, job_keywords
    )

    # Base score from keyword overlap
    base_score = int(overlap * 100)

    # Boost if candidate has specific tech skills mentioned in requirements
    if job_requirements:
        req_keywords = set()
        for req in job_requirements:
            req_keywords.update(extract_keywords(req))
        req_matched, _, req_overlap = compute_keyword_overlap(
            candidate_skills, req_keywords
        )
        # Blend: 70% keyword overlap + 30% requirement overlap
        base_score = int(base_score * 0.7 + req_overlap * 100 * 0.3)

    return max(0, min(100, base_score))


def compute_experience_score(
    candidate_years: int | None,
    job_requirements: list[str] | None,
    job_keywords: set[str],
    candidate_skills: set[str],
) -> int:
    """Compute experience score (0-100) based on years and requirement matching.

    Higher is better.
    """
    if not job_requirements and not job_keywords:
        return 50

    score = 0
    components = 0

    # Years of experience component
    required_years = extract_years_experience(
        " ".join(job_requirements) if job_requirements else None
    )
    if required_years and candidate_years:
        components += 1
        ratio = min(candidate_years / required_years, 2.0)
        if ratio >= 1.0:
            score += 100
        elif ratio >= 0.75:
            score += 75
        elif ratio >= 0.5:
            score += 50
        else:
            score += 25

    # Requirement keyword matching
    if job_requirements:
        components += 1
        req_keywords = set()
        for req in job_requirements:
            req_keywords.update(extract_keywords(req))
        _, _, req_overlap = compute_keyword_overlap(
            candidate_skills, req_keywords
        )
        score += int(req_overlap * 100)

    return max(0, min(100, score // max(components, 1)))


# ── Remote / hybrid detection ──────────────────────────────────────


def detect_remote_or_hybrid(job_text: str | None) -> str | None:
    """Detect if a job is remote, hybrid, or onsite.

    Returns "remote", "hybrid", "onsite", or None.
    """
    if not job_text:
        return None

    text = normalize_text(job_text)
    has_remote = any(kw in text for kw in REMOTE_KEYWORDS)
    has_onsite = any(kw in text for kw in ONSITE_KEYWORDS)

    if has_remote and has_onsite:
        return "hybrid"
    if has_remote:
        return "remote"
    if has_onsite:
        return "onsite"
    return None


# ── Missing keyword extraction ─────────────────────────────────────


def compute_missing_keywords(
    candidate_profile: dict[str, Any] | None,
    job_posting: dict[str, Any],
    max_keywords: int = 5,
) -> list[str]:
    """Compute missing keywords deterministically.

    Extracts keywords from both candidate profile and job posting,
    then returns the set difference (job keywords not in profile).
    """
    # Extract candidate keywords
    candidate_skills_set: set[str] = set()
    if candidate_profile:
        skills = candidate_profile.get("skills", {}) or {}
        for prog in skills.get("programming_ml", []):
            lang = prog.get("language", "")
            if lang:
                candidate_skills_set.add(normalize_text(lang))
            for fw in prog.get("frameworks", []):
                candidate_skills_set.add(normalize_text(fw))
        for domain in skills.get("domain_expertise", []):
            candidate_skills_set.add(normalize_text(domain))
        for tool in skills.get("software_tools", []):
            candidate_skills_set.add(normalize_text(tool))

    # Extract from experience bullets
    for exp in (candidate_profile or {}).get("experience", []):
        for bullet in exp.get("bullets", []):
            candidate_skills_set.update(extract_keywords(bullet))

    # Extract job keywords
    job_keywords: set[str] = set()
    title = job_posting.get("title", "") or ""
    description = job_posting.get("description", "") or ""
    requirements = job_posting.get("requirements") or []

    job_keywords.update(extract_keywords(title))
    job_keywords.update(extract_keywords(description))
    for req in requirements:
        job_keywords.update(extract_keywords(req))

    # Find missing keywords
    missing = job_keywords - candidate_skills_set

    # Prioritize by length (longer = more specific) and score
    scored: list[tuple[str, float]] = []
    for kw in missing:
        score = len(kw)  # Longer keywords are more specific/important
        # Boost score if keyword appears in requirements (not just description)
        req_text = " ".join(requirements).lower()
        if kw in req_text:
            score *= 1.5
        scored.append((kw, score))

    # Sort by score descending
    scored.sort(key=lambda x: x[1], reverse=True)
    return [kw for kw, _ in scored[:max_keywords]]


# ── Main deterministic analysis ────────────────────────────────────


def determine_location_status(
    candidate_location: str | None,
    job_location: str | None,
    candidate_constraints: str | None = None,
) -> str:
    """Public wrapper for analyze_location."""
    return analyze_location(candidate_location, job_location, candidate_constraints)


def determine_deadline(
    job_description: str | None,
    job_deadline_field: str | None,
) -> tuple[str | None, bool]:
    """Public wrapper for deadline extraction."""
    deadline_str, is_urgent = extract_deadline(job_description or "")
    if not deadline_str and job_deadline_field:
        deadline_str = job_deadline_field
        try:
            deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d").date()
            is_urgent = (deadline_date - date.today()).days <= 7
        except ValueError:
            pass
    return deadline_str, is_urgent


def compute_quantitative_scores(
    candidate: dict[str, Any] | None,
    job: dict[str, Any],
    job_target: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute all quantitative scores deterministically.

    Returns a dict with:
    - technical_score
    - experience_score
    - location_status
    - deadline
    - deadline_urgent
    - missing_keywords
    - language
    """
    # Extract candidate skills
    candidate_skills_set: set[str] = set()
    candidate_years: int = 0
    candidate_location: str | None = None
    candidate_constraints: str | None = None

    if candidate:
        skills = candidate.get("skills", {}) or {}
        for prog in skills.get("programming_ml", []):
            lang = prog.get("language", "")
            if lang:
                candidate_skills_set.add(normalize_text(lang))
            for fw in prog.get("frameworks", []):
                candidate_skills_set.add(normalize_text(fw))
        for domain in skills.get("domain_expertise", []):
            candidate_skills_set.add(normalize_text(domain))
        for tool in skills.get("software_tools", []):
            candidate_skills_set.add(normalize_text(tool))
        for exp in candidate.get("experience", []):
            for bullet in exp.get("bullets", []):
                candidate_skills_set.update(extract_keywords(bullet))
        candidate_years = estimate_candidate_years(candidate.get("experience"))
        candidate_location = candidate.get("location")
        candidate_constraints = candidate.get("constraints")

    # Extract job keywords
    job_keywords: set[str] = set()
    title = job.get("title", "") or ""
    description = job.get("description", "") or ""
    requirements = job.get("requirements") or []
    job_location = job.get("location")

    job_keywords.update(extract_keywords(title))
    job_keywords.update(extract_keywords(description))
    for req in requirements:
        job_keywords.update(extract_keywords(req))

    # Compute scores
    technical = compute_technical_score(
        candidate_skills_set, job_keywords, requirements
    )
    experience = compute_experience_score(
        candidate_years, requirements, job_keywords, candidate_skills_set
    )

    # Location
    location_status = analyze_location(
        candidate_location, job_location, candidate_constraints
    )

    # Deadline
    deadline_str, is_urgent = extract_deadline(description or "")
    if not deadline_str and job.get("deadline"):
        deadline_str = job["deadline"]
        try:
            deadline_date = datetime.strptime(deadline_str, "%Y-%m-%d").date()
            is_urgent = (deadline_date - date.today()).days <= 7
        except ValueError:
            pass

    # Language
    language = detect_language(description or "")
    if not language:
        language = job.get("language")

    # Missing keywords
    missing = compute_missing_keywords(candidate, job)

    # ── Apply job_target refinements ──────────────────────────
    if job_target:
        target_titles = job_target.get("target_titles", [])
        keywords = job_target.get("keywords", [])
        exclude_companies_str = job_target.get("exclude_companies", "")
        exclude_keywords = job_target.get("exclude_keywords", [])
        target_work_modes = job_target.get("work_mode", [])

        # Boost technical score if job title matches target titles
        title_lower = title.lower()
        for t in target_titles:
            if t.lower() in title_lower:
                technical = min(100, technical + 8)
                break

        # Boost for priority keywords found in job description
        if keywords:
            desc_lower = description.lower()
            found = sum(1 for kw in keywords if kw.lower() in desc_lower)
            technical = min(100, technical + min(found * 3, 15))

        # Exclude companies
        if exclude_companies_str and job.get("company"):
            excluded = [c.strip().lower() for c in exclude_companies_str.split(",")]
            if job["company"].lower() in excluded:
                return {
                    "technical_score": 0,
                    "experience_score": 0,
                    "location_status": "excluded_company",
                    "deadline": deadline_str,
                    "deadline_urgent": is_urgent,
                    "missing_keywords": missing + list(exclude_keywords),
                    "language": language,
                    "_candidate_skills": list(candidate_skills_set),
                    "_job_keywords": list(job_keywords),
                    "_veto": True,
                    "_veto_reason": f"Company {job['company']} is excluded",
                }

        # Add exclude_keywords to missing if present in description
        if exclude_keywords:
            ek_lower = [ek.lower() for ek in exclude_keywords]
            desc_lower = description.lower()
            found_excludes = [ek for ek in ek_lower if ek in desc_lower]
            if found_excludes:
                missing.extend([f"⚠️ Avoid: {ek}" for ek in found_excludes])
                technical = max(0, technical - 12)

        # Penalize work_mode mismatch
        if target_work_modes and "remote" not in target_work_modes:
            desc_lower = description.lower()
            if "remote" in desc_lower:
                technical = max(0, technical - 5)

    return {
        "technical_score": technical,
        "experience_score": experience,
        "location_status": location_status,
        "deadline": deadline_str,
        "deadline_urgent": is_urgent,
        "missing_keywords": missing,
        "language": language,
        "_candidate_skills": list(candidate_skills_set),
        "_job_keywords": list(job_keywords),
        # These are still LLM responsibilities:
        # behavioral_score, career_score, strengths, gaps, red_flags
    }
