"""Fit calibration service — analyzes outcome data to correlate skills/keywords with success rates.

FASE 7: After enough outcomes are recorded, this service:
- Calculates conversion funnel metrics (application → interview → offer → hired)
- Extracts keywords from job postings and correlates them with interview/offer rates
- Identifies which skills, keywords, and patterns correlate with success vs rejection
- Generates actionable insights and recommendations

This is 100% deterministic — no LLM calls. All computation is done via
aggregation queries and simple statistics.
"""

from __future__ import annotations


from collections import Counter
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.db.models import Application, JobPosting, Outcome, RankEvaluation
from app.exceptions import NotFoundError
from app.schemas.outcome import (
    CalibrationInsight,
    CalibrationKeyword,
    CalibrationReport,
    FunnelMetrics,
)

from app.core.logging import get_logger, bind_context
logger = get_logger(__name__)

# ── Status classification ───────────────────────────────────────────

PROGRESS_STATUSES = {
    "interview_invited",
    "phone_screen_completed",
    "technical_completed",
    "case_completed",
    "final_round_completed",
    "offer_received",
}

POSITIVE_RESOLUTIONS = {"hired", "offer_declined"}
NEGATIVE_RESOLUTIONS = {"rejected", "no_response"}
NEUTRAL_RESOLUTIONS = {"interview_only", "withdrawn"}

ALL_RESOLUTIONS = POSITIVE_RESOLUTIONS | NEGATIVE_RESOLUTIONS | NEUTRAL_RESOLUTIONS


# ── Public entry point ──────────────────────────────────────────────


async def generate_calibration_report(
    db: AsyncSession,
    user_id: str,
) -> CalibrationReport:
    """Generate a full calibration report for the user."""
    with bind_context(pipeline_stage="calibration"):
        logger.info("Generating calibration report | user=%s", user_id)
        # 1. Load all outcomes with their related application + job posting + rank evaluation
        outcomes = await _load_outcomes_with_relations(db, user_id)
        if not outcomes:
            raise NotFoundError("No outcomes found. Record some application outcomes first.")

        # 2. Compute funnel metrics
        funnel = _compute_funnel(outcomes)

        # 3. Extract keywords from job postings and correlate with outcomes
        top_keywords, bottom_keywords = _analyze_keywords(outcomes)

        # 4. Generate insights
        insights = _generate_insights(funnel, top_keywords, bottom_keywords, outcomes)

        return CalibrationReport(
            funnel=funnel,
            top_keywords=top_keywords[:10],
            bottom_keywords=bottom_keywords[:10],
            insights=insights,
            data_points=len(outcomes),
        )


# ── Data loading ────────────────────────────────────────────────────


async def _load_outcomes_with_relations(
    db: AsyncSession,
    user_id: str,
) -> list[dict[str, Any]]:
    """Load all outcomes with eagerly loaded relations.

    Returns a list of structured dicts combining outcome + application + job + evaluation data.
    """
    result = await db.execute(
        select(Outcome)
        .where(Outcome.user_id == user_id)
        .options(
            selectinload(Outcome.application).selectinload(Application.job_posting),
            selectinload(Outcome.application).selectinload(Application.rank_evaluation),
        )
        .order_by(Outcome.created_at.desc())
    )
    outcomes = list(result.scalars().all())

    enriched = []
    for o in outcomes:
        app = o.application
        job = app.job_posting if app else None
        evaluation = app.rank_evaluation if app else None

        enriched.append({
            "outcome": o,
            "application": app,
            "job": job,
            "evaluation": evaluation,
        })
    return enriched


# ── Funnel computation ──────────────────────────────────────────────


