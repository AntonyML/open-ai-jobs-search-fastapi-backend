"""CVLinter — deterministic, LLM-free quality checks on generated CVs (CAPA 3).

Runs after Pydantic validation and before persistence. Every check is cheap
and deterministic; issues are returned as actionable English instructions
that ``build_lint_retry_prompt`` turns into a surgical correction request for
the model (no more blind retries).

All profile access is defensive: a partial or mock profile never crashes the
pipeline — missing data simply disables the related check.
"""

from __future__ import annotations

import re
from typing import Any

# Placeholders the drafter must never leave in the document.
PLACEHOLDER_RE = re.compile(
    r"\[[^\]]*\]|\{[^}]*\}|YOUR\s*NAME|TBD|COMPANY NAME",
    re.IGNORECASE,
)

# Bullet openers that read as vague and get filtered by ATS keyword parsers.
FORBIDDEN_BULLET_OPENERS = ("responsible for", "helped", "worked on", "assisted")

# Bullets shorter than this cannot carry an X-Y-Z claim (ATS guardrail goal).
MIN_BULLET_LENGTH = 25

_COMPANY_SUFFIXES = ("inc", "llc", "ltd", "corp", "co", "sa", "sl", "srl", "gmbh")


def lint_cv(output: dict[str, Any], profile: Any) -> list[str]:
    """Return actionable quality issues, or an empty list when clean."""
    issues: list[str] = []
    cv = output.get("cv") if isinstance(output, dict) else None
    if not isinstance(cv, dict):
        return issues

    _placeholder_issues(cv, profile, issues)
    _bullet_issues(cv, issues)
    _company_issues(cv, profile, issues)
    _ats_basics_issues(cv, profile, issues)
    return issues


# ── Placeholders ───────────────────────────────────────────────────────


def _placeholder_issues(node: Any, profile: Any, issues: list[str], path: str = "") -> None:
    """Walk every string field and flag leftover template placeholders."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}" if path else key
            _placeholder_issues(value, profile, issues, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            _placeholder_issues(value, profile, issues, f"{path}[{index}]")
    elif isinstance(node, str):
        for match in PLACEHOLDER_RE.finditer(node):
            issues.append(f"Placeholder found: {match.group(0)!r} in {path} — {_placeholder_fix(path, profile)}")


def _placeholder_fix(path: str, profile: Any) -> str:
    """Give the retry prompt the concrete replacement data when we have it."""
    leaf = path.rsplit(".", 1)[-1].rsplit("[", 1)[0]
    if leaf in ("first_name", "last_name"):
        name = _safe_profile_attr(profile, "full_name")
        return f"replace with the candidate's real name ({name})" if name else "replace with the candidate's real name"
    if leaf == "email":
        email = _safe_profile_attr(profile, "email")
        return (
            f"replace with the candidate's real email ({email})" if email else "replace with the candidate's real email"
        )
    if leaf == "phone":
        phone = _safe_profile_attr(profile, "phone")
        return (
            f"replace with the candidate's real phone ({phone})" if phone else "replace with the candidate's real phone"
        )
    return "replace with real data from the candidate profile"


# ── Experience bullets ─────────────────────────────────────────────────


def _bullet_issues(cv: dict, issues: list[str]) -> None:
    for index, entry in enumerate(cv.get("experience") or [], start=1):
        company = str(entry.get("company") or "?")
        for b_index, raw in enumerate(entry.get("bullets") or [], start=1):
            text = str(raw).strip()
            if not text:
                continue
            lowered = text.lower()
            if len(text) < MIN_BULLET_LENGTH:
                issues.append(
                    f"Bullet {b_index} in experience entry {index} ({company}) is only "
                    f"{len(text)} chars — too short; rewrite it with the X-Y-Z formula"
                )
            opener = next((o for o in FORBIDDEN_BULLET_OPENERS if lowered.startswith(o)), None)
            if opener is not None:
                issues.append(
                    f"Bullet {b_index} in experience entry {index} ({company}) starts with "
                    f'"{opener}" — rewrite it with a strong past-tense action verb'
                )


# ── Company cross-reference (hallucination guard) ──────────────────────


def _company_issues(cv: dict, profile: Any, issues: list[str]) -> None:
    profile_companies = [str(entry.get("company") or "").strip() for entry in _profile_list(profile, "experience")]
    profile_companies = [c for c in profile_companies if c]
    if not profile_companies:
        return  # nothing to cross-check against

    normalized = [_normalize_company(c) for c in profile_companies]
    for index, entry in enumerate(cv.get("experience") or [], start=1):
        company = str(entry.get("company") or "").strip()
        if not company:
            continue
        nc = _normalize_company(company)
        if nc and not any(nc == pc or (len(nc) > 3 and (nc in pc or pc in nc)) for pc in normalized):
            issues.append(
                f'Company "{company}" in experience entry {index} does not appear in '
                f"the candidate profile ({', '.join(profile_companies)}) — do not "
                "invent employers, use the profile companies"
            )


def _normalize_company(name: str) -> str:
    """Lowercase, strip punctuation and common legal suffixes ('Acme Inc.' → 'acme')."""
    words = re.sub(r"[^a-z0-9 ]", " ", name.lower()).split()
    if words and words[-1] in _COMPANY_SUFFIXES:
        words = words[:-1]
    return " ".join(words)


# ── ATS basics (header identity + section presence) ────────────────────


def _ats_basics_issues(cv: dict, profile: Any, issues: list[str]) -> None:
    profile_email = _safe_profile_attr(profile, "email")
    cv_email = str(cv.get("email") or "").strip()
    if profile_email and cv_email and cv_email.lower() != profile_email.lower():
        issues.append(
            f'Header email "{cv_email}" does not match the candidate profile ({profile_email}) — use the real email'
        )

    profile_phone = _safe_profile_attr(profile, "phone")
    cv_phone = str(cv.get("phone") or "").strip()
    if profile_phone and cv_phone and cv_phone != profile_phone:
        issues.append(
            f'Header phone "{cv_phone}" does not match the candidate profile ({profile_phone}) — use the real phone'
        )

    for group in cv.get("skills") or []:
        label = str(group.get("label") or "Untitled group")
        if not (group.get("skills") or []):
            issues.append(f'Skill group "{label}" is empty — populate it from the candidate profile skills')

    if _profile_list(profile, "education") and not (cv.get("education") or []):
        issues.append("Education is missing from the CV — include the candidate's education from the profile")


# ── Defensive profile access ───────────────────────────────────────────


def _profile_list(profile: Any, attr: str) -> list:
    """Return a real list for a profile attribute, or [] (never a mock/None)."""
    try:
        value = getattr(profile, attr, None)
    except Exception:
        return []
    return value if isinstance(value, list) else []


def _safe_profile_attr(profile: Any, name: str) -> str | None:
    """Return a non-empty string attribute, or None (never raises, rejects mocks)."""
    try:
        value = getattr(profile, name, None)
    except Exception:
        return None
    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip()
