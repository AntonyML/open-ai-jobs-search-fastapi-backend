"""Tests for the upskill service.

Uses an in-memory SQLite database and mocks the LLM calls.
Never calls the real LLM.
"""

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Base,
    CandidateProfile,
    JobPosting,
    RankEvaluation,
    Upskill,
    User,
)
from app.exceptions import LLMError, NotFoundError, ProfileIncompleteError
from app.schemas.upskill import (
    GapHeatmapLLMOutput,
    GapHeatmapOut,
    HardSkillGapOut,
    HardSkillGapsLLMOutput,
    LearningPlanItemOut,
    LearningPlanLLMOutput,
    LearningResourceOut,
    SynthesizedGapOut,
    SynthesizedGapsLLMOutput,
    UpskillSummaryOut,
)
from app.services import upskill


# ── Fixtures ────────────────────────────────────────────────────────


@pytest.fixture
async def db_session():
    """In-memory SQLite database for each test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
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


@pytest.fixture
async def sample_candidate(db_session):
    """Candidate profile with realistic skills."""
    candidate = CandidateProfile(
        user_id="test-user-id",
        full_name="Jane Doe",
        location="Copenhagen, Denmark",
        email="jane@example.com",
        skills={
            "programming_ml": [
                {"language": "Python", "proficiency": "Expert", "frameworks": ["PyTorch", "TensorFlow", "scikit-learn"]},
                {"language": "SQL", "proficiency": "Advanced", "frameworks": []},
            ],
            "domain_expertise": ["Machine Learning", "NLP"],
            "software_tools": ["Docker", "Git", "AWS"],
        },
        experience=[
            {
                "title": "ML Engineer",
                "company": "Acme",
                "start_date": "2020-01",
                "end_date": "Present",
                "location": "Copenhagen",
                "bullets": ["Built ML pipelines"],
            }
        ],
        education=[
            {"degree": "MSc Computer Science", "institution": "DTU", "period": "2018-2020", "key_topics": "ML"}
        ],
    )
    db_session.add(candidate)
    await db_session.commit()
    await db_session.refresh(candidate)
    return candidate


@pytest.fixture
async def sample_job(db_session, sample_candidate):
    """A ranked job posting."""
    job = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="job-123",
        title="Senior ML Engineer",
        company="TechCorp",
        location="Copenhagen, Denmark",
        url="https://linkedin.com/jobs/123",
        requirements=[
            "Python, PyTorch",
            "Kubernetes, Helm",
            "AWS SageMaker",
            "CI/CD pipelines",
            "Team leadership",
        ],
        status="ranked",
        rank_score=82.0,
        rank_verdict="Strong Fit",
        rank_date=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest.fixture
async def sample_evaluation(db_session, sample_job):
    """Rank evaluation for the sample job."""
    evaluation = RankEvaluation(
        job_posting_id=sample_job.id,
        user_id="test-user-id",
        technical_score=85,
        experience_score=80,
        behavioral_score=75,
        career_score=90,
        overall_score=82,
        verdict="Strong Fit",
        location_status="PASS",
        language="en",
        raw_response={},
    )
    db_session.add(evaluation)
    await db_session.commit()
    await db_session.refresh(evaluation)
    return evaluation


# ── Mock LLM outputs ────────────────────────────────────────────────


def _mock_pass1():
    return HardSkillGapsLLMOutput(
        gaps=[
            HardSkillGapOut(skill="Kubernetes", priority="Critical", source_jobs=["job-1"], frequency=3, fit_weight=2.5),
            HardSkillGapOut(skill="CI/CD", priority="High", source_jobs=["job-1", "job-2"], frequency=2, fit_weight=1.8),
        ]
    )


def _mock_pass2():
    return SynthesizedGapsLLMOutput(
        gaps=[
            SynthesizedGapOut(skill="MLOps", type="tooling", priority="Critical", evidence="Kubernetes + CI/CD imply MLOps gap"),
            SynthesizedGapOut(skill="Cloud Architecture", type="domain", priority="High", evidence="AWS required in jobs"),
        ]
    )


def _mock_heatmap():
    return GapHeatmapLLMOutput(
        heatmap=[
            GapHeatmapOut(skill="Kubernetes", type="hard", priority="Critical", gap_source="Pass 1: 3/3 jobs"),
            GapHeatmapOut(skill="CI/CD", type="hard", priority="High", gap_source="Pass 1: 2/3 jobs"),
            GapHeatmapOut(skill="MLOps", type="tooling", priority="Critical", gap_source="Pass 2: LLM synthesis"),
            GapHeatmapOut(skill="Cloud Architecture", type="domain", priority="High", gap_source="Pass 2: LLM synthesis"),
        ]
    )


def _mock_learning_plan():
    return LearningPlanLLMOutput(
        plan=[
            LearningPlanItemOut(
                skill="Kubernetes",
                type="hard",
                priority="Critical",
                resources=[
                    LearningResourceOut(title="K8s Docs", url="https://kubernetes.io/docs", format="article", duration_hours=10, cost="free", quality_score=9),
                ],
                study_order=1,
                prerequisites=["Docker"],
                estimated_weeks=3,
            ),
            LearningPlanItemOut(
                skill="MLOps",
                type="tooling",
                priority="Critical",
                resources=[
                    LearningResourceOut(title="MLOps Fundamentals", url="https://coursera.org/mlops", format="course", duration_hours=15, cost="free", quality_score=8),
                ],
                study_order=2,
                prerequisites=["Kubernetes"],
                estimated_weeks=3,
            ),
        ]
    )


def _all_4_passes():
    return [_mock_pass1(), _mock_pass2(), _mock_heatmap(), _mock_learning_plan()]


# ── Core flow tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_upskill_aggregate_success(db_session, sample_candidate, sample_job, sample_evaluation):
    """execute_upskill aggregate mode runs all 4 passes and returns completed record."""
    with patch("app.services.upskill.llm_completion_structured", side_effect=_all_4_passes()):
        result = await upskill.execute_upskill(db=db_session, user_id="test-user-id", mode="aggregate")

    assert result.id is not None
    assert result.user_id == "test-user-id"
    assert result.candidate_id == sample_candidate.id
    assert result.status == "completed"
    assert result.mode == "aggregate"

    # Pass 1 stored
    assert result.hard_skill_gaps is not None
    assert len(result.hard_skill_gaps) == 2
    assert result.hard_skill_gaps[0]["skill"] == "Kubernetes"
    assert result.hard_skill_gaps[0]["priority"] == "Critical"
    assert result.hard_skill_gaps[0]["frequency"] == 3
    assert result.hard_skill_gaps[0]["fit_weight"] == 2.5

    # Pass 2 stored
    assert result.synthesized_gaps is not None
    assert len(result.synthesized_gaps) == 2
    assert result.synthesized_gaps[0]["skill"] == "MLOps"
    assert result.synthesized_gaps[0]["type"] == "tooling"

    # Heatmap stored
    assert result.gap_heatmap is not None
    assert len(result.gap_heatmap) == 4

    # Learning plan stored
    assert result.learning_plan is not None
    assert len(result.learning_plan) == 2
    assert result.learning_plan[0]["skill"] == "Kubernetes"
    assert result.learning_plan[0]["estimated_weeks"] == 3
    assert len(result.learning_plan[0]["resources"]) == 1
    assert result.learning_plan[0]["resources"][0]["title"] == "K8s Docs"


@pytest.mark.asyncio
async def test_execute_upskill_targeted_success(db_session, sample_candidate, sample_job, sample_evaluation):
    """execute_upskill targeted mode uses the specific job and stores target_job_posting_id."""
    with patch("app.services.upskill.llm_completion_structured", side_effect=_all_4_passes()):
        result = await upskill.execute_upskill(
            db=db_session,
            user_id="test-user-id",
            mode="targeted",
            target_job_posting_id=sample_job.id,
            target_job_url="https://linkedin.com/jobs/123",
        )

    assert result.status == "completed"
    assert result.mode == "targeted"
    assert result.target_job_posting_id == sample_job.id
    assert result.target_job_url == "https://linkedin.com/jobs/123"


@pytest.mark.asyncio
async def test_execute_upskill_profile_not_found(db_session):
    """execute_upskill raises ProfileIncompleteError when no candidate profile exists."""
    with pytest.raises(ProfileIncompleteError):
        await upskill.execute_upskill(db=db_session, user_id="no-such-user", mode="aggregate")


@pytest.mark.asyncio
async def test_execute_upskill_no_ranked_jobs(db_session, sample_candidate):
    """execute_upskill raises NotFoundError when no ranked jobs exist."""
    with pytest.raises(NotFoundError):
        await upskill.execute_upskill(db=db_session, user_id="test-user-id", mode="aggregate")


@pytest.mark.asyncio
async def test_execute_upskill_targeted_job_not_found(db_session, sample_candidate):
    """execute_upskill raises NotFoundError when target job posting doesn't exist."""
    with pytest.raises(NotFoundError):
        await upskill.execute_upskill(
            db=db_session,
            user_id="test-user-id",
            mode="targeted",
            target_job_posting_id="nonexistent-id",
        )


