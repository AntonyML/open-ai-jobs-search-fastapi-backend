"""Tests for the CV cutter service — relevance-weighted bullet removal.

Tests are 100% deterministic (no LLM calls). The CV cutter operates
on the data level (TailoredExperienceEntry bullets), not on LaTeX.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import CandidateProfile, JobPosting, RankEvaluation, User
from app.schemas.apply import TailoredExperienceEntry
from app.schemas.ats_check import ATSResult
from app.services import cv_cutter


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    """Create an in-memory SQLite database for testing."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        from app.db.models import Base
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        user = User(
            id="test-user-id",
            email="test@example.com",
            hashed_password="fakehash",
            full_name="Test User",
        )
        session.add(user)
        await session.commit()
        yield session

    await engine.dispose()


# ── Helpers ─────────────────────────────────────────────────────────


def _make_job(requirements: list[str] | None = None, description: str = "") -> JobPosting:
    return JobPosting(
        id="test-job-cvcut",
        user_id="test-user-id",
        portal="test",
        external_id="test-job-cvcut",
        title="ML Engineer",
        company="TestCorp",
        description=description or "We need an ML Engineer with experience in Python, PyTorch, and Kubernetes.",
        requirements=requirements or ["Python", "PyTorch", "Kubernetes", "AWS", "Docker"],
        language="en",
    )


def _make_experience(*bullet_lists: list[str]) -> list[TailoredExperienceEntry]:
    entries = []
    for i, bullets in enumerate(bullet_lists):
        entries.append(TailoredExperienceEntry(
            title=f"Role {i + 1}", company=f"Company {i + 1}",
            start_date="2020-01", end_date="Present", location="Copenhagen",
            bullets=bullets,
        ))
    return entries


def _make_render_fn():
    def _render(experience: list[TailoredExperienceEntry]) -> str:
        lines = []
        for exp in experience:
            lines.append(f"% {exp.title} at {exp.company}")
            for bullet in exp.bullets:
                lines.append(f"\\item {bullet}")
        return "\n".join(lines)
    return _render


async def _make_compile_fn(page_count: int = 2):
    async def _compile(tex: str, out_dir: Path, name: str):
        return Path(f"/tmp/{name}.pdf"), page_count
    return _compile


def _make_mock_ats(pass_ats: bool = True) -> ATSResult:
    """Create a proper ATSResult for mocking (not an AsyncMock — avoids JSON serialization errors)."""
    return ATSResult(
        raw_text="Mock PDF text.",
        has_cid_markers=False,
        has_email=True,
        has_phone=True,
        has_candidate_name=True,
        keyword_coverage=1.0 if pass_ats else 0.3,
        found_keywords=["python"] if pass_ats else [],
        missing_keywords=[] if pass_ats else ["kubernetes"],
        reading_order_ok=True,
        pass_ats=pass_ats,
    )


# ── Unit tests for scoring functions ────────────────────────────────


class TestComputeRelevanceScore:
    def test_high_relevance(self):
        bullet = "Built PyTorch ML pipeline on Kubernetes and deployed with Docker on AWS"
        job = _make_job(requirements=["Python", "PyTorch", "Kubernetes", "AWS", "Docker"])
        assert cv_cutter._compute_relevance_score(bullet, job) > 0.5

    def test_low_relevance(self):
        bullet = "Managed team schedules and project timelines"
        job = _make_job(requirements=["Python", "PyTorch", "Kubernetes"])
        assert cv_cutter._compute_relevance_score(bullet, job) == 0.0

    def test_medium_relevance(self):
        bullet = "Worked with Python for data analysis tasks"
        job = _make_job(requirements=["Python", "PyTorch", "Kubernetes", "AWS", "Docker"])
        score = cv_cutter._compute_relevance_score(bullet, job)
        assert 0.0 < score < 1.0


class TestComputeUniquenessScore:
    def test_unique_bullet(self):
        bullet = "Implemented real-time fraud detection with Spark Streaming"
        all_bullets = [
            (0, 0, "Built PyTorch models for image classification"),
            (0, 1, "Deployed Kubernetes clusters on AWS EKS"),
            (1, 0, "Led team of 5 engineers"),
        ]
        assert cv_cutter._compute_uniqueness_score(bullet, all_bullets, (1, 1)) > 0.5

    def test_duplicate_bullet(self):
        bullet = "Built PyTorch model to classify customer churn"
        all_bullets = [
            (0, 0, "Built PyTorch model to classify customer segments"),
            (0, 1, "Deployed models with Kubernetes"),
        ]
        assert cv_cutter._compute_uniqueness_score(bullet, all_bullets, (0, 2)) < 0.5

    def test_single_bullet(self):
        bullet = "Some unique content here"
        assert cv_cutter._compute_uniqueness_score(bullet, [(0, 0, bullet)], (0, 0)) == 1.0


