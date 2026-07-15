"""API v1 router — aggregates all versioned endpoint modules."""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.setup import router as setup_router
from app.api.v1.scrape import router as scrape_router
from app.api.v1.rank import router as rank_router
from app.api.v1.apply import router as apply_router
from app.api.v1.interview import router as interview_router
from app.api.v1.outcome import router as outcome_router
from app.api.v1.expand import router as expand_router
from app.api.v1.upskill import router as upskill_router
from app.api.v1.add_portal import router as add_portal_router
from app.api.v1.add_template import router as add_template_router
from app.api.v1.reset import router as reset_router
from app.api.v1.pipeline_reset import router as pipeline_reset_router
from app.api.v1.providers import router as providers_router
from app.api.v1.orchestrator import router as orchestrator_router
from app.api.v1.salary import router as salary_router
from app.api.v1.verification import router as verification_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.dashboard import analytics_router

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness probe — returns 200 if the API is running."""
    return {"status": "ok", "version": "0.1.0"}


# ── Auth router ────────────────────────────────────────────────────
router.include_router(auth_router)

# ── Providers router ───────────────────────────────────────────────
router.include_router(providers_router)

# ── Orchestrator router ────────────────────────────────────────────
router.include_router(orchestrator_router)

# ── Skill routers ──────────────────────────────────────────────────
router.include_router(setup_router)
router.include_router(scrape_router)
router.include_router(rank_router)
router.include_router(apply_router)
router.include_router(interview_router)
router.include_router(outcome_router)
router.include_router(expand_router)
router.include_router(upskill_router)
router.include_router(salary_router)  # POST/GET/DELETE /profile/salary-data
router.include_router(verification_router)  # POST /apply/{id}/verify
router.include_router(add_portal_router)
router.include_router(add_template_router)
router.include_router(pipeline_reset_router)
router.include_router(reset_router)

# ── Dashboard + Analytics ───────────────────────────────────────────
router.include_router(dashboard_router)
router.include_router(analytics_router)