@pytest.mark.asyncio
async def test_execute_upskill_pass1_llm_error(db_session, sample_candidate, sample_job, sample_evaluation):
    """execute_upskill wraps LLM failure as LLMError and marks status=failed."""
    with patch("app.services.upskill.llm_completion_structured", side_effect=Exception("timeout")):
        with pytest.raises(LLMError):
            await upskill.execute_upskill(db=db_session, user_id="test-user-id", mode="aggregate")

    # Verify the DB record was marked failed
    from sqlalchemy import select
    result = await db_session.execute(select(Upskill).where(Upskill.user_id == "test-user-id"))
    record = result.scalar_one_or_none()
    assert record is not None
    assert record.status == "failed"
    assert "Pass 1" in record.error_message


# ── Query helper tests ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_upskill_by_id(db_session, sample_candidate, sample_job, sample_evaluation):
    """get_upskill returns the record by ID."""
    with patch("app.services.upskill.llm_completion_structured", side_effect=_all_4_passes()):
        created = await upskill.execute_upskill(db=db_session, user_id="test-user-id", mode="aggregate")

    fetched = await upskill.get_upskill(db_session, created.id, "test-user-id")
    assert fetched.id == created.id
    assert fetched.status == "completed"


@pytest.mark.asyncio
async def test_get_upskill_not_found(db_session):
    """get_upskill raises NotFoundError for a non-existent ID."""
    with pytest.raises(NotFoundError):
        await upskill.get_upskill(db_session, "nonexistent-id", "test-user-id")


