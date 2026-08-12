"""Pydantic schemas for server-side app notifications."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AppNotificationOut(BaseModel):
    """Server-side notification (admin alerts, purchase requests, quota events)."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    # info | credits_low | quota_exhausted | ia_exhausted |
    # purchase_request | plan_expired | <pipeline>[_error]
    type: str
    title: str
    body: str | None
    is_read: bool
    created_at: datetime | None
    payload: dict[str, Any] | None = None


class AppNotificationCreate(BaseModel):
    """Create a notification for the authenticated user."""

    type: str = Field(..., max_length=50)
    title: str = Field(..., max_length=255)
    body: str | None = Field(default=None, max_length=2000)
    payload: dict[str, Any] | None = None
