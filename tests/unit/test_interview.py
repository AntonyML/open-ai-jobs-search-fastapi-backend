"""Tests for the interview service.

Uses an in-memory SQLite database and mocks the LLM calls.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import (
    Application,
    CandidateProfile,
    InterviewPrep,
    JobPosting,
    RankEvaluation,
    StarExample,
    User,
)
from app.exceptions import LLMError, NotFoundError, ProfileIncompleteError
from app.services import interview


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
        # Create a test user
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
    """Create a sample candidate profile."""
    candidate = CandidateProfile(
        user_id="test-user-id",
        full_name="Jane Doe",
        location="Copenhagen, Denmark",
        email="jane@example.com",
        phone="+45 12345678",
        linkedin_url="https://linkedin.com/in/janedoe",
        github_url="https://github.com/janedoe",
        employment_status="Employed",
        constraints="No relocation",
        education=[
            {"degree": "MSc Computer Science", "institution": "DTU", "period": "2018-2020", "key_topics": "ML, Distributed Systems"}
        ],
        experience=[
            {
                "title": "Senior ML Engineer",
                "company": "Acme Corp",
                "start_date": "2020-01",
                "end_date": "Present",
                "location": "Copenhagen",
                "bullets": [
                    "Built ML pipeline processing 1M+ events/day",
                    "Reduced model latency by 40% via TensorRT optimization",
                    "Led team of 3 engineers",
                ],
            },
            {
                "title": "Data Scientist",
                "company": "Beta Inc",
                "start_date": "2018-06",
                "end_date": "2019-12",
                "location": "Aarhus",
                "bullets": [
                    "Developed recommendation system",
                    "Published 2 papers at top conferences",
                ],
            },
        ],
        projects=[
            {"name": "Open Source ML Library", "description": "Contributor to popular ML library"}
        ],
        skills={
            "programming_ml": [
                {"language": "Python", "proficiency": "Expert", "frameworks": ["PyTorch", "TensorFlow", "scikit-learn"]},
                {"language": "SQL", "proficiency": "Advanced", "frameworks": []},
            ],
            "domain_expertise": ["Machine Learning", "NLP", "Recommendation Systems"],
            "software_tools": ["Docker", "Kubernetes", "AWS", "Git"],
        },
        publications=[
            {"authors": "Doe, J.", "year": "2021", "title": "Efficient Transformers", "journal": "NeurIPS", "doi": "10.xxxx/xxxx"}
        ],
        awards=[
            {"award": "Best Paper Award", "event": "ICML", "year": "2020"}
        ],
        references=[
            {"name": "John Smith", "title": "CTO", "company": "Acme Corp", "email": "john@acme.com"}
        ],
        profile_statement="ML engineer with 5+ years building production ML systems at scale.",
    )
    db_session.add(candidate)
    await db_session.commit()
    await db_session.refresh(candidate)
    return candidate


@pytest.fixture
async def sample_job(db_session, sample_candidate):
    """Create a sample job posting."""
    job = JobPosting(
        user_id="test-user-id",
        portal="linkedin",
        external_id="job-123",
        title="Senior Machine Learning Engineer",
        company="TechCorp",
        location="Copenhagen, Denmark",
        url="https://linkedin.com/jobs/123",
        posting_date="2026-07-10",
        deadline="2026-08-10",
        description="We are looking for a Senior ML Engineer to build scalable ML systems. Experience with PyTorch, Kubernetes, and AWS required. You will lead a team of 3-5 engineers.",
        requirements=[
            "5+ years ML engineering experience",
            "Expert in Python and PyTorch",
            "Experience with Kubernetes and AWS",
            "Team leadership experience",
            "Strong communication skills",
        ],
        employment_type="full-time",
        language="en",
        status="ranked",
        rank_score=83.0,
        rank_verdict="Strong Fit",
        rank_date=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)
    return job


@pytest.fixture
async def sample_evaluation(db_session, sample_candidate, sample_job):
    """Create a sample rank evaluation."""
    evaluation = RankEvaluation(
        job_posting_id=sample_job.id,
        user_id="test-user-id",
        technical_score=85,
        experience_score=80,
        behavioral_score=75,
        career_score=90,
        overall_score=83,
        verdict="Strong Fit",
        location_status="PASS",
        deadline="2026-08-10",
        deadline_urgent=False,
        strengths=["Strong ML engineering background", "Team leadership experience", "Production ML at scale"],
        gaps=["No explicit Kubernetes certification", "Limited public cloud architecture experience"],
        missing_keywords=["Kubernetes", "AWS", "CI/CD"],
        red_flags=["Gap in employment 2017-2018"],
        language="en",
        raw_response={},
    )
    db_session.add(evaluation)
    await db_session.commit()
    await db_session.refresh(evaluation)
    return evaluation


@pytest.fixture
async def sample_application(db_session, sample_candidate, sample_job, sample_evaluation):
    """Create a sample application."""
    application = Application(
        user_id="test-user-id",
        job_posting_id=sample_job.id,
        rank_evaluation_id=sample_evaluation.id,
        tailored_experience=[
            {
                "title": "Senior ML Engineer",
                "company": "Acme Corp",
                "start_date": "2020-01",
                "end_date": "Present",
                "location": "Copenhagen",
                "bullets": [
                    "Accomplished 40% reduction in model inference latency, as measured by p99 latency, by implementing TensorRT optimization and batching",
                    "Achieved processing of 1M+ events/day, measured by throughput metrics, by building scalable ML pipeline with PyTorch and Kubernetes",
                    "Led team of 5 engineers to deliver real-time fraud detection system processing 10K transactions/sec with <50ms latency",
                ],
            },
            {
                "title": "Data Scientist",
                "company": "Beta Inc",
                "start_date": "2018-06",
                "end_date": "2019-12",
                "location": "Aarhus",
                "bullets": [
                    "Increased recommendation click-through rate by 15%, measured via A/B test, by adding collaborative filtering signals to ranking model",
                    "Published 2 papers at top conferences, demonstrating research impact, by conducting novel NLP research",
                ],
            },
        ],
        incorporated_keywords=[
            {"keyword": "Kubernetes", "where_incorporated": "Senior ML Engineer at Acme Corp, bullet 2", "original_context": "Required in job posting: Kubernetes"},
            {"keyword": "AWS", "where_incorporated": "Senior ML Engineer at Acme Corp, bullet 2", "original_context": "Required in job posting: AWS"},
        ],
        addressed_red_flags=["Gap in employment 2017-2018"],
        cv_tex_path="/tmp/cv.tex",
        cv_pdf_path="/tmp/cv.pdf",
        cover_letter_tex_path="/tmp/cover.tex",
        cover_letter_pdf_path="/tmp/cover.pdf",
        cv_compiled=True,
        cv_pages=2,
        cover_letter_compiled=True,
        cover_letter_pages=1,
        cv_template="moderncv-banking",
        cover_letter_template="cover-cls",
        language="en",
    )
    db_session.add(application)
    await db_session.commit()
    await db_session.refresh(application)
    return application


@pytest.fixture
async def sample_star_examples(db_session, sample_candidate):
    """Create sample STAR examples."""
    examples = [
        StarExample(
            candidate_id=sample_candidate.id,
            title="ML Pipeline Optimization",
            skill_demonstrated="ML Engineering",
            situation="Slow data pipeline processing 1M+ events/day with high latency",
            task="Reduce end-to-end latency by 50% while maintaining throughput",
            action="Rewrote batch processing using PyTorch DataLoader with multiprocessing, implemented TensorRT optimization for inference, added Redis caching layer",
            result="Achieved 40% latency reduction (p99 from 200ms to 120ms), maintained 1M+ events/day throughput, saved $50K/month in compute costs",
            use_for=["technical challenge", "performance optimization", "ML engineering"],
        ),
        StarExample(
            candidate_id=sample_candidate.id,
            title="Team Leadership",
            skill_demonstrated="Leadership",
            situation="Team of 3 engineers struggling with delivery velocity and code quality",
            task="Improve team delivery and establish engineering best practices",
            action="Introduced code review process, weekly tech talks, sprint planning with clear definitions of done, mentored junior engineers 1:1",
            result="Team velocity increased 30%, bug rate dropped 50%, 2 junior engineers promoted within 12 months",
            use_for=["leadership", "team management", "mentoring"],
        ),
        StarExample(
            candidate_id=sample_candidate.id,
            title="Recommendation System",
            skill_demonstrated="ML Research",
            situation="Existing recommendation system had low click-through rate",
            task="Improve recommendation relevance and CTR",
            action="Added collaborative filtering signals, implemented A/B testing framework, ran 5 experiments over 3 months",
            result="Increased CTR by 15%, published 2 papers at top conferences, system deployed to production serving 10M+ users",
            use_for=["technical challenge", "research", "A/B testing", "recommendation systems"],
        ),
    ]
    for ex in examples:
        db_session.add(ex)
    await db_session.commit()
    for ex in examples:
        await db_session.refresh(ex)
    return examples


# ── Helper: mock LLM ────────────────────────────────────────────────


def mock_company_research():
    return interview.CompanyResearchLLMOutput(
        mission="To democratize AI for enterprises",
        values=["Innovation", "Transparency", "Customer obsession"],
        recent_news=[
            {"title": "TechCorp launches new AI platform", "url": "https://techcorp.com/news/1", "date": "2026-06-15"},
            {"title": "TechCorp raises $50M Series B", "url": "https://techcrunch.com/2026/05/01", "date": "2026-05-01"},
        ],
        products=["AI Platform", "MLOps Tools", "AutoML"],
        team_structure="Engineering org of 50, split into platform, ML, and product teams",
        growth_signals=["Hiring 20 engineers this quarter", "Expanding to EU market"],
        red_flags=["Recent layoffs in sales team"],
    )


def mock_likely_questions():
    return interview.LikelyQuestionsLLMOutput(
        questions=[
            interview.LikelyQuestionOut(
                question="Walk me through your experience building production ML pipelines",
                source="requirements",
                priority="high",
            ),
            interview.LikelyQuestionOut(
                question="How do you handle model deployment and monitoring at scale?",
                source="requirements",
                priority="high",
            ),
            interview.LikelyQuestionOut(
                question="Tell me about a time you led a team through a technical challenge",
                source="gaps",
                priority="high",
            ),
            interview.LikelyQuestionOut(
                question="Why TechCorp specifically?",
                source="stage",
                priority="medium",
            ),
            interview.LikelyQuestionOut(
                question="What's your experience with Kubernetes and AWS?",
                source="missing_keywords",
                priority="high",
            ),
        ]
    )


def mock_star_mapping():
    return interview.StarMappingLLMOutput(
        mappings=[
            interview.StarMappingOut(
                question="Walk me through your experience building production ML pipelines",
                star_example_id="star-1",
                star_example_title="ML Pipeline Optimization",
            ),
            interview.StarMappingOut(
                question="How do you handle model deployment and monitoring at scale?",
                star_example_id="star-1",
                star_example_title="ML Pipeline Optimization",
            ),
            interview.StarMappingOut(
                question="Tell me about a time you led a team through a technical challenge",
                star_example_id="star-2",
                star_example_title="Team Leadership",
            ),
        ]
    )


def mock_new_star_drafts():
    return interview.NewStarDraftsLLMOutput(
        drafts=[
            interview.NewStarDraftOut(
                question="Why TechCorp specifically?",
                draft_situation="Interviewer asks about motivation for this specific company",
                draft_task="Articulate genuine interest aligned with company mission and recent news",
                draft_action="Reference TechCorp's mission to democratize AI, mention recent Series B and AI platform launch, connect to candidate's passion for production ML at scale",
                draft_result="Demonstrates researched interest and alignment with company direction",
            ),
            interview.NewStarDraftOut(
                question="What's your experience with Kubernetes and AWS?",
                draft_situation="Job requires Kubernetes and AWS experience which are missing keywords",
                draft_task="Honestly address gap while showing adjacent experience",
                draft_action="Acknowledge no production K8s/AWS cert, highlight Docker experience, on-prem cluster management, express eagerness to learn, mention self-study of EKS",
                draft_result="Shows self-awareness, adjacent skills, and growth mindset",
            ),
        ]
    )


def mock_consistency_brief():
    return interview.ConsistencyBriefLLMOutput(
        claims=[
            interview.ConsistencyBriefOut(
                claim="40% latency reduction via TensorRT optimization",
                source="cv",
                why_probed="Specific metric — interviewer will ask for details on implementation",
            ),
            interview.ConsistencyBriefOut(
                claim="Led team of 5 engineers",
                source="cv",
                why_probed="Leadership claim — interviewer will probe management style and outcomes",
            ),
            interview.ConsistencyBriefOut(
                claim="Processing 1M+ events/day",
                source="cv",
                why_probed="Scale claim — interviewer will ask about architecture and bottlenecks",
            ),
        ]
    )


def mock_tough_questions():
    return interview.ToughQuestionsLLMOutput(
        questions=[
            interview.ToughQuestionOut(
                question="Why did you leave Acme Corp?",
                answer="I haven't left — I'm currently at Acme Corp. I'm exploring new opportunities because I want to work on larger-scale ML infrastructure (TechCorp's AI platform) and expand my cloud-native skills (Kubernetes/AWS) which aren't the focus in my current role.",
            ),
            interview.ToughQuestionOut(
                question="You don't have explicit Kubernetes certification or AWS production experience.",
                answer="You're right — I don't have a K8s cert or production AWS experience. However, I've managed Docker containers on on-prem Kubernetes clusters for 2 years, built CI/CD pipelines with GitLab, and have been self-studying EKS through AWS workshops. My PyTorch/TensorRT optimization work required deep infrastructure understanding. I'm eager to apply this foundation to cloud-native ML at TechCorp.",
            ),
            interview.ToughQuestionOut(
                question="Where do you see yourself in 5 years?",
                answer="I want to be leading ML platform teams that enable organizations to deploy ML reliably at scale. TechCorp's AI platform mission aligns perfectly — I'd grow from senior engineer to tech lead to engineering manager here, building the tools I've wished for as an ML practitioner.",
            ),
            interview.ToughQuestionOut(
                question="What's your biggest weakness?",
                answer="I tend to dive deep into technical details and can lose sight of the bigger product picture. I've been working on this by forcing myself to write 'business impact' summaries for every technical decision and regularly syncing with product managers to align on priorities.",
            ),
            interview.ToughQuestionOut(
                question="Why TechCorp specifically?",
                answer="Three reasons: 1) Your mission to democratize AI for enterprises matches my passion for production ML tooling. 2) Your recent Series B and AI platform launch show momentum I want to be part of. 3) The team structure (platform/ML/product split) is exactly the environment where I thrive — technical depth with product impact.",
            ),
        ]
    )


def mock_questions_to_ask():
    return interview.QuestionsToAskLLMOutput(
        questions=[
            interview.QuestionToAskOut(
                question="What does a typical week look like for a Senior ML Engineer on the platform team?",
                category="role",
                why_ask="Understand day-to-day responsibilities and team dynamics",
            ),
            interview.QuestionToAskOut(
                question="What's the biggest challenge the ML platform team is facing right now?",
                category="team",
                why_ask="Shows interest in real problems, reveals pain points you could help with",
            ),
            interview.QuestionToAskOut(
                question="How does the team stay current with new ML tools and frameworks?",
                category="tech",
                why_ask="Reveals learning culture and technical autonomy",
            ),
            interview.QuestionToAskOut(
                question="How would you describe the team culture and leadership style?",
                category="culture",
                why_ask="Final round — last chance to detect deal-breakers in management style",
            ),
            interview.QuestionToAskOut(
                question="What does success look like in the first 6 months for this role?",
                category="role",
                why_ask="Clarifies expectations and shows outcome orientation",
            ),
        ]
    )


def mock_logistics():
    return interview.LogisticsLLMOutput(
        date="2026-07-20",
        format="video",
        interviewer_names=["Sarah Chen (Engineering Manager)", "Mike Torres (Senior ML Engineer)"],
        phone_video_tips=[
            "Test your camera/microphone 15 minutes before",
            "Have a glass of water nearby",
            "Smile when speaking — it changes your tone",
            "It's OK to take 5 seconds to think before answering",
        ],
    )


# ── Tests ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_interview_prep_basic(db_session, sample_candidate, sample_job, sample_application, sample_evaluation, sample_star_examples):
    """execute_interview_prep generates a complete interview prep pack."""
    with patch("app.services.interview.llm_completion_structured") as mock_llm:
        # Mock all LLM calls in sequence
        mock_llm.side_effect = [
            mock_company_research(),      # company research
            mock_likely_questions(),      # likely questions
            mock_star_mapping(),          # STAR mapping
            mock_new_star_drafts(),       # new STAR drafts
            mock_consistency_brief(),     # consistency brief
            mock_tough_questions(),       # tough questions
            mock_questions_to_ask(),      # questions to ask
            mock_logistics(),             # logistics
        ]

        prep = await interview.execute_interview_prep(
            db=db_session,
            user_id="test-user-id",
            application_id=sample_application.id,
            stage="technical",
            interview_date="2026-07-20",
            interview_format="video",
            interviewer_names=["Sarah Chen (Engineering Manager)", "Mike Torres (Senior ML Engineer)"],
        )

    assert prep.id is not None
    assert prep.application_id == sample_application.id
    assert prep.user_id == "test-user-id"
    assert prep.stage == "technical"
    assert prep.interview_date == "2026-07-20"
    assert prep.interview_format == "video"
    assert len(prep.interviewer_names) == 2

    # Check company research
    assert prep.company_research is not None
    assert prep.company_research["mission"] == "To democratize AI for enterprises"
    assert len(prep.company_research["recent_news"]) == 2

    # Check conversation hooks
    assert len(prep.conversation_hooks) > 0

    # Check likely questions
    assert len(prep.likely_questions) == 5
    assert prep.likely_questions[0]["priority"] == "high"

    # Check STAR mapping
    assert len(prep.star_mapping) == 3

    # Check new STAR drafts
    assert len(prep.new_star_drafts) == 2

    # Check consistency brief
    assert len(prep.consistency_brief) == 3

    # Check tough questions
    assert len(prep.tough_questions) == 5

    # Check questions to ask
    assert len(prep.questions_to_ask) == 5

    # Check logistics
    assert prep.logistics is not None
    assert prep.logistics["format"] == "video"
    assert len(prep.logistics["phone_video_tips"]) == 4


@pytest.mark.asyncio
async def test_execute_interview_prep_application_not_found(db_session):
    """execute_interview_prep raises NotFoundError for non-existent application."""
    with pytest.raises(NotFoundError):
        await interview.execute_interview_prep(
            db=db_session,
            user_id="test-user-id",
            application_id="nonexistent-id",
            stage="technical",
        )


@pytest.mark.asyncio
async def test_execute_interview_prep_profile_incomplete(db_session):
    """execute_interview_prep raises ProfileIncompleteError when candidate profile missing."""
    # Create a user and application but no candidate profile
    user = User(
        id="user-no-profile-interview",
        email="noprofile-interview@example.com",
        hashed_password="fakehash",
        full_name="No Profile User",
    )
    db_session.add(user)
    await db_session.commit()

    job = JobPosting(
        user_id="user-no-profile-interview",
        portal="linkedin",
        external_id="job-789",
        title="Senior ML Engineer",
        company="TechCorp",
        location="Copenhagen",
        url="https://linkedin.com/jobs/789",
        posting_date="2026-07-10",
        deadline="2026-08-10",
        description="ML job",
        requirements=["Python", "PyTorch"],
        employment_type="full-time",
        language="en",
        status="ranked",
        rank_score=80.0,
        rank_verdict="Strong Fit",
        rank_date=datetime.now(timezone.utc),
    )
    db_session.add(job)
    await db_session.commit()
    await db_session.refresh(job)

    evaluation = RankEvaluation(
        job_posting_id=job.id,
        user_id="user-no-profile-interview",
        technical_score=80,
        experience_score=75,
        behavioral_score=70,
        career_score=85,
        overall_score=80,
        verdict="Strong Fit",
        location_status="PASS",
        deadline="2026-08-10",
        deadline_urgent=False,
        strengths=["ML experience"],
        gaps=["No Kubernetes"],
        missing_keywords=["Kubernetes"],
        red_flags=[],
        language="en",
        raw_response={},
    )
    db_session.add(evaluation)
    await db_session.commit()
    await db_session.refresh(evaluation)

    application = Application(
        user_id="user-no-profile-interview",
        job_posting_id=job.id,
        rank_evaluation_id=evaluation.id,
        tailored_experience=[],
        cv_tex_path="/tmp/cv.tex",
        cv_pdf_path="/tmp/cv.pdf",
        cover_letter_tex_path="/tmp/cover.tex",
        cover_letter_pdf_path="/tmp/cover.pdf",
        cv_compiled=True,
        cv_pages=2,
        cover_letter_compiled=True,
        cover_letter_pages=1,
        cv_template="moderncv-banking",
        cover_letter_template="cover-cls",
        language="en",
    )
    db_session.add(application)
    await db_session.commit()
    await db_session.refresh(application)

    with patch("app.services.interview.llm_completion_structured"):
        with pytest.raises(ProfileIncompleteError):
            await interview.execute_interview_prep(
                db=db_session,
                user_id="user-no-profile-interview",
                application_id=application.id,
                stage="technical",
            )


@pytest.mark.asyncio
async def test_execute_interview_prep_llm_error(db_session, sample_candidate, sample_job, sample_application, sample_evaluation, sample_star_examples):
    """execute_interview_prep raises LLMError when LLM call fails."""
    with patch("app.services.interview.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = LLMError("LLM timeout")

        with pytest.raises(LLMError):
            await interview.execute_interview_prep(
                db=db_session,
                user_id="test-user-id",
                application_id=sample_application.id,
                stage="technical",
            )


@pytest.mark.asyncio
async def test_get_interview_prep(db_session, sample_candidate, sample_job, sample_application, sample_evaluation, sample_star_examples):
    """get_interview_prep returns the prep by ID."""
    # First create a prep
    with patch("app.services.interview.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_company_research(),
            mock_likely_questions(),
            mock_star_mapping(),
            mock_new_star_drafts(),
            mock_consistency_brief(),
            mock_tough_questions(),
            mock_questions_to_ask(),
            mock_logistics(),
        ]

        prep = await interview.execute_interview_prep(
            db=db_session,
            user_id="test-user-id",
            application_id=sample_application.id,
            stage="technical",
        )

    # Now fetch it
    fetched = await interview.get_interview_prep(db_session, prep.id, "test-user-id")
    assert fetched.id == prep.id
    assert fetched.stage == "technical"


@pytest.mark.asyncio
async def test_get_interview_prep_not_found(db_session):
    """get_interview_prep raises NotFoundError for non-existent prep."""
    with pytest.raises(NotFoundError):
        await interview.get_interview_prep(db_session, "nonexistent-id", "test-user-id")


@pytest.mark.asyncio
async def test_get_interview_prep_wrong_user(db_session, sample_candidate, sample_job, sample_application, sample_evaluation, sample_star_examples):
    """get_interview_prep raises NotFoundError when prep belongs to another user."""
    with patch("app.services.interview.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_company_research(),
            mock_likely_questions(),
            mock_star_mapping(),
            mock_new_star_drafts(),
            mock_consistency_brief(),
            mock_tough_questions(),
            mock_questions_to_ask(),
            mock_logistics(),
        ]

        prep = await interview.execute_interview_prep(
            db=db_session,
            user_id="test-user-id",
            application_id=sample_application.id,
            stage="technical",
        )

    with pytest.raises(NotFoundError):
        await interview.get_interview_prep(db_session, prep.id, "other-user-id")


@pytest.mark.asyncio
async def test_list_interview_preps(db_session, sample_candidate, sample_job, sample_application, sample_evaluation, sample_star_examples):
    """list_interview_preps returns preps for the user."""
    # Each execute_interview_prep makes 8 LLM calls, so 3 preps = 24 calls
    mock_responses = [
        mock_company_research(),
        mock_likely_questions(),
        mock_star_mapping(),
        mock_new_star_drafts(),
        mock_consistency_brief(),
        mock_tough_questions(),
        mock_questions_to_ask(),
        mock_logistics(),
    ] * 3  # Repeat for 3 preps

    with patch("app.services.interview.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = mock_responses

        # Create 3 preps
        for i in range(3):
            await interview.execute_interview_prep(
                db=db_session,
                user_id="test-user-id",
                application_id=sample_application.id,
                stage="technical",
            )

    preps = await interview.list_interview_preps(db_session, "test-user-id", limit=10)
    assert len(preps) == 3


# ── Prompt builder tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_build_company_research_prompt(sample_candidate, sample_job, sample_application):
    """build_company_research_prompt creates correct prompt structure."""
    messages = interview.build_company_research_prompt(sample_candidate, sample_job, sample_application)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert "GUARDRAIL" in messages[0]["content"]
    assert "Jane Doe" in messages[0]["content"]
    assert "Senior Machine Learning Engineer" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_likely_questions_prompt(sample_candidate, sample_job, sample_application, sample_evaluation):
    """build_likely_questions_prompt creates correct prompt structure."""
    messages = interview.build_likely_questions_prompt(
        sample_candidate, sample_job, sample_application, sample_evaluation, "technical"
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "GUARDRAIL" in messages[0]["content"]
    assert "technical" in messages[0]["content"]
    assert "Strong Fit" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_star_mapping_prompt(sample_candidate, sample_job, sample_star_examples):
    """build_star_mapping_prompt creates correct prompt structure."""
    likely_questions = [
        {"question": "Walk me through your ML pipeline experience", "source": "requirements", "priority": "high"},
        {"question": "Tell me about a leadership challenge", "source": "gaps", "priority": "high"},
    ]
    messages = interview.build_star_mapping_prompt(
        sample_candidate, sample_job, likely_questions, sample_star_examples
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "ML Pipeline Optimization" in messages[0]["content"]
    assert "Team Leadership" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_new_star_drafts_prompt(sample_candidate, sample_job):
    """build_new_star_drafts_prompt creates correct prompt structure."""
    unmapped = [
        {"question": "Why TechCorp?", "source": "stage", "priority": "medium"},
        {"question": "Kubernetes experience?", "source": "missing_keywords", "priority": "high"},
    ]
    messages = interview.build_new_star_drafts_prompt(sample_candidate, sample_job, unmapped)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "GUARDRAIL" in messages[0]["content"]
    assert "Why TechCorp?" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_consistency_brief_prompt(sample_application):
    """build_consistency_brief_prompt creates correct prompt structure."""
    messages = interview.build_consistency_brief_prompt(sample_application)

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "GUARDRAIL" in messages[0]["content"]
    assert "40% reduction" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_tough_questions_prompt(sample_candidate, sample_job, sample_evaluation):
    """build_tough_questions_prompt creates correct prompt structure."""
    messages = interview.build_tough_questions_prompt(
        sample_candidate, sample_job, sample_evaluation, "technical"
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "GUARDRAIL" in messages[0]["content"]
    assert "Why did you leave" in messages[0]["content"]
    assert "You don't have" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_questions_to_ask_prompt(sample_candidate, sample_job):
    """build_questions_to_ask_prompt creates correct prompt structure."""
    company_research = {
        "mission": "Democratize AI",
        "values": ["Innovation"],
        "recent_news": [{"title": "Launch"}],
        "products": ["AI Platform"],
        "team_structure": "50 engineers",
        "growth_signals": ["Hiring"],
        "red_flags": [],
    }
    messages = interview.build_questions_to_ask_prompt(
        sample_candidate, sample_job, company_research, "technical"
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "Democratize AI" in messages[0]["content"]


@pytest.mark.asyncio
async def test_build_logistics_prompt():
    """build_logistics_prompt creates correct prompt structure."""
    messages = interview.build_logistics_prompt(
        "technical", "video", "2026-07-20", ["Sarah Chen", "Mike Torres"]
    )

    assert len(messages) == 2
    assert messages[0]["role"] == "system"
    assert "technical" in messages[0]["content"]
    assert "video" in messages[0]["content"]


# ── Helper function tests ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_extract_conversation_hooks():
    """_extract_conversation_hooks extracts hooks from company research."""
    company_research = {
        "recent_news": [
            {"title": "TechCorp launches AI platform", "url": "https://example.com/1"},
            {"title": "TechCorp raises $50M", "url": "https://example.com/2"},
        ],
        "growth_signals": ["Hiring 20 engineers", "Expanding to EU"],
        "products": ["AI Platform", "MLOps Tools"],
    }

    hooks = interview._extract_conversation_hooks(company_research)

    assert len(hooks) <= 5
    # Should have hooks from news, growth signals, and products
    topics = [h["topic"] for h in hooks]
    assert any("AI platform" in t for t in topics)
    assert any("Hiring 20 engineers" in t for t in topics)
    assert any("AI Platform" in t for t in topics)





# ── Mock interview tests ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_mock_interview_returns_first_question(db_session, sample_candidate, sample_job, sample_application, sample_evaluation, sample_star_examples):
    """start_mock_interview returns first question from prep pack."""
    # First create a prep pack with mock LLM
    with patch("app.services.interview.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_company_research(),
            mock_likely_questions(),
            mock_star_mapping(),
            mock_new_star_drafts(),
            mock_consistency_brief(),
            mock_tough_questions(),
            mock_questions_to_ask(),
            mock_logistics(),
        ]
        prep = await interview.execute_interview_prep(
            db=db_session,
            user_id="test-user-id",
            application_id=sample_application.id,
            stage="technical",
        )

    # Start mock interview
    result = await interview.start_mock_interview(
        db_session, "test-user-id", prep.id
    )

    assert result["prep_id"] == prep.id
    assert result["question_number"] == 1
    assert result["total_questions"] == 5
    assert result["is_complete"] is False
    assert result["feedback"] is None  # No feedback on first question
    assert len(result["transcript"]) == 1  # First interviewer question
    assert result["transcript"][0]["role"] == "interviewer"
    assert "ML pipeline" in result["question"].lower() or "building" in result["question"].lower()
    assert "Mock interview started" in result["message"]


@pytest.mark.asyncio
async def test_start_mock_interview_no_questions(db_session):
    """start_mock_interview handles prep pack with no likely questions."""
    # Create a prep pack directly (without LLM) that has empty likely_questions
    prep = InterviewPrep(
        user_id="test-user-id",
        application_id="dummy-app-id",
        stage="technical",
        likely_questions=[],
    )
    db_session.add(prep)
    await db_session.commit()
    await db_session.refresh(prep)

    result = await interview.start_mock_interview(
        db_session, "test-user-id", prep.id
    )

    assert result["is_complete"] is True
    assert "No questions available" in result["message"]


@pytest.mark.asyncio
async def test_submit_mock_answer_completes_last_question(db_session, sample_candidate, sample_job, sample_application, sample_evaluation, sample_star_examples):
    """submit_mock_answer returns feedback and marks complete when LLM returns __COMPLETE__."""
    # Create a prep pack with 1 question
    with patch("app.services.interview.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_company_research(),
            mock_likely_questions(),
            mock_star_mapping(),
            mock_new_star_drafts(),
            mock_consistency_brief(),
            mock_tough_questions(),
            mock_questions_to_ask(),
            mock_logistics(),
        ]
        prep = await interview.execute_interview_prep(
            db=db_session,
            user_id="test-user-id",
            application_id=sample_application.id,
            stage="technical",
        )

    # Mock the llm_completion call for mock interview feedback
    transcript = [
        {"role": "interviewer", "content": "Tell me about your experience with ML pipelines."}
    ]

    with patch("app.llm.adapter.llm_completion") as mock_completion:
        mock_completion.return_value = {
            "content": '{"feedback": "Great structured answer! You covered the key technologies well.", "next_question": "__COMPLETE__"}'
        }

        result = await interview.submit_mock_answer(
            db=db_session,
            user_id="test-user-id",
            prep_id=prep.id,
            user_answer="I built ML pipelines using PyTorch and Kubernetes.",
            prep=prep,
            transcript=transcript,
        )

    assert result["is_complete"] is True
    assert "Great structured answer" in result["feedback"]
    assert result["question"] == ""  # No next question
    assert result["prep_id"] == prep.id
    assert len(result["transcript"]) == 2  # interviewer + candidate (no next question since complete)


@pytest.mark.asyncio
async def test_submit_mock_answer_with_llm_error(db_session, sample_candidate, sample_job, sample_application, sample_evaluation, sample_star_examples):
    """submit_mock_answer handles LLM error gracefully (doesn't crash)."""
    with patch("app.services.interview.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_company_research(),
            mock_likely_questions(),
            mock_star_mapping(),
            mock_new_star_drafts(),
            mock_consistency_brief(),
            mock_tough_questions(),
            mock_questions_to_ask(),
            mock_logistics(),
        ]
        prep = await interview.execute_interview_prep(
            db=db_session,
            user_id="test-user-id",
            application_id=sample_application.id,
            stage="technical",
        )

    transcript = [
        {"role": "interviewer", "content": "Tell me about your experience."}
    ]

    # Mock LLM to raise an error
    with patch("app.llm.adapter.llm_completion") as mock_completion:
        mock_completion.side_effect = Exception("LLM timeout")

        result = await interview.submit_mock_answer(
            db=db_session,
            user_id="test-user-id",
            prep_id=prep.id,
            user_answer="My experience includes...",
            prep=prep,
            transcript=transcript,
        )

    # Should not crash — returns completion with fallback message
    assert result["is_complete"] is True
    assert result["feedback"] is not None
    assert "couldn't generate" in result["feedback"].lower() or "continue" in result["feedback"].lower()


@pytest.mark.asyncio
async def test_submit_mock_answer_saves_transcript_to_db(db_session, sample_candidate, sample_job, sample_application, sample_evaluation, sample_star_examples):
    """submit_mock_answer saves the transcript to prep.mock_transcript."""
    with patch("app.services.interview.llm_completion_structured") as mock_llm:
        mock_llm.side_effect = [
            mock_company_research(),
            mock_likely_questions(),
            mock_star_mapping(),
            mock_new_star_drafts(),
            mock_consistency_brief(),
            mock_tough_questions(),
            mock_questions_to_ask(),
            mock_logistics(),
        ]
        prep = await interview.execute_interview_prep(
            db=db_session,
            user_id="test-user-id",
            application_id=sample_application.id,
            stage="technical",
        )

    transcript = [
        {"role": "interviewer", "content": "Tell me about your experience."}
    ]

    with patch("app.llm.adapter.llm_completion") as mock_completion:
        mock_completion.return_value = {
            "content": '{"feedback": "Good answer!", "next_question": "__COMPLETE__"}'
        }

        await interview.submit_mock_answer(
            db=db_session,
            user_id="test-user-id",
            prep_id=prep.id,
            user_answer="My experience...",
            prep=prep,
            transcript=transcript,
        )

    # Check transcript was saved
    await db_session.refresh(prep)
    assert prep.mock_transcript is not None
    assert "INTERVIEWER" in prep.mock_transcript
    assert "CANDIDATE" in prep.mock_transcript
    assert "My experience" in prep.mock_transcript
