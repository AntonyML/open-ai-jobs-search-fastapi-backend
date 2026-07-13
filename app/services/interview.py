"""Interview service — prepares candidates for real interviews.

Implements the /interview workflow from the original repo:
1. Loads application context (job posting, submitted CV/cover letter, outcome.md)
2. Researches the company (interview-focused)
3. Builds prep pack: likely questions, STAR mapping, consistency brief, tough questions, questions to ask, logistics
4. Offers mock interview
5. Saves prep pack to database
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.settings import get_settings
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
from app.llm.adapter import llm_completion_structured
from app.schemas.interview import (
    CompanyResearchLLMOutput,
    CompanyResearchOut,
    ConversationHookLLMOutput,
    ConversationHookOut,
    LikelyQuestionsLLMOutput,
    LikelyQuestionOut,
    StarMappingLLMOutput,
    StarMappingOut,
    NewStarDraftsLLMOutput,
    NewStarDraftOut,
    ConsistencyBriefLLMOutput,
    ConsistencyBriefOut,
    ToughQuestionsLLMOutput,
    ToughQuestionOut,
    QuestionsToAskLLMOutput,
    QuestionToAskOut,
    LogisticsLLMOutput,
    LogisticsOut,
)

settings = get_settings()


# ── Guardrail constant ──────────────────────────────────────────────

INTERVIEW_GUARDRAIL = """
IMPORTANT GUARDRAIL: You are preparing a candidate for a real interview.
You MUST NEVER invent, hallucinate, or assume experience, titles, companies,
or skills that the candidate does not explicitly have in their profile.

Your role is to:
- Identify genuine likely questions based on the job posting and candidate profile
- Map existing STAR examples honestly — only link examples that truly demonstrate the competency
- Draft new STAR examples grounded STRICTLY in facts from the candidate profile
- Flag consistency issues: claims in the CV/cover letter that the interviewer will probe
- Customize tough questions with honest bridge answers (acknowledge gap, connect adjacent experience, show learning path)
- Never prepare an answer that invents experience the candidate doesn't have