def _compute_funnel(
    enriched_outcomes: list[dict[str, Any]],
) -> FunnelMetrics:
    """Compute conversion funnel metrics from outcome data.

    Takes the LATEST outcome per unique application to avoid counting
    multiple progress updates for the same application.
    """
    # Group outcomes by application_id, keep the latest status
    app_statuses: dict[str, str] = {}
    for item in enriched_outcomes:
        app_id = item["outcome"].application_id
        status = item["outcome"].status
        # Keep the latest (first in list since ordered desc)
        if app_id not in app_statuses:
            app_statuses[app_id] = status

    total = len(app_statuses)
    statuses = list(app_statuses.values())

    interviewed = sum(
        1 for s in statuses
        if s in PROGRESS_STATUSES or s in POSITIVE_RESOLUTIONS or s == "interview_only"
    )
    offered = sum(
        1 for s in statuses
        if s in {"offer_received", "hired", "offer_declined"}
    )
    hired = sum(1 for s in statuses if s == "hired")
    rejected = sum(1 for s in statuses if s in NEGATIVE_RESOLUTIONS)
    no_response = sum(1 for s in statuses if s == "no_response")
    withdrawn = sum(1 for s in statuses if s == "withdrawn")
    in_progress = sum(
        1 for s in statuses
        if s in PROGRESS_STATUSES and s not in {"offer_received"}
    )

    funnel = FunnelMetrics(
        total_applications=total,
        interviews=interviewed,
        offers=offered,
        hired=hired,
        rejected=rejected,
        no_response=no_response,
        withdrawn=withdrawn,
        in_progress=in_progress,
    )

    # Conversion rates
    if total > 0:
        funnel.application_to_interview_pct = round(interviewed / total * 100, 1)
    if interviewed > 0:
        funnel.interview_to_offer_pct = round(offered / interviewed * 100, 1)
    if offered > 0:
        funnel.offer_to_hired_pct = round(hired / offered * 100, 1)
    if total > 0:
        funnel.overall_success_pct = round(hired / total * 100, 1)

    return funnel


# ── Keyword analysis ────────────────────────────────────────────────


def _analyze_keywords(
    enriched_outcomes: list[dict[str, Any]],
) -> tuple[list[CalibrationKeyword], list[CalibrationKeyword]]:
    """Analyze which keywords/skills correlate with interview and hire success.

    For each keyword found across job postings, calculate:
    - How many applications had this keyword
    - What % of those got interviews
    - What % of those got hired
    - Average rank score for jobs with this keyword

    Returns:
        (top_keywords, bottom_keywords) sorted by correlation strength
    """
    # Collect keyword → list of (had_interview, had_offer, had_hire, score)
    keyword_data: dict[str, list[dict[str, Any]]] = {}

    for item in enriched_outcomes:
        job = item["job"]
        evaluation = item["evaluation"]
        status = item["outcome"].status

        if not job or not job.description:
            continue

        # Extract keywords from job description (simple word extraction)
        # Focus on technical terms and capitalized phrases
        keywords = _extract_job_keywords(job)

        # Determine outcome flags for this application
        had_interview = status in PROGRESS_STATUSES or status in POSITIVE_RESOLUTIONS or status == "interview_only"
        had_offer = status in {"offer_received", "hired", "offer_declined"}
        had_hire = status == "hired"
        score = evaluation.overall_score if evaluation else 50

        for kw in keywords:
            if kw not in keyword_data:
                keyword_data[kw] = []
            keyword_data[kw].append({
                "interview": had_interview,
                "offer": had_offer,
                "hire": had_hire,
                "score": score,
            })

    if not keyword_data:
        return [], []

    # Calculate metrics per keyword
    keywords_analyzed: list[CalibrationKeyword] = []
    for kw, data in keyword_data.items():
        total = len(data)
        if total < 2:  # Skip keywords that appear in only 1 job
            continue

        interview_count = sum(1 for d in data if d["interview"])
        hire_count = sum(1 for d in data if d["hire"])
        avg_score = sum(d["score"] for d in data) / total

        interview_rate = round(interview_count / total * 100, 1)
        hire_rate = round(hire_count / total * 100, 1)

        # Determine correlation: compare interview rate to overall average
        # (simple heuristic — more sophisticated stats could be added)
        correlation = "neutral"
        if interview_rate > 50 and hire_rate > 20:
            correlation = "positive"
        elif interview_rate < 20 and hire_rate < 5:
            correlation = "negative"

        keywords_analyzed.append(CalibrationKeyword(
            keyword=kw,
            present_in_count=total,
            interview_rate=interview_rate,
            hire_rate=hire_rate,
            avg_score=round(avg_score, 1),
            correlation=correlation,
        ))

    # Sort by correlation strength (positive first, then by interview rate desc)
    positive = sorted(
        [k for k in keywords_analyzed if k.correlation == "positive"],
        key=lambda x: x.interview_rate,
        reverse=True,
    )
    negative = sorted(
        [k for k in keywords_analyzed if k.correlation == "negative"],
        key=lambda x: x.interview_rate,
    )
    neutral = sorted(
        [k for k in keywords_analyzed if k.correlation == "neutral"],
        key=lambda x: x.present_in_count,
        reverse=True,
    )

    # Top = positive correlations (most impactful)
    # Bottom = negative correlations (least impactful)
    top_keywords = positive + neutral[:5]
    bottom_keywords = negative + neutral[-5:] if len(neutral) > 5 else negative

    return top_keywords, bottom_keywords