class TestComputeCoverReferenceScore:
    def test_referenced(self):
        bullet = "Reduced model inference latency by 40% using TensorRT"
        cover = "I reduced model inference latency by 40% using TensorRT at Acme."
        assert cv_cutter._compute_cover_reference_score(bullet, cover) == 1.0

    def test_not_referenced(self):
        bullet = "Built real-time data pipeline with Kafka"
        assert cv_cutter._compute_cover_reference_score(bullet, "Python experience.") == 0.0

    def test_empty_cover(self):
        assert cv_cutter._compute_cover_reference_score("Some bullet", "") == 0.0


class TestExtractJobKeywords:
    def test_from_requirements(self):
        job = _make_job(requirements=["Python", "PyTorch"])
        kw = cv_cutter._extract_job_keywords(job)
        assert "python" in kw and "pytorch" in kw

    def test_from_description(self):
        job = _make_job(requirements=None, description="Looking for an ML Engineer with PyTorch.")
        kw = cv_cutter._extract_job_keywords(job)
        assert "pytorch" in kw

    def test_stop_words_excluded(self):
        job = _make_job(requirements=["Python and PyTorch and Kubernetes"])
        kw = cv_cutter._extract_job_keywords(job)
        assert "and" not in kw
        assert "python" in kw


class TestRemoveBullet:
    def test_removes_middle_bullet(self):
        exp = _make_experience(["A", "B", "C"])
        result = cv_cutter._remove_bullet(exp, 0, 1)
        assert result[0].bullets == ["A", "C"]

    def test_preserves_last_bullet(self):
        exp = _make_experience(["Only"], ["Other", "Second"])
        result = cv_cutter._remove_bullet(exp, 0, 0)
        assert len(result[0].bullets) == 1  # Protected — can't remove last bullet

    def test_no_mutation(self):
        exp = _make_experience(["A", "B"])
        cv_cutter._remove_bullet(exp, 0, 1)
        assert len(exp[0].bullets) == 2  # Original unchanged


# ── Integration: trim_cv_to_page_limit ──────────────────────────────


@pytest.mark.asyncio
async def test_trim_no_trimming_needed():
    exp = _make_experience(["Bullet 1", "Bullet 2"], ["Bullet 3"])
    job = _make_job()
    _, trim_result = await cv_cutter.trim_cv_to_page_limit(
        experience=exp, job_posting=job, cover_letter_latex="",
        render_fn=_make_render_fn(), compile_fn=await _make_compile_fn(2),
        output_dir=Path("/tmp"), job_name="test_cv", max_pages=2,
    )
    assert not trim_result.was_trimmed
    assert trim_result.bullets_removed == 0


@pytest.mark.asyncio
async def test_trim_removes_lowest_scoring():
    exp = _make_experience(
        ["Built PyTorch models for image classification", "Deployed Kubernetes on AWS",
         "Managed team meetings and status reports"],
        ["Led team of 5 engineers", "Organized company hackathons"],
    )
    job = _make_job(requirements=["PyTorch", "Kubernetes", "AWS", "ML"])
    _, trim_result = await cv_cutter.trim_cv_to_page_limit(
        experience=exp, job_posting=job, cover_letter_latex="",
        render_fn=_make_render_fn(), compile_fn=await _make_compile_fn(3),
        output_dir=Path("/tmp"), job_name="test_cv", max_pages=2,
    )
    assert trim_result.was_trimmed
    assert trim_result.bullets_removed > 0
    removed = " ".join(trim_result.removed_bullet_texts).lower()
    assert any(kw in removed for kw in ["meetings", "hackathon", "report"])


@pytest.mark.asyncio
async def test_trim_protects_cover_referenced():
    exp = _make_experience([
        "Reduced model latency by 40% via TensorRT",
        "Managed team meetings and weekly reports",
        "Organized company social events",
    ])
    job = _make_job(requirements=["PyTorch"])
    cover = "I reduced model latency by 40% via TensorRT."
    result_exp, trim_result = await cv_cutter.trim_cv_to_page_limit(
        experience=exp, job_posting=job, cover_letter_latex=cover,
        render_fn=_make_render_fn(), compile_fn=await _make_compile_fn(3),
        output_dir=Path("/tmp"), job_name="test_cv", max_pages=2,
    )
    assert trim_result.was_trimmed
    assert any("latency" in b for b in result_exp[0].bullets)