The candidate must be able to defend every claim in the interview without backtracking.
"""


# ── Prompt builders ─────────────────────────────────────────────────


def _build_candidate_summary(candidate: CandidateProfile) -> str:
    """Build a concise candidate summary for prompts."""
    parts = []

    if candidate.full_name:
        parts.append(f"Name: {candidate.full_name}")
    if candidate.location:
        parts.append(f"Location: {candidate.location}")
    if candidate.profile_statement:
        parts.append(f"Profile: {candidate.profile_statement}")

    if candidate.education:
        edu_lines = []
        for edu in candidate.education[:2]:
            line = f"  - {edu.get('degree', 'Degree')} at {edu.get('institution', 'Institution')}"
            if edu.get("period"):
                line += f" ({edu['period']})"
            if edu.get("key_topics"):
                line += f" — {edu['key_topics']}"
            edu_lines.append(line)
        if edu_lines:
            parts.append("Education:\n" + "\n".join(edu_lines))

    if candidate.experience:
        exp_lines = []
        for exp in candidate.experience[:3]:
            line = f"  - {exp.get('title', 'Role')} at {exp.get('company', 'Company')}"
            if exp.get("start_date") or exp.get("end_date"):
                line += f" ({exp.get('start_date', '')}–{exp.get('end_date', 'Present')})"
            if exp.get("location"):
                line += f" [{exp['location']}]"
            if exp.get("bullets"):
                for bullet in exp["bullets"][:2]:
                    line += f"\n    • {bullet}"
            exp_lines.append(line)
        if exp_lines:
            parts.append("Experience:\n" + "\n".join(exp_lines))

    if candidate.skills:
        skill_parts = []
        if candidate.skills.get("programming_ml"):
            langs = [f"{s.get('language', '')} ({s.get('proficiency', '')})" for s in candidate.skills["programming_ml"]]
            skill_parts.append(f"Programming/ML: {', '.join(langs)}")
        if candidate.skills.get("domain_expertise"):
            skill_parts.append(f"Domain: {', '.join(candidate.skills['domain_expertise'])}")
        if candidate.skills.get("software_tools"):
            skill_parts.append(f"Tools: {', '.join(candidate.skills['software_tools'])}")
        if skill_parts:
            parts.append("Skills:\n" + "\n".join(f"  - {p}" for p in skill_parts))

    return "\n\n".join(parts) if parts else "Profile not yet completed."


def _build_job_summary(job: JobPosting) -> str:
    """Build a concise job summary for prompts."""
    parts = [
        f"Title: {job.title}",
        f"Company: {job.company or 'Not specified'}",
        f"Location: {job.location or 'Not specified'}",
    ]

    if job.posting_date:
        parts.append(f"Posted: {job.posting_date}")
    if job.deadline:
        parts.append(f"Deadline: {job.deadline}")
    if job.employment_type:
        parts.append(f"Type: {job.employment_type}")
    if job.language:
        parts.append(f"Language: {job.language}")

    if job.description:
        desc = job.description[:1500] + ("..." if len(job.description) > 1500 else "")
        parts.append(f"Description:\n{desc}")

    if job.requirements:
        reqs = "\n".join(f"  • {r}" for r in job.requirements[:8])
        parts.append(f"Requirements:\n{reqs}")

    return "\n\n".join(parts)


def _build_application_context(application: Application) -> str:
    """Build context from the submitted application (CV + cover letter)."""
    parts = []

    if application.tailored_experience:
        exp_lines = []
        for exp in application.tailored_experience:
            exp_lines.append(f"\n{exp.get('title', 'Role')} at {exp.get('company', 'Company')}")
            for bullet in exp.get("bullets", []):
                exp_lines.append(f"  • {bullet}")
        parts.append("Submitted CV Experience (tailored):\n" + "\n".join(exp_lines))

    if application.incorporated_keywords:
        kw_lines = [f"  - {k.get('keyword', '')}: {k.get('where_incorporated', '')}" for k in application.incorporated_keywords]
        parts.append("Incorporated Keywords:\n" + "\n".join(kw_lines))

    if application.addressed_red_flags:
        parts.append("Addressed Red Flags:\n" + "\n".join(f"  - {r}" for r in application.addressed_red_flags))

    return "\n\n".join(parts) if parts else "No application content available."


def build_company_research_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    application: Application,
) -> list[dict[str, str]]:
    """Build prompt for company research (interview-focused)."""
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)

    system_prompt = f"""{INTERVIEW_GUARDRAIL}

You are researching a company for a candidate's interview preparation.
Focus on information the candidate can naturally reference in conversation.

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}

RESEARCH TASKS:
1. Company mission, values, recent news (last 6 months)
2. Products/services, team structure, growth signals
3. Any red flags (layoffs, restructuring, culture issues)
4. 2-3 verified conversation hooks — specific, recent, verifiable topics the candidate can naturally reference

Return ONLY valid JSON matching the CompanyResearchLLMOutput schema.
"""

    user_prompt = "Research this company for interview preparation. Return structured JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_likely_questions_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    application: Application,
    evaluation: RankEvaluation | None,
    stage: str,
) -> list[dict[str, str]]:
    """Build prompt for likely interview questions."""
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)
    app_context = _build_application_context(application)

    eval_context = ""
    if evaluation:
        eval_context = f"""
RANK EVALUATION INSIGHTS:
- Overall fit: {evaluation.verdict} ({evaluation.overall_score}/100)
- Technical gaps: {', '.join(evaluation.gaps or [])}
- Missing keywords: {', '.join(evaluation.missing_keywords or [])}
- Red flags: {', '.join(evaluation.red_flags or [])}
- Strengths: {', '.join(evaluation.strengths or [])}
"""

    system_prompt = f"""{INTERVIEW_GUARDRAIL}

You are predicting likely interview questions for a specific stage.

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}

SUBMITTED APPLICATION:
{app_context}
{eval_context}

INTERVIEW STAGE: {stage}

QUESTION SOURCES (priority order):
1. Recorded feedback from earlier stages (outcome.md) — anything flagged, doubted, or unresolved will come back
2. Rank evaluation gaps — requirements where profile is weakest are likeliest probes
3. Job posting stated requirements — competency by competency
4. Stage type — phone screens get motivation/timeline; technical rounds get stack questions; final rounds get values/salary/reservations