@pytest.mark.asyncio
async def test_list_upskills(db_session, sample_candidate, sample_job, sample_evaluation):
    """list_upskills returns all records for the user, ordered by created_at desc."""
    # Create 3 upskill runs
    with patch("app.services.upskill.llm_completion_structured", side_effect=_all_4_passes() * 3):
        for _ in range(3):
            await upskill.execute_upskill(db=db_session, user_id="test-user-id", mode="aggregate")

    results = await upskill.list_upskills(db_session, "test-user-id", limit=10)
    assert len(results) == 3
    # All belong to test-user-id
    assert all(r.user_id == "test-user-id" for r in results)


# ── UpskillSummaryOut computed fields ───────────────────────────────


def test_upskill_summary_gaps_found():
    """UpskillSummaryOut.gaps_found counts hard + synthesized gaps."""
    record = Upskill(
        id="u1",
        user_id="test-user-id",
        candidate_id="c1",
        mode="aggregate",
        status="completed",
        hard_skill_gaps=[
            {"skill": "K8s", "type": "hard", "priority": "Critical", "source_jobs": [], "frequency": 2, "fit_weight": 1.5},
            {"skill": "CI/CD", "type": "hard", "priority": "High", "source_jobs": [], "frequency": 1, "fit_weight": 0.8},
        ],
        synthesized_gaps=[
            {"skill": "MLOps", "type": "tooling", "priority": "Critical", "source": "LLM synthesis", "evidence": "..."},
        ],
        gap_heatmap=[
            {"skill": "K8s", "type": "hard", "priority": "Critical", "gap_source": "Pass 1"},
            {"skill": "CI/CD", "type": "hard", "priority": "High", "gap_source": "Pass 1"},
            {"skill": "MLOps", "type": "tooling", "priority": "Critical", "gap_source": "Pass 2"},
        ],
        learning_plan=[
            {"skill": "K8s", "type": "hard", "priority": "Critical", "resources": [], "study_order": 1, "prerequisites": [], "estimated_weeks": 3},
            {"skill": "MLOps", "type": "tooling", "priority": "Critical", "resources": [], "study_order": 2, "prerequisites": [], "estimated_weeks": 3},
        ],
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )

    summary = UpskillSummaryOut(
        id=record.id,
        user_id=record.user_id,
        candidate_id=record.candidate_id,
        mode=record.mode,
        status=record.status,
        gaps_found=len(record.hard_skill_gaps or []) + len(record.synthesized_gaps or []),
        learning_plan_items=len(record.learning_plan or []),
        created_at=record.created_at,
    )

    assert summary.gaps_found == 3  # 2 hard + 1 synthesized
    assert summary.learning_plan_items == 2