@pytest.mark.asyncio
async def test_trim_preserves_min_bullets():
    exp = _make_experience(
        ["Only A"], ["Only B"], ["ML bullet", "Another relevant"],
    )
    job = _make_job(requirements=["ML"])
    result_exp, _ = await cv_cutter.trim_cv_to_page_limit(
        experience=exp, job_posting=job, cover_letter_latex="",
        render_fn=_make_render_fn(), compile_fn=await _make_compile_fn(5),
        output_dir=Path("/tmp"), job_name="test_cv", max_pages=2,
    )
    assert all(len(e.bullets) >= 1 for e in result_exp)


@pytest.mark.asyncio
async def test_trim_exhausts_return_best_effort():
    exp = _make_experience(["A1"], ["B1"], ["C1"])
    job = _make_job()
    _, trim_result = await cv_cutter.trim_cv_to_page_limit(
        experience=exp, job_posting=job, cover_letter_latex="",
        render_fn=_make_render_fn(), compile_fn=await _make_compile_fn(5),
        output_dir=Path("/tmp"), job_name="test_cv", max_pages=2,
    )
    assert trim_result.bullets_removed == 0


# ── Integration: CV cutter inside execute_apply ─────────────────────


@pytest.mark.asyncio
async def test_execute_apply_cv_fits_without_trim(db_session):
    from unittest.mock import patch
    from app.db.models import JobPosting as JP, CandidateProfile as CP, RankEvaluation as RE
    from app.services import apply
    from tests.unit.test_apply import mock_tailored_experience, mock_cover_letter

    job = JP(user_id="test-user-id", portal="linkedin", external_id="fit-1",
             title="ML Engineer", company="TechCorp", location="Copenhagen",
             description="ML job.", requirements=["Python"], employment_type="full-time",
             language="en", status="ranked", rank_score=75.0, rank_verdict="Fit")
    db_session.add(job)
    await db_session.commit(); await db_session.refresh(job)

    cand = CP(user_id="test-user-id", full_name="Jane Doe", location="Copenhagen",
              email="jane@example.com", phone="+45 12345678", employment_status="Employed",
              education=[], experience=[], skills={}, profile_statement="ML engineer.")
    db_session.add(cand); await db_session.commit()

    eval_rec = RE(job_posting_id=job.id, user_id="test-user-id", technical_score=75,
                  experience_score=70, behavioral_score=70, career_score=80,
                  overall_score=75, verdict="Fit", location_status="PASS",
                  deadline="2026-08-10", deadline_urgent=False, strengths=[],
                  gaps=[], missing_keywords=[], red_flags=[], language="en", raw_response={})
    db_session.add(eval_rec); await db_session.commit(); await db_session.refresh(eval_rec)

    mock_ats = _make_mock_ats(pass_ats=True)

    with patch("app.services.apply.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_tailored_experience(), mock_cover_letter(),
            apply.ReviewFeedback(overall_assessment="Good.", passes=[], issues=[], missed_keywords=[], strong_recommendations=[]),
            mock_tailored_experience(), mock_cover_letter(),
        ]
        with patch("app.services.apply.compile_latex") as mock_compile:
            mock_compile.side_effect = [(Path("/tmp/cv.pdf"), 2), (Path("/tmp/cover.pdf"), 1)]
            with patch("app.services.cv_cutter.trim_cv_to_page_limit") as mock_cutter:
                with patch("app.services.apply.shutil.copy2"):
                    with patch("app.services.apply.Path.mkdir"):
                        with patch("app.services.apply.Path.exists", return_value=True):
                            with patch("app.services.apply.Path.write_text"):
                                with patch("app.services.ats_check.check_ats_parseability", return_value=mock_ats):
                                    result = await apply.execute_apply(
                                        db=db_session, user_id="test-user-id",
                                        job_posting_id=job.id, rank_evaluation_id=eval_rec.id,
                                    )

    assert result.cv_compiled and result.cv_pages == 2
    mock_cutter.assert_not_called()


