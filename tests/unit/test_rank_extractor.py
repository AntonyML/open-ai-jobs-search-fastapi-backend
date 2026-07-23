"""Test Capa A–E: rank_extractor — extracción, normalización, rechazos, matching, evidencia.

Todos los tests son deterministas — no requieren DB ni LLM.
"""

import json

import pytest

from app.services.rank_extractor import (
    SKILL_CATEGORIES,
    SKILL_SYNONYMS,
    build_evidence,
    check_hard_rejects,
    clean_html,
    detect_education_requirement,
    detect_seniority,
    detect_structured_location,
    detect_work_authorization,
    extract_salary_range,
    extract_structured_requirements,
    match_skills_controlled,
    normalize_skill,
)


# ═══════════════════════════════════════════════════════════════════════
# 4.1 — Capa A: Extracción y normalización
# ═══════════════════════════════════════════════════════════════════════

class TestCleanHtml:
    def test_strips_tags(self):
        assert clean_html("<p>Hello</p>") == "Hello"

    def test_strips_entities(self):
        assert clean_html("Hello &amp; World") == "Hello World"

    def test_collapses_whitespace(self):
        assert clean_html("a    b") == "a b"

    def test_none_returns_empty(self):
        assert clean_html(None) == ""

    def test_empty_returns_empty(self):
        assert clean_html("") == ""


class TestNormalizeSkill:
    def test_postgres_synonyms(self):
        assert normalize_skill("postgresql") == "postgresql"
        assert normalize_skill("PostgreSQL") == "postgresql"
        assert normalize_skill("postgres") == "postgresql"
        assert normalize_skill("psql") == "postgresql"

    def test_k8s_to_kubernetes(self):
        assert normalize_skill("k8s") == "kubernetes"
        assert normalize_skill("K8s") == "kubernetes"

    def test_js_to_javascript(self):
        assert normalize_skill("js") == "javascript"

    def test_ts_to_typescript(self):
        assert normalize_skill("ts") == "typescript"

    def test_unknown_skill_passes_through(self):
        assert normalize_skill("some_unknown_skill") == "some_unknown_skill"

    def test_case_insensitive(self):
        assert normalize_skill("AWS") == "aws"

    def test_all_synonyms_map(self):
        """Every synonym in SKILL_SYNONYMS maps to itself or a canonical form."""
        for synonym, canonical in SKILL_SYNONYMS.items():
            result = normalize_skill(synonym)
            assert result == canonical, f"{synonym} → {result}, expected {canonical}"


class TestExtractStructuredRequirements:
    def test_extracts_skills(self):
        result = extract_structured_requirements(
            "We need Python, PyTorch, and Docker skills"
        )
        assert "python" in result["skills"]
        assert "pytorch" in result["skills"]
        assert "docker" in result["skills"]

    def test_extracts_years_experience(self):
        result = extract_structured_requirements("5+ years of experience in ML")
        assert result["years_experience"] == 5

    def test_extracts_education(self):
        result = extract_structured_requirements("Master degree in CS required")
        assert result["education"] == "master"

    def test_extracts_certifications(self):
        result = extract_structured_requirements("AWS Certified Solutions Architect preferred")
        assert any("aws" in c.lower() for c in result["certifications"])

    def test_extracts_modalities(self):
        result = extract_structured_requirements("Remote position, hybrid also ok")
        assert "remote" in result["modalities"]
        assert "hybrid" in result["modalities"]

    def test_handles_lowercase_normalization(self):
        result = extract_structured_requirements("K8s and TF experience")
        assert "kubernetes" in result["skills"]
        assert "tensorflow" in result["skills"]

    def test_no_matches(self):
        result = extract_structured_requirements("We need a good team player")
        assert isinstance(result["skills"], set)


