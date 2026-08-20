"""API v1 router — aggregates all versioned endpoint modules."""

from fastapi import APIRouter

from app.api.v1.admin import router as admin_router
from app.api.v1.apply import router as apply_router
from app.api.v1.auth import router as auth_router
from app.api.v1.billing import router as billing_router
from app.api.v1.cv import router as cv_router
from app.api.v1.dashboard import analytics_router
from app.api.v1.dashboard import router as dashboard_router
from app.api.v1.expand import router as expand_router
from app.api.v1.interview import router as interview_router
from app.api.v1.job_data import router as job_data_router
from app.api.v1.jobs import router as jobs_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.orchestrator import router as orchestrator_router
from app.api.v1.outcome import router as outcome_router
from app.api.v1.providers import router as providers_router
from app.api.v1.public import router as public_router
from app.api.v1.rank import router as rank_router
from app.api.v1.reset import router as reset_router
from app.api.v1.salary import router as salary_router
from app.api.v1.setup import router as setup_router
from app.api.v1.upskill import router as upskill_router
from app.api.v1.users import router as users_router
from app.api.v1.verification import router as verification_router

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness probe — returns 200 if the API is running."""
    return {"status": "ok", "version": "0.1.0"}


# ── Auth router ────────────────────────────────────────────────────
router.include_router(auth_router)

# ── Orchestrator router ────────────────────────────────────────────
router.include_router(orchestrator_router)

# ── Skill routers ──────────────────────────────────────────────────
router.include_router(setup_router)
router.include_router(rank_router)
router.include_router(apply_router)
router.include_router(interview_router)
router.include_router(outcome_router)
router.include_router(expand_router)
router.include_router(upskill_router)
router.include_router(salary_router)  # POST/GET/DELETE /profile/salary-data
router.include_router(verification_router)  # POST /apply/{id}/verify
router.include_router(job_data_router)
router.include_router(reset_router)

# ── Admin ────────────────────────────────────────────────────────────
router.include_router(admin_router)

# ── Dashboard + Analytics ───────────────────────────────────────────
router.include_router(dashboard_router)
router.include_router(analytics_router)

# ── Users ────────────────────────────────────────────────────────────
router.include_router(users_router)

# ── Jobs (microservice ingesta) ──────────────────────────────────────
router.include_router(jobs_router)

# ── CV generator ─────────────────────────────────────────────────────
router.include_router(cv_router)

# ── Global provider status (read-only, any authenticated user) ────────
router.include_router(providers_router)

# ── Billing / credits ────────────────────────────────────────────────
router.include_router(billing_router)

# ── Public (no auth) ─────────────────────────────────────────────────
router.include_router(public_router)

# ── Notifications ─────────────────────────────────────────────────────
router.include_router(notifications_router)
