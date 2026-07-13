"""API v1 router — aggregates all versioned endpoint modules."""

from fastapi import APIRouter

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

router = APIRouter()


@router.get("/health")
async def health_check():
    """Liveness probe — returns 200 if the API is running."""
    return {"status": "ok", "version": "0.1.0"}


# ── Skill routers ──────────────────────────────────────────────────
router.include_router(setup_router)
router.include_router(scrape_router)
router.include_router(rank_router)
router.include_router(apply_router)
router.include_router(interview_router)
router.include_router(outcome_router)
router.include_router(expand_router)
router.include_router(upskill_router)
router.include_router(add_portal_router)
router.include_router(add_template_router)
router.include_router(reset_router)