def _extract_job_keywords(job: JobPosting) -> set[str]:
    """Extract meaningful keywords from a job posting.

    Uses deterministic extraction: capitalized technical terms, skill-like phrases.
    """
    keywords: set[str] = set()
    text = f"{job.title or ''} {job.description or ''}"

    # Add requirements explicitly (they are already extracted)
    if job.requirements:
        for req in job.requirements:
            # Extract key terms from each requirement
            words = req.lower().split()
            for w in words:
                # Skip common words and numbers
                if len(w) > 2 and w not in _STOP_WORDS:
                    keywords.add(w)

    # Find capitalized multi-word terms (likely company names, frameworks, etc.)
    import re
    # Match phrases like "Kubernetes", "Machine Learning", "PyTorch", "AWS"
    capitalized = re.findall(r'\b[A-Z][a-zA-Z0-9+#.]*(?:\s[A-Z][a-zA-Z0-9+#.]*)*\b', text)
    for term in capitalized:
        term_lower = term.lower()
        if len(term) > 2 and term_lower not in _STOP_WORDS:
            keywords.add(term_lower)

    return keywords


_STOP_WORDS = {
    "the", "and", "for", "are", "but", "not", "you", "all", "can", "has",
    "was", "had", "per", "its", "our", "your", "their", "with", "from",
    "this", "that", "they", "will", "have", "been", "also",
    "more", "than", "some", "such", "each", "what", "when", "where",
    "which", "who", "whom", "how", "why", "about", "into", "through",
    "during", "before", "after", "above", "below", "between", "under",
    "again", "further", "then", "once", "here", "there", "because",
    "while", "only", "very", "just", "much", "many", "most", "other",
    "some", "every", "own", "same", "new", "first", "last",
    "able", "any", "every", "both", "each", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "because", "as", "until",
    "while", "of", "at", "by", "for", "with", "about", "against",
    "between", "into", "through", "during", "before", "after", "above",
    "below", "to", "from", "up", "down", "in", "out", "on", "off",
    "over", "under", "again", "further", "then", "once", "here",
    "there", "when", "where", "why", "how", "all", "each", "every",
    "both", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "also",
    "well", "even", "still", "already", "yet", "please",
    "may", "might", "must", "shall", "should", "would", "could",
    "need", "dare", "ought", "used", "always", "never", "sometimes",
    "often", "usually", "generally", "finally", "eventually", "currently",
    "previously", "recently", "typically", "ideally", "preferably",
    "including", "various", "related", "relevant",
    "specific", "particular", "additional", "multiple", "minimum",
    "based", "located",
}


# ── Insights generation ─────────────────────────────────────────────