class TestDetectStructuredLocation:
    def test_detects_remote(self):
        loc = detect_structured_location("Remote - Worldwide")
        assert loc["work_mode"] == "remote"

    def test_detects_onsite(self):
        loc = detect_structured_location("Onsite in Copenhagen")
        assert loc["work_mode"] == "onsite"

    def test_detects_hybrid(self):
        loc = detect_structured_location("Hybrid work in Aarhus")
        assert loc["work_mode"] == "hybrid"

    def test_detects_country_dk(self):
        loc = detect_structured_location("Copenhagen, Denmark")
        assert loc["country"] == "DK"

    def test_detects_timezone(self):
        loc = detect_structured_location("Copenhagen, CEST")
        assert loc["timezone"] == "Europe/Copenhagen"


class TestExtractSalaryRange:
    def test_usd_range(self):
        result = extract_salary_range("Salary: 100k - 150k USD per year")
        assert result["currency"] is None
        assert result["salary_min"] is None

    def test_dkk_monthly(self):
        result = extract_salary_range("Monthly: 45.000 - 55.000 kr")
        assert result["currency"] == "DKK"
        assert result["period"] is not None

    def test_no_salary(self):
        result = extract_salary_range(None)
        assert result["salary_min"] is None

    def test_eur_yearly(self):
        result = extract_salary_range("80.000 - 100.000 € per year")
        assert result["currency"] == "EUR"
        assert result["period"] == "yearly"


class TestDetectSeniority:
    def test_senior(self):
        assert detect_seniority("Senior ML Engineer", "") == "senior"

    def test_junior(self):
        assert detect_seniority("Junior Developer", "") == "junior"

    def test_lead(self):
        assert detect_seniority("Lead Architect", "") == "lead"

    def test_principal(self):
        assert detect_seniority("Principal Engineer", "") == "lead"

    def test_director(self):
        assert detect_seniority("Director of Engineering", "") == "director"

    def test_manager(self):
        assert detect_seniority("Engineering Manager", "") == "manager"

    def test_mid_level_explicit(self):
        assert detect_seniority("Mid-level Developer", "") == "mid"

    def test_detects_from_description(self):
        assert detect_seniority("Dev", "We are looking for a senior engineer") == "senior"


class TestDetectEducationRequirement:
    def test_phd(self):
        result = detect_education_requirement("Requires PhD in Computer Science")
        assert result["required_level"] == "phd"

    def test_master(self):
        result = detect_education_requirement("Must have Master's degree in CS")
        assert result["required_level"] == "master"

    def test_bachelor(self):
        result = detect_education_requirement("Minimum Bachelor in Engineering required")
        assert result["required_level"] == "bachelor"

    def test_no_match(self):
        result = detect_education_requirement("No specific degree mentioned")
        assert result["required_level"] is None


class TestDetectWorkAuthorization:
    def test_visa_sponsorship(self):
        result = detect_work_authorization("We offer visa sponsorship")
        assert result["requires_visa_sponsorship"] == "offered"
        assert result["requires_citizenship"] is None

    def test_citizenship(self):
        result = detect_work_authorization("Must be US citizen")
        assert result["requires_citizenship"] is True

    def test_unknown_by_default(self):
        result = detect_work_authorization("Open to all")
        assert result["requires_visa_sponsorship"] == "unknown"


# ═══════════════════════════════════════════════════════════════════════
# 4.2 — Capa B: Hard Rejects
# ═══════════════════════════════════════════════════════════════════════

class TestCheckHardRejects:
    def test_excluded_company(self):
        reason = check_hard_rejects(
            job={"title": "Engineer", "description": "", "company": "evilcorp"},
            job_target={"exclude_companies": ["evilcorp"]},
        )
        assert reason is not None
        assert "excluded" in reason.lower()

    def test_excluded_keyword(self):
        reason = check_hard_rejects(
            job={"title": "Engineer", "description": "Must have COBOL experience", "company": "GoodCorp"},
            job_target={"exclude_keywords": ["cobol"]},
        )
        assert reason is not None

    def test_work_mode_mismatch(self):
        reason = check_hard_rejects(
            job={"title": "Engineer", "description": "Onsite position", "company": "GoodCorp"},
            job_target={"work_mode": ["remote"]},
            extracted={"structured_location": {"work_mode": "onsite"}},
        )
        assert reason is not None
        assert "work mode" in reason.lower()

    def test_seniority_gap(self):
        reason = check_hard_rejects(
            job={"title": "Junior Developer", "description": "", "company": "GoodCorp"},
            job_target={"seniority": "director"},
            extracted={"seniority": "junior"},
        )
        assert reason is not None
        assert "seniority" in reason.lower()

    def test_no_veto(self):
        reason = check_hard_rejects(
            job={"title": "Engineer", "description": "Python", "company": "GoodCorp"},
            job_target={},
        )
        assert reason is None