For each question, specify source and priority (high/medium/low).
Return ONLY valid JSON matching LikelyQuestionsLLMOutput schema.
"""

    user_prompt = f"Generate likely interview questions for a {stage} interview. Return structured JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_star_mapping_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    likely_questions: list[dict[str, Any]],
    star_examples: list[StarExample],
) -> list[dict[str, str]]:
    """Build prompt for mapping STAR examples to likely questions."""
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)

    # Format existing STAR examples
    star_lines = []
    for se in star_examples:
        star_lines.append(f"""
ID: {se.id}
Title: {se.title}
Skill: {se.skill_demonstrated or 'N/A'}
Use for: {', '.join(se.use_for or [])}
Situation: {se.situation}
Task: {se.task}
Action: {se.action}
Result: {se.result}
""")
    star_text = "\n---\n".join(star_lines) if star_lines else "No STAR examples available."

    system_prompt = f"""{INTERVIEW_GUARDRAIL}

You are mapping existing STAR examples to likely interview questions.

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}

EXISTING STAR EXAMPLES:
{star_text}

LIKELY QUESTIONS:
{json.dumps(likely_questions, indent=2, ensure_ascii=False)}

TASK:
For each likely question, find the BEST matching STAR example from the list above.
Only match if the example genuinely demonstrates the competency being probed.
If no existing example covers a question, note it for a new draft.

Return ONLY valid JSON matching StarMappingLLMOutput schema.
"""

    user_prompt = "Map STAR examples to likely questions. Return structured JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_new_star_drafts_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    unmapped_questions: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Build prompt for drafting new STAR examples for unmapped questions."""
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)

    questions_text = "\n".join(f"- {q['question']}" for q in unmapped_questions)

    system_prompt = f"""{INTERVIEW_GUARDRAIL}

You are drafting NEW STAR examples for questions not covered by existing examples.

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}

UNMAPPED QUESTIONS:
{questions_text}

TASK:
For each question, draft a STAR answer grounded STRICTLY in facts from the candidate profile.
- Situation: Context from actual experience
- Task: Actual responsibility
- Action: Specific actions, tools, methods the candidate really used
- Result: Measurable outcomes from real experience

If the candidate has NO relevant experience for a question, draft an honest bridge answer:
- Acknowledge the gap
- Connect adjacent experience
- Show learning path

Return ONLY valid JSON matching NewStarDraftsLLMOutput schema.
"""

    user_prompt = "Draft new STAR examples for unmapped questions. Return structured JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_consistency_brief_prompt(
    application: Application,
) -> list[dict[str, str]]:
    """Build prompt for consistency brief — claims from CV/cover letter that interviewer will probe."""
    app_context = _build_application_context(application)

    system_prompt = f"""{INTERVIEW_GUARDRAIL}

You are identifying specific claims from the submitted CV and cover letter
that the interviewer is most likely to probe in depth.

SUBMITTED APPLICATION:
{app_context}

TASK:
List the specific claims (achievements, numbers, skills emphasized) that an interviewer
will likely ask about. For each, note the source (CV or cover letter) and why it will be probed.

Return ONLY valid JSON matching ConsistencyBriefLLMOutput schema.
"""

    user_prompt = "Generate consistency brief from submitted application. Return structured JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_tough_questions_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation | None,
    stage: str,
) -> list[dict[str, str]]:
    """Build prompt for customized tough questions."""
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)

    eval_context = ""
    if evaluation:
        eval_context = f"""
RANK EVALUATION:
- Overall fit: {evaluation.verdict} ({evaluation.overall_score}/100)
- Gaps: {', '.join(evaluation.gaps or [])}
- Missing keywords: {', '.join(evaluation.missing_keywords or [])}
- Red flags: {', '.join(evaluation.red_flags or [])}
"""

    system_prompt = f"""{INTERVIEW_GUARDRAIL}

You are customizing the standard tough interview questions for this specific application.

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}
{eval_context}

INTERVIEW STAGE: {stage}

STANDARD TOUGH QUESTIONS TO CUSTOMIZE:
1. "Why did you leave [previous company]?"
2. "You don't have [specific skill/experience]."
3. "Where do you see yourself in 5 years?"
4. "What's your biggest weakness?"
5. "Why this company specifically?"

TASK:
For each question, provide a customized answer that:
- Uses verified facts from the candidate profile
- For "You don't have X": acknowledge gap, connect adjacent experience, show learning path
- For "Why this company": use verified hooks from company research
- Never invents experience

Return ONLY valid JSON matching ToughQuestionsLLMOutput schema.
"""

    user_prompt = "Customize tough questions for this application. Return structured JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_questions_to_ask_prompt(
    candidate: CandidateProfile,
    job: JobPosting,
    company_research: dict[str, Any] | None,
    stage: str,
) -> list[dict[str, str]]:
    """Build prompt for questions the candidate should ask the interviewer."""
    candidate_summary = _build_candidate_summary(candidate)
    job_summary = _build_job_summary(job)

    research_text = ""
    if company_research:
        research_text = f"""
COMPANY RESEARCH:
- Mission: {company_research.get('mission', 'N/A')}
- Values: {', '.join(company_research.get('values', []))}
- Recent news: {', '.join([n.get('title', '') for n in company_research.get('recent_news', [])])}
- Products: {', '.join(company_research.get('products', []))}
- Team structure: {company_research.get('team_structure', 'N/A')}
- Growth signals: {', '.join(company_research.get('growth_signals', []))}
- Red flags: {', '.join(company_research.get('red_flags', []))}
"""

    system_prompt = f"""{INTERVIEW_GUARDRAIL}

You are selecting questions for the candidate to ask the interviewer.

CANDIDATE PROFILE:
{candidate_summary}

JOB POSTING:
{job_summary}
{research_text}

INTERVIEW STAGE: {stage}

QUESTION CATEGORIES:
- Role/team questions (screens)
- Tech/growth questions (technical rounds)
- Culture/leadership questions (final rounds — last chance to detect deal-breakers)

TASK:
Select 4-6 questions, customized to this application. Cut any question the research already answers publicly.
Each question must have a category and a 'why_ask' rationale.

Return ONLY valid JSON matching QuestionsToAskLLMOutput schema.
"""

    user_prompt = "Select questions to ask for this interview. Return structured JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


