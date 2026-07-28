"""Tests for the comparison harness — heuristic scoring and report formatting.

These are deterministic, no DB, no LLM.
"""

from __future__ import annotations

from app.scripts.compare_paths import (
    ComparisonReport,
    PathResult,
    build_path_result_from_application,
    compute_comparison,
    detect_generic_phrases,
    format_report,
    score_keyword_coverage,
    score_xyz_usage,
)


class TestHeuristicScoring:
    """Deterministic scoring functions used in comparison."""

    def test_xyz_count_high_for_action_bullets(self):
        text = "Reduced latency by 40% through caching, achieving 99.9% uptime"
        assert score_xyz_usage(text) >= 2

    def test_xyz_count_low_for_responsibility_bullets(self):
        text = "Was responsible for maintaining the legacy system"
        assert score_xyz_usage(text) == 0

    def test_detect_generic_phrases(self):
        text = "Responsible for the team and tasked with delivery"
        found = detect_generic_phrases(text)
        assert "responsible for" in found
        assert "tasked with" in found

    def test_no_generic_phrases(self):
        text = "Built a recommendation system reducing churn by 15%"
        assert detect_generic_phrases(text) == []

    def test_keyword_coverage_all_match(self):
        bullets = ["Python expert", "PyTorch model deployment"]
        result = score_keyword_coverage(bullets, {"Python", "PyTorch", "ML"})
        assert result["Python"] is True
        assert result["PyTorch"] is True
        assert result["ML"] is False  # Not in any bullet


class TestPathResultBuilder:
    """Build PathResult from dict data (cached or DB-agnostic)."""

    def test_from_empty_dict(self):
        result = build_path_result_from_application({}, "LaTeX")
        assert result.path_name == "LaTeX"
        assert result.cv_pages == 0

    def test_from_none(self):
        result = build_path_result_from_application(None, "Typst")
        assert result.error == "No application data"

    def test_from_dict_with_experience(self):
        app = {
            "pipeline_stage": "compiled",
            "cv_pages": 2,
            "cv_compiled": True,
            "cover_letter_compiled": True,
            "ats_pass": True,
            "ats_score": 0.85,
            "tailored_experience": [
                {"title": "Engineer", "bullets": ["Built X reducing Y by Z", "Led team of 3"]},
                {"title": "Scientist", "bullets": ["Analyzed data with Python"]},
            ],
        }
        result = build_path_result_from_application(app, "Typst")
        assert result.cv_pages == 2
        assert result.cv_compiled is True
        assert result.ats_pass is True
        assert result.total_bullets == 3
        assert result.xyz_formula_count == 2  # "reducing" + "by"
        assert "Built X reducing Y by Z" in result.cv_bullets

    def test_keyword_matches_from_dict(self):
        app = {
            "tailored_experience": [
                {"bullets": ["Python development", "Kubernetes deployment"]},
            ],
        }
        result = build_path_result_from_application(
            app, "Typst", job_keywords={"Python", "Kubernetes", "AWS"}
        )
        assert result.keyword_matches["Python"] is True
        assert result.keyword_matches["Kubernetes"] is True
        assert result.keyword_matches["AWS"] is False

    def test_generic_phrases_from_dict(self):
        app = {
            "tailored_experience": [
                {"bullets": ["Responsible for the database", "Built API with FastAPI"]},
            ],
        }
        result = build_path_result_from_application(app, "LaTeX")
        assert "responsible for" in result.generic_phrases


class TestComparisonLogic:
    """Comparison scoring between LaTeX and Typst results."""

    def test_latex_wins_on_pages_closer_to_target(self):
        report = ComparisonReport(
            latex=PathResult(path_name="LaTeX", cv_pages=2),
            typst=PathResult(path_name="Typst", cv_pages=3),
        )
        report = compute_comparison(report)
        assert any("page count" in w for w in report.latex_wins)

    def test_typst_wins_on_keyword_coverage(self):
        report = ComparisonReport(
            latex=PathResult(path_name="LaTeX", ats_keyword_coverage=0.5),
            typst=PathResult(path_name="Typst", ats_keyword_coverage=0.9),
        )
        report = compute_comparison(report)
        assert any("keyword coverage" in w for w in report.typst_wins)

    def test_tie_when_equal(self):
        report = ComparisonReport(
            latex=PathResult(path_name="LaTeX", cv_pages=2, ats_keyword_coverage=0.7),
            typst=PathResult(path_name="Typst", cv_pages=2, ats_keyword_coverage=0.7),
        )
        report = compute_comparison(report)
        assert len(report.ties) >= 2

    def test_latex_wins_on_fewer_generic_phrases(self):
        report = ComparisonReport(
            latex=PathResult(
                path_name="LaTeX",
                cv_bullets=["Built X"],
                generic_phrases=[],
                total_bullets=1,
                avg_bullet_length=10,
                xyz_formula_count=0,
                keyword_matches={},
            ),
            typst=PathResult(
                path_name="Typst",
                cv_bullets=["Responsible for X"],
                generic_phrases=["responsible for"],
                total_bullets=1,
                avg_bullet_length=10,
                xyz_formula_count=0,
                keyword_matches={},
            ),
        )
        report = compute_comparison(report)
        assert any("generic" in w.lower() for w in report.latex_wins)


class TestReportFormatting:
    """Report rendering is deterministic and well-structured."""

    def test_basic_report(self):
        report = ComparisonReport(
            profile_name="Jane Doe",
            job_title="ML Engineer",
            job_company="TechCorp",
            job_language="en",
            latex=PathResult(
                path_name="LaTeX", cv_pages=2, cv_compiled=True,
                ats_pass=True, ats_keyword_coverage=0.8,
                cv_bullets=["Built X with Python", "Deployed on K8s"],
                total_bullets=2, xyz_formula_count=1, avg_bullet_length=20,
                generic_phrases=[], keyword_matches={"Python": True},
            ),
            typst=PathResult(
                path_name="Typst", cv_pages=3, cv_compiled=True,
                ats_pass=False, ats_keyword_coverage=0.6,
                cv_bullets=["Built X with Python"],
                total_bullets=1, xyz_formula_count=1, avg_bullet_length=20,
                generic_phrases=["responsible for"],
                keyword_matches={"Python": True},
            ),
        )
        report = compute_comparison(report)
        md = format_report(report)
        assert "Jane Doe" in md
        assert "ML Engineer" in md
        assert "TechCorp" in md
        assert "LaTeX" in md
        assert "Typst" in md
        assert "Quick Verdict" in md
        assert "Side-by-Side Metrics" in md
        assert "Built X with Python" in md

    def test_report_with_errors(self):
        report = ComparisonReport(
            latex=PathResult(path_name="LaTeX", error="Compilation failure"),
            typst=PathResult(path_name="Typst", cv_pages=2, cv_compiled=True),
        )
        md = format_report(report)
        assert "Compilation failure" in md
        assert "Errors & Warnings" in md

    def test_json_round_trip(self):
        report = ComparisonReport(
            latex=PathResult(path_name="LaTeX", cv_pages=2, cv_compiled=True),
            typst=PathResult(path_name="Typst", cv_pages=1, cv_compiled=True),
        )
        report = compute_comparison(report)
        js = report.to_json()
        import json
        data = json.loads(js)
        assert data["latex"]["cv_pages"] == 2
        assert data["typst"]["cv_pages"] == 1