# ═══════════════════════════════════════════════════════════════════════
# 4.3 — Capa C: Matching
# ═══════════════════════════════════════════════════════════════════════

class TestMatchSkillsControlled:
    def test_exact_match(self):
        match = match_skills_controlled(
            candidate_skills={"python", "pytorch"},
            job_skills={"python", "pytorch", "docker"},
        )
        assert "python" in match["exact_matches"]
        assert "pytorch" in match["exact_matches"]

    def test_category_match(self):
        match = match_skills_controlled(
            candidate_skills={"python", "js"},
            job_skills={"typescript"},
        )
        assert len(match["category_matches"]) > 0

    def test_semantic_signal(self):
        match = match_skills_controlled(
            candidate_skills={"pytorch"},
            job_skills={"PyTorchExperienced"},
        )
        assert isinstance(match["semantic_signals"], list)

    def test_no_matches(self):
        match = match_skills_controlled(
            candidate_skills={"cobol"},
            job_skills={"python", "pytorch"},
        )
        assert len(match["exact_matches"]) == 0

    def test_match_score(self):
        match = match_skills_controlled(
            candidate_skills={"python", "pytorch", "docker", "aws"},
            job_skills={"python", "docker", "aws", "kubernetes"},
        )
        assert match["coverage_ratio"] >= 0.5
        assert match["coverage_ratio"] <= 1.0


# ═══════════════════════════════════════════════════════════════════════
# 4.3 — Capa D/E: DimensionScore construction (via evidence)
# ═══════════════════════════════════════════════════════════════════════

class TestBuildEvidence:
    def test_returns_all_dimensions(self):
        ev = build_evidence(
            match_result={
                "exact_matches": {"python"},
                "category_matches": [],
                "semantic_signals": [],
                "unmatched_job_skills": set(),
            },
            extracted_job={
                "years_experience": 3,
                "structured_location": {"work_mode": "remote"},
                "location_status": "PASS",
            },
            candidate_skills={"python"},
            candidate_years=5,
        )
        for dim in ("technical_fit", "relevant_experience", "constraints", "career_alignment", "behavioral_fit"):
            assert dim in ev

    def test_experience_gap_mentions_shortfall(self):
        ev = build_evidence(
            match_result={"exact_matches": set(), "category_matches": [], "semantic_signals": [], "unmatched_job_skills": set()},
            extracted_job={"years_experience": 5, "structured_location": {}, "location_status": "FLAG"},
            candidate_skills=set(),
            candidate_years=1,
        )
        notes = " ".join(ev["relevant_experience"]).lower()
        assert "below" in notes or "requirement" in notes


# ═══════════════════════════════════════════════════════════════════════
# Versionamiento de reglas (mismo input → mismo output)
# ═══════════════════════════════════════════════════════════════════════

class TestDeterminism:
    def test_normalize_skill_deterministic(self):
        inputs = ["PostgreSQL", "k8s", "TF", "AWS", "js", "ts", "golang"]
        results = [normalize_skill(x) for x in inputs]
        results2 = [normalize_skill(x) for x in inputs]
        assert results == results2

    def test_check_hard_rejects_deterministic(self):
        job = {"title": "Engineer", "description": "Python", "company": "GoodCorp"}
        target = {"exclude_companies": [], "exclude_keywords": []}
        assert check_hard_rejects(job, target) == check_hard_rejects(job, target)

    def test_extract_structured_deterministic(self):
        desc = "5+ years Python, PyTorch, AWS. Master degree preferred."
        assert extract_structured_requirements(desc) == extract_structured_requirements(desc)