def build_logistics_prompt(
    stage: str,
    interview_format: str | None,
    interview_date: str | None,
    interviewer_names: list[str] | None,
) -> list[dict[str, str]]:
    """Build prompt for logistics advice."""
    system_prompt = f"""{INTERVIEW_GUARDRAIL}

You are providing interview logistics advice.

STAGE: {stage}
FORMAT: {interview_format or 'Not specified'}
DATE: {interview_date or 'Not specified'}
INTERVIEWERS: {', '.join(interviewer_names) if interviewer_names else 'Not specified'}

TASK:
Provide practical logistics advice including phone/video tips when relevant.
Return ONLY valid JSON matching LogisticsLLMOutput schema.
"""

    user_prompt = "Provide logistics advice for this interview. Return structured JSON."

    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]


# ── Main orchestration ──────────────────────────────────────────────


async def execute_interview_prep(
    db: AsyncSession,
    user_id: str,
    application_id: str,
    stage: str,
    interview_date: str | None = None,
    interview_format: str | None = None,
    interviewer_names: list[str] | None = None,
) -> InterviewPrep:
    """Execute the full interview preparation workflow.

    Args:
        db: Database session
        user_id: Authenticated user ID
        application_id: Application to prepare for
        stage: Interview stage (phone_screen, technical, case, final_round)
        interview_date: Optional YYYY-MM-DD
        interview_format: Optional phone, video, onsite
        interviewer_names: Optional list of names/titles

    Returns:
        The created InterviewPrep record
    """
    # 1. Load application + related data
    app_result = await db.execute(
        select(Application)
        .where(Application.id == application_id)
        .where(Application.user_id == user_id)
    )
    application = app_result.scalar_one_or_none()
    if application is None:
        raise NotFoundError("Application not found.")

    # 2. Load job posting
    job_result = await db.execute(
        select(JobPosting).where(JobPosting.id == application.job_posting_id)
    )
    job = job_result.scalar_one_or_none()
    if job is None:
        raise NotFoundError("Job posting not found.")

    # 3. Load candidate profile
    candidate_result = await db.execute(
        select(CandidateProfile).where(CandidateProfile.user_id == user_id)
    )
    candidate = candidate_result.scalar_one_or_none()
    if candidate is None:
        raise ProfileIncompleteError("Candidate profile not found. Run /setup first.")

    # 4. Load rank evaluation
    eval_result = await db.execute(
        select(RankEvaluation).where(RankEvaluation.job_posting_id == job.id)
    )
    evaluation = eval_result.scalar_one_or_none()

    # 5. Load STAR examples
    star_result = await db.execute(
        select(StarExample).where(StarExample.candidate_id == candidate.id)
    )
    star_examples = list(star_result.scalars().all())

    # 6. Company research
    company_research = await _do_company_research(candidate, job, application)

    # 7. Likely questions
    likely_questions = await _do_likely_questions(candidate, job, application, evaluation, stage)

    # 8. STAR mapping
    star_mapping = await _do_star_mapping(candidate, job, likely_questions, star_examples)

    # 9. New STAR drafts for unmapped questions
    mapped_question_texts = {m["question"] for m in star_mapping}
    unmapped = [q for q in likely_questions if q["question"] not in mapped_question_texts]
    new_star_drafts = await _do_new_star_drafts(candidate, job, unmapped)

    # 10. Consistency brief
    consistency_brief = await _do_consistency_brief(application)

    # 11. Tough questions
    tough_questions = await _do_tough_questions(candidate, job, evaluation, stage)

    # 12. Questions to ask
    questions_to_ask = await _do_questions_to_ask(candidate, job, company_research, stage)

    # 13. Logistics
    logistics = await _do_logistics(stage, interview_format, interview_date, interviewer_names)

    # 14. Conversation hooks from company research
    conversation_hooks = _extract_conversation_hooks(company_research)

    # 15. Create InterviewPrep record
    prep = InterviewPrep(
        user_id=user_id,
        application_id=application_id,
        stage=stage,
        interview_date=interview_date,
        interview_format=interview_format,
        interviewer_names=interviewer_names,
        company_research=company_research,
        conversation_hooks=conversation_hooks,
        likely_questions=likely_questions,
        star_mapping=star_mapping,
        new_star_drafts=new_star_drafts,
        consistency_brief=consistency_brief,
        tough_questions=tough_questions,
        questions_to_ask=questions_to_ask,
        logistics=logistics,
        raw_response={},
    )
    db.add(prep)
    await db.commit()
    await db.refresh(prep)

    return prep


