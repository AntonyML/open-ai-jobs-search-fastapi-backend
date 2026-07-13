"""API v1 router — aggregates all versioned endpoint modules."""

from fastapi import APIRouter

from app.api.v1.setup import router as setup_router
from app.api.v1.scrape import router as scrape_router
from app.api.v1.rank import router as rank_router
from app.api.v1.apply import router as apply_router
from app.api.v1.interview import router as interview_router

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