# ── LLM usage accounting (gate → correlation_id → record_llm_usage) ──


@pytest.mark.asyncio
async def test_execute_upskill_records_llm_usage(
    db_session, sample_candidate, sample_job, sample_evaluation,
):
    """execute_upskill calls credits.record_llm_usage with the correlation_id when one is passed."""
    from unittest.mock import AsyncMock

    with (
        patch("app.services.upskill.llm_completion_structured", side_effect=_all_4_passes()),
        patch("app.services.upskill.credits.record_llm_usage", new=AsyncMock()) as mock_record,
    ):
        result = await upskill.execute_upskill(
            db=db_session,
            user_id="test-user-id",
            mode="aggregate",
            correlation_id="cid-upskill-test",
        )

    assert result.status == "completed"
    mock_record.assert_awaited_once()
    args, call_kwargs = mock_record.await_args
    assert args[1] == "cid-upskill-test"  # positional correlation_id
    assert call_kwargs["tokens_input"] >= 0
    assert call_kwargs["tokens_output"] >= 0
    assert call_kwargs["cost_usd_cents"] >= 0


@pytest.mark.asyncio
async def test_execute_upskill_without_correlation_id_skips_usage(
    db_session, sample_candidate, sample_job, sample_evaluation,
):
    """No correlation_id (e.g. admin bypass) → record_llm_usage is never called."""
    from unittest.mock import AsyncMock

    with (
        patch("app.services.upskill.llm_completion_structured", side_effect=_all_4_passes()),
        patch("app.services.upskill.credits.record_llm_usage", new=AsyncMock()) as mock_record,
    ):
        result = await upskill.execute_upskill(
            db=db_session, user_id="test-user-id", mode="aggregate"
        )

    assert result.status == "completed"
    mock_record.assert_not_awaited()


@pytest.mark.asyncio
async def test_upskill_threads_usage_sink_to_all_llm_passes(
    db_session, sample_candidate, sample_job, sample_evaluation,
):
    """The same usage sink dict flows into every llm_completion_structured call."""
    from unittest.mock import AsyncMock

    mock_llm = AsyncMock(side_effect=_all_4_passes())
    with patch("app.services.upskill.llm_completion_structured", mock_llm):
        await upskill.execute_upskill(
            db=db_session,
            user_id="test-user-id",
            mode="aggregate",
            correlation_id="cid-upskill-test",
        )

    assert mock_llm.await_count == 4
    sinks = [call.kwargs.get("usage") for call in mock_llm.await_args_list]
    assert all(s is not None for s in sinks)
    # All four passes share the same sink object so usage accumulates.
    assert len({id(s) for s in sinks}) == 1


@pytest.mark.asyncio
async def test_background_task_records_usage(
    db_session, sample_candidate, sample_job, sample_evaluation,
):
    """_execute_upskill_background records LLM usage on its own session with the correlation_id."""
    from unittest.mock import AsyncMock
    from unittest.mock import patch as _patch

    # Create a pending upskill record like the router does.
    record = Upskill(
        user_id="test-user-id",
        candidate_id=sample_candidate.id,
        mode="aggregate",
        status="pending",
    )
    db_session.add(record)
    await db_session.commit()
    await db_session.refresh(record)

    class _Ctx:
        def __init__(self, session):
            self._session = session

        async def __aenter__(self):
            return self._session

        async def __aexit__(self, *exc):
            return False

    with (
        _patch("app.services.upskill.llm_completion_structured", side_effect=_all_4_passes()),
        _patch("app.db.session.async_session_factory", return_value=_Ctx(db_session)),
        _patch("app.services.upskill.credits.record_llm_usage", new=AsyncMock()) as mock_record,
    ):
        await upskill._execute_upskill_background(record.id, correlation_id="cid-bg-test")

    mock_record.assert_awaited_once()
    assert mock_record.await_args.args[1] == "cid-bg-test"  # positional correlation_id

    # The record completed on its own session.
    from sqlalchemy import select
    result = await db_session.execute(select(Upskill).where(Upskill.id == record.id))
    refreshed = result.scalar_one_or_none()
    assert refreshed is not None
    assert refreshed.status == "completed"