# ── Helper functions for each step ─────────────────────────────────


async def _do_company_research(
    candidate: CandidateProfile,
    job: JobPosting,
    application: Application,
) -> dict[str, Any]:
    """Research the company for interview preparation."""
    messages = build_company_research_prompt(candidate, job, application)

    try:
        result: CompanyResearchLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=CompanyResearchLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.3,
            max_tokens=2048,
        )
        return result.model_dump()
    except Exception as e:
        raise LLMError(f"Company research failed: {e}") from e


async def _do_likely_questions(
    candidate: CandidateProfile,
    job: JobPosting,
    application: Application,
    evaluation: RankEvaluation | None,
    stage: str,
) -> list[dict[str, Any]]:
    """Generate likely interview questions."""
    messages = build_likely_questions_prompt(candidate, job, application, evaluation, stage)

    try:
        result: LikelyQuestionsLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=LikelyQuestionsLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.3,
            max_tokens=2048,
        )
        return [q.model_dump() for q in result.questions]
    except Exception as e:
        raise LLMError(f"Likely questions generation failed: {e}") from e


async def _do_star_mapping(
    candidate: CandidateProfile,
    job: JobPosting,
    likely_questions: list[dict[str, Any]],
    star_examples: list[StarExample],
) -> list[dict[str, Any]]:
    """Map existing STAR examples to likely questions."""
    messages = build_star_mapping_prompt(candidate, job, likely_questions, star_examples)

    try:
        result: StarMappingLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=StarMappingLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.3,
            max_tokens=2048,
        )
        return [m.model_dump() for m in result.mappings]
    except Exception as e:
        raise LLMError(f"STAR mapping failed: {e}") from e