def _generate_insights(
    funnel: FunnelMetrics,
    top_keywords: list[CalibrationKeyword],
    bottom_keywords: list[CalibrationKeyword],
    enriched_outcomes: list[dict[str, Any]],
) -> list[CalibrationInsight]:
    """Generate actionable insights based on the calibration analysis.

    Insights are deterministic, rule-based analyses that help the user
    understand their job search patterns and opportunities for improvement.
    """
    insights: list[CalibrationInsight] = []

    # 1. Funnel-based insights
    if funnel.total_applications == 0:
        insights.append(CalibrationInsight(
            category="general",
            insight="No applications tracked yet. Start recording outcomes after applying.",
            recommendation="Use the Outcome page to log each application's status after you hear back.",
            impact="medium",
        ))
        return insights

    if funnel.total_applications < 5:
        insights.append(CalibrationInsight(
            category="general",
            insight=f"Only {funnel.total_applications} applications tracked. More data needed for meaningful calibration.",
            recommendation="Continue tracking outcomes as they come in. The calibration improves with more data points.",
            impact="low",
        ))
    else:
        # Application → Interview rate
        if funnel.application_to_interview_pct < 20 and funnel.total_applications >= 5:
            insights.append(CalibrationInsight(
                category="funnel",
                insight=f"Low interview rate ({funnel.application_to_interview_pct}%). Only {funnel.interviews} interviews from {funnel.total_applications} applications.",
                recommendation="Consider improving CV tailoring, targeting jobs more closely aligned with your profile, or adding missing keywords from job postings.",
                impact="high",
            ))
        elif funnel.application_to_interview_pct >= 40:
            insights.append(CalibrationInsight(
                category="funnel",
                insight=f"Strong interview rate ({funnel.application_to_interview_pct}%). Your CV is resonating with recruiters.",
                recommendation="Focus on improving interview performance to convert more interviews into offers.",
                impact="medium",
            ))

        # Interview → Offer rate
        if funnel.interview_to_offer_pct < 25 and funnel.interviews >= 3:
            insights.append(CalibrationInsight(
                category="funnel",
                insight=f"Low offer conversion ({funnel.interview_to_offer_pct}% of interviews → offers). This suggests interview preparation may need attention.",
                recommendation="Use the Interview Prep feature to prepare STAR examples and practice common questions for each application.",
                impact="high",
            ))

        # Overall success
        if funnel.overall_success_pct >= 20:
            insights.append(CalibrationInsight(
                category="funnel",
                insight=f"Overall success rate: {funnel.overall_success_pct}% hired from total applications. Excellent conversion!",
                recommendation="Your job search strategy is working well. Keep refining your process and sharing what works.",
                impact="medium",
            ))

    # 2. Keyword-based insights
    if top_keywords:
        top_3 = [k.keyword for k in top_keywords[:3]]
        insights.append(CalibrationInsight(
            category="keyword",
            insight=f"Top correlated keywords: {', '.join(top_3)}. These skills appear in jobs that lead to interviews.",
            recommendation=f"Ensure these keywords are prominently featured in your CV and LinkedIn profile: {', '.join(top_3)}",
            impact="high",
        ))

    if bottom_keywords:
        bottom_3 = [k.keyword for k in bottom_keywords[:3]]
        insights.append(CalibrationInsight(
            category="keyword",
            insight=f"Low-correlation keywords: {', '.join(bottom_3)}. Jobs mentioning these have lower success rates.",
            recommendation=f"Consider whether investing in these skills is worth the effort, or whether to focus on higher-yield areas.",
            impact="medium",
        ))

    # 3. Company/role insights
    companies = Counter()
    roles = Counter()
    for item in enriched_outcomes:
        job = item["job"]
        if job:
            if job.company:
                companies[job.company] += 1
            if job.title:
                roles[job.title] += 1

    if companies:
        top_company = companies.most_common(1)[0]
        if top_company[1] >= 2:
            insights.append(CalibrationInsight(
                category="company",
                insight=f"You've applied to {top_company[0]} {top_company[1]} times. Multiple applications to the same company.",
                recommendation="If you're not getting interviews at companies you applied to multiple times, consider whether your targeting or application approach needs adjustment.",
                impact="medium",
            ))

    # 4. Progress-based insights
    if funnel.in_progress > 0:
        insights.append(CalibrationInsight(
            category="progress",
            insight=f"You have {funnel.in_progress} applications in progress (interviews or offers pending).",
            recommendation="Use the Interview Prep feature for each in-progress application to maximize your chances.",
            impact="high",
        ))

    # 5. Rejection pattern insights
    if funnel.rejected >= 5 and funnel.interviews == 0:
        insights.append(CalibrationInsight(
            category="pattern",
            insight=f"{funnel.rejected} rejections with 0 interviews. Your applications may not be reaching the interview stage.",
            recommendation="Try the Verification Checklist (POST /apply/{id}/verify) to check your CV for ATS compatibility issues before applying.",
            impact="high",
        ))

    # 6. Withdrawn pattern
    if funnel.withdrawn >= 3:
        insights.append(CalibrationInsight(
            category="pattern",
            insight=f"You've withdrawn from {funnel.withdrawn} applications. Consider whether your job criteria are well-defined.",
            recommendation="Use the Rank page to evaluate jobs more carefully before applying to avoid wasting effort on misaligned opportunities.",
            impact="low",
        ))

    return insights
