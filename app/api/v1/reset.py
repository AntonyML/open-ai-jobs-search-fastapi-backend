"""Reset router — endpoints for clearing profile data and career documents."""

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user
from app.db.session import get_db as _get_db
from app.schemas.reset import ResetRequest, ResetResponse
from app.services import reset

router = APIRouter(prefix="/reset", tags=["reset"])


@router.post(
    "/",
    response_model=ResetResponse,
    status_code=status.HTTP_200_OK,
    responses={
        status.HTTP_400_BAD_REQUEST: {
            "description": "Confirmation required. Destructive action preview provided.",
            "content": {
                "application/json": {
                    "example": {
                        "error": "confirmation_required",
                        "message": "## Profile reset will clear:\n- Candidate Profile (ID: 123) — [has content]...\n\nWARNING: This action is destructive and cannot be undone.\nTo confirm, re-submit the request with confirm='RESET'.",  # noqa: E501
                    }
                }
            },
        }
    },
)
async def trigger_reset(
    payload: ResetRequest,
    user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(_get_db),
):
    """Reset candidate profile and/or career documents.

    This action is destructive. If `confirm` is not set to 'RESET', it raises
    a ConfirmationRequiredError (which returns a preview of what will be deleted
    along with instructions on how to confirm).
    """
    result = await reset.execute_reset(
        db=db,
        user_id=user["sub"],
        scope=payload.scope,
        confirm=payload.confirm,
    )
    return result