async def _do_new_star_drafts(
    candidate: CandidateProfile,
    job: JobPosting,
    unmapped_questions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Draft new STAR examples for unmapped questions."""
    if not unmapped_questions:
        return []

    messages = build_new_star_drafts_prompt(candidate, job, unmapped_questions)

    try:
        result: NewStarDraftsLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=NewStarDraftsLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.4,
            max_tokens=3000,
        )
        return [d.model_dump() for d in result.drafts]
    except Exception as e:
        raise LLMError(f"New STAR drafts failed: {e}") from e


async def _do_consistency_brief(application: Application) -> list[dict[str, Any]]:
    """Generate consistency brief from submitted application."""
    messages = build_consistency_brief_prompt(application)

    try:
        result: ConsistencyBriefLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=ConsistencyBriefLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.3,
            max_tokens=2048,
        )
        return [c.model_dump() for c in result.claims]
    except Exception as e:
        raise LLMError(f"Consistency brief failed: {e}") from e


async def _do_tough_questions(
    candidate: CandidateProfile,
    job: JobPosting,
    evaluation: RankEvaluation | None,
    stage: str,
) -> list[dict[str, Any]]:
    """Generate customized tough questions."""
    messages = build_tough_questions_prompt(candidate, job, evaluation, stage)

    try:
        result: ToughQuestionsLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=ToughQuestionsLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.4,
            max_tokens=2048,
        )
        return [q.model_dump() for q in result.questions]
    except Exception as e:
        raise LLMError(f"Tough questions failed: {e}") from e


async def _do_questions_to_ask(
    candidate: CandidateProfile,
    job: JobPosting,
    company_research: dict[str, Any] | None,
    stage: str,
) -> list[dict[str, Any]]:
    """Generate questions for the candidate to ask the interviewer."""
    messages = build_questions_to_ask_prompt(candidate, job, company_research, stage)

    try:
        result: QuestionsToAskLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=QuestionsToAskLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.3,
            max_tokens=2048,
        )
        return [q.model_dump() for q in result.questions]
    except Exception as e:
        raise LLMError(f"Questions to ask failed: {e}") from e


async def _do_logistics(
    stage: str,
    interview_format: str | None,
    interview_date: str | None,
    interviewer_names: list[str] | None,
) -> dict[str, Any]:
    """Generate logistics advice."""
    messages = build_logistics_prompt(stage, interview_format, interview_date, interviewer_names)

    try:
        result: LogisticsLLMOutput = await llm_completion_structured(
            messages=messages,
            output_schema=LogisticsLLMOutput,
            provider=settings.llm_default_provider,
            temperature=0.3,
            max_tokens=1024,
        )
        return result.model_dump()
    except Exception as e:
        raise LLMError(f"Logistics generation failed: {e}") from e


def _extract_conversation_hooks(company_research: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract verified conversation hooks from company research."""
    hooks = []

    # Recent news as hooks
    for news in company_research.get("recent_news", [])[:3]:
        hooks.append({
            "topic": news.get("title", ""),
            "source_url": news.get("url", ""),
            "why_relevant": f"Recent company news — shows you've done your homework",
        })

    # Growth signals as hooks
    for signal in company_research.get("growth_signals", [])[:2]:
        hooks.append({
            "topic": signal,
            "source_url": "",
            "why_relevant": "Company growth signal — natural conversation starter about team scaling",
        })

    # Products as hooks
    for product in company_research.get("products", [])[:2]:
        hooks.append({
            "topic": product,
            "source_url": "",
            "why_relevant": "Core product — can ask about roadmap, challenges, tech stack",
        })

    return hooks[:5]  # Max 5 hooks


# ── Query helpers ───────────────────────────────────────────────────


async def get_interview_prep(
    db: AsyncSession, prep_id: str, user_id: str
) -> InterviewPrep:
    """Get an interview prep by ID, verifying ownership."""
    result = await db.execute(
        select(InterviewPrep)
        .where(InterviewPrep.id == prep_id)
        .where(InterviewPrep.user_id == user_id)
    )
    prep = result.scalar_one_or_none()
    if prep is None:
        raise NotFoundError("Interview prep not found.")
    return prep


async def list_interview_preps(
    db: AsyncSession,
    user_id: str,
    limit: int = 20,
    offset: int = 0,
) -> list[InterviewPrep]:
    """List interview preps for a user."""
    result = await db.execute(
        select(InterviewPrep)
        .where(InterviewPrep.user_id == user_id)
        .order_by(InterviewPrep.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(result.scalars().all())