@pytest.mark.asyncio
async def test_execute_apply_with_cv_cutter_flow(db_session):
    from unittest.mock import patch
    from app.db.models import JobPosting as JP, CandidateProfile as CP, RankEvaluation as RE
    from app.exceptions import LatexCompileError
    from app.services import apply
    from app.schemas.cv_cutter import CVTrimResult
    from tests.unit.test_apply import mock_tailored_experience, mock_cover_letter

    job = JP(user_id="test-user-id", portal="linkedin", external_id="cut-1",
             title="Senior ML Engineer", company="TechCorp", location="Copenhagen",
             description="ML job.", requirements=["Python"], employment_type="full-time",
             language="en", status="ranked", rank_score=83.0, rank_verdict="Strong Fit")
    db_session.add(job)
    await db_session.commit(); await db_session.refresh(job)

    cand = CP(user_id="test-user-id", full_name="Jane Doe", location="Copenhagen",
              email="jane@example.com", phone="+45 12345678", employment_status="Employed",
              education=[], experience=[], skills={}, profile_statement="ML engineer.")
    db_session.add(cand); await db_session.commit()

    eval_rec = RE(job_posting_id=job.id, user_id="test-user-id", technical_score=85,
                  experience_score=80, behavioral_score=75, career_score=90,
                  overall_score=83, verdict="Strong Fit", location_status="PASS",
                  deadline="2026-08-10", deadline_urgent=False, strengths=[],
                  gaps=[], missing_keywords=[], red_flags=[], language="en", raw_response={})
    db_session.add(eval_rec); await db_session.commit(); await db_session.refresh(eval_rec)

    mock_ats = _make_mock_ats(pass_ats=True)

    with patch("app.services.apply.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_tailored_experience(), mock_cover_letter(),
            apply.ReviewFeedback(overall_assessment="Good.", passes=[], issues=[], missed_keywords=[], strong_recommendations=[]),
            mock_tailored_experience(), mock_cover_letter(),
        ]
        with patch("app.services.apply.compile_latex") as mock_compile:
            # 3 calls: 1st CV fails, 2nd cover ok, 3rd final CV ok
            mock_compile.side_effect = [
                LatexCompileError("Wrong page count for cv_: expected 2, got 3"),
                (Path("/tmp/cover.pdf"), 1),
                (Path("/tmp/cv_final.pdf"), 2),
            ]
            with patch("app.services.cv_cutter.trim_cv_to_page_limit") as mock_cutter:
                trimmed = mock_tailored_experience().experience
                mock_cutter.return_value = (trimmed, CVTrimResult(
                    entries_before=2, bullets_before=3, bullets_removed=1,
                    pages_achieved=2, removed_bullet_texts=["Low rel bullet"],
                    remaining_bullets_per_entry=[2, 1], was_trimmed=True,
                ))
                with patch("app.services.apply.compile_latex_get_pages") as mock_get_pages:
                    mock_get_pages.return_value = (Path("/tmp/cv_trimmed.pdf"), 3)
                    with patch("app.services.apply.Path.mkdir"):
                        with patch("app.services.apply.Path.exists", return_value=True):
                            with patch("app.services.apply.Path.write_text"):
                                with patch("app.services.apply.shutil.copy2"):
                                    with patch("app.services.ats_check.check_ats_parseability", return_value=mock_ats):
                                        result = await apply.execute_apply(
                                            db=db_session, user_id="test-user-id",
                                            job_posting_id=job.id, rank_evaluation_id=eval_rec.id,
                                        )
    assert result.application_id is not None and result.cv_compiled
    mock_cutter.assert_called_once()


# ═══════════════════════════════════════════════════════════════════════
# JSON/dict CV cutter (trim_cv_experience) — Fase 1.4
# ═══════════════════════════════════════════════════════════════════════


def _make_dict_experience(*bullet_lists: list[str]) -> list[dict]:
    entries = []
    for i, bullets in enumerate(bullet_lists):
        entries.append({
            "title": f"Role {i + 1}",
            "company": f"Company {i + 1}",
            "location": "Copenhagen",
            "date_range": {"start": "2020-01", "end": "Present"},
            "bullets": bullets,
        })
    return entries


class TestTrimCvExperience:
    """Tests for the dict-based trim_cv_experience function."""

    async def _compile_ok(self, page_count: int = 1):
        """Return a compile_fn that always reports the given page count."""
        async def _compile(experience: list[dict]) -> tuple[Path, int]:
            return Path("/tmp/cv.pdf"), page_count
        return _compile

    @pytest.mark.asyncio
    async def test_no_trim_needed(self):
        """CV already within page limit — no bullets removed."""
        exp = _make_dict_experience(
            ["Relevant bullet about Python"],
            ["Another bullet about PyTorch"],
        )
        compile_fn = await self._compile_ok(page_count=1)
        job = _make_job(requirements=["Python", "PyTorch"])

        result_exp, result = await cv_cutter.trim_cv_experience(
            exp, job, compile_fn, max_pages=2,
        )

        assert result.was_trimmed is False
        assert result.bullets_removed == 0
        assert len(result_exp) == 2
        assert len(result_exp[0]["bullets"]) == 1

    @pytest.mark.asyncio
    async def test_removes_lowest_scoring_bullet(self):
        """When over page limit, the lowest-scoring bullet is removed."""
        exp = _make_dict_experience(
            ["Relevant Python experience", "Also decent SQL work"],
            ["Irrelevant cooking hobby", "Another cooking detail"],
        )
        async def _compile_over(experience: list[dict]) -> tuple[Path, int]:
            total = sum(len(e.get("bullets", [])) for e in experience)
            pages = 2 if total > 2 else 1  # 1 page once down to 1 bullet/entry
            return Path("/tmp/cv.pdf"), pages
        job = _make_job(requirements=["Python"])

        result_exp, result = await cv_cutter.trim_cv_experience(
            exp, job, _compile_over, max_pages=1,
        )

        assert result.was_trimmed is True
        assert result.bullets_removed >= 1
        # The cooking bullet has no keyword overlap, should be removed before Python
        all_bullets = (
            result_exp[0].get("bullets", [])
            + result_exp[1].get("bullets", [])
        )
        assert "Irrelevant cooking hobby" not in all_bullets

    @pytest.mark.asyncio
    async def test_protects_minimum_bullets(self):
        """Never remove the last bullet from any entry."""
        exp = _make_dict_experience(
            ["Only bullet in role 1"],
            ["Bullet A", "Bullet B"],
        )
        compile_fn = await self._compile_ok(page_count=3)  # Always over limit
        job = _make_job(requirements=["Python"])

        result_exp, result = await cv_cutter.trim_cv_experience(
            exp, job, compile_fn, max_pages=1,
        )

        # First entry protects its only bullet
        assert len(result_exp[0]["bullets"]) >= 1

    @pytest.mark.asyncio
    async def test_cover_reference_protects_bullet(self):
        """Bullets referenced in cover text get a score boost."""
        exp = _make_dict_experience(
            ["Important Python skill used in project X", "Secondary note about setup"],
            ["Less relevant detail about setup", "Minor side comment"],
        )
        cover_text = "Used in project X"
        async def _compile_cover(experience: list[dict]) -> tuple[Path, int]:
            total = sum(len(e.get("bullets", [])) for e in experience)
            pages = 2 if total > 2 else 1
            return Path("/tmp/cv.pdf"), pages
        job = _make_job(requirements=["Python"])

        result_exp, result = await cv_cutter.trim_cv_experience(
            exp, job, _compile_cover, cover_text=cover_text, max_pages=1,
        )

        assert result.was_trimmed is True
        # The bullet referenced in cover letter should survive
        all_remaining = (
            result_exp[0].get("bullets", [])
            + result_exp[1].get("bullets", [])
        )
        assert "Important Python skill used in project X" in all_remaining

    @pytest.mark.asyncio
    async def test_exhausted_trim_returns_warning(self):
        """When trimming can't reach target pages, return what we have."""
        exp = _make_dict_experience(
            ["Bullet 1a", "Bullet 1b"],
            ["Bullet 2a", "Bullet 2b"],
        )
        # Always report more pages than limit
        async def _compile_exhausted(experience: list[dict]) -> tuple[Path, int]:
            return Path("/tmp/cv.pdf"), 5
        job = _make_job(requirements=["Python"])

        result_exp, result = await cv_cutter.trim_cv_experience(
            exp, job, _compile_exhausted, max_pages=1,
        )

        # Should have removed all removable bullets (down to min 1 each)
        assert result.bullets_removed == 2
        assert result.was_trimmed is True

    @pytest.mark.asyncio
    async def test_relevance_score_determines_order(self):
        """Higher-relevance bullets are kept over lower-relevance ones."""
        # Entry 0 has Python (high relevance) + SQL (no overlap = 0)
        # Entry 1 has two baking bullets (no overlap = 0)
        # After trimming, at least the Python-relevant bullet survives.
        exp = _make_dict_experience(
            ["Python expert with 5 years", "SQL database design"],
            ["Expert baker with pastry degree", "Decorative cake techniques"],
        )
        async def _compile(experience: list[dict]) -> tuple[Path, int]:
            total = sum(len(e.get("bullets", [])) for e in experience)
            pages = 2 if total > 2 else 1
            return Path("/tmp/cv.pdf"), pages
        job = _make_job(requirements=["Python", "PyTorch", "ML"])

        result_exp, result = await cv_cutter.trim_cv_experience(
            exp, job, _compile, max_pages=1,
        )

        assert result.was_trimmed is True
        all_bullets = (
            result_exp[0].get("bullets", [])
            + result_exp[1].get("bullets", [])
        )
        # Python-related bullet must survive
        assert "Python expert with 5 years" in all_bullets
        # At least one zero-relevance bullet was removed
        assert result.bullets_removed >= 1
