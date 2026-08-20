"""Migration guard for the legacy ``premium`` tier (plan.md §2.7).

The ``premium`` tier predates the current free/pro/max catalog and made the
admin dashboard show a "Premium" card that counted exactly
``tier == "premium"`` — almost always 0.  This module migrates any remaining
``premium`` users to a real plan (default ``max``, configurable) and makes
sure they keep an active subscription so nobody loses access.

Guarantees:

- **No-op safe**: with 0 ``premium`` users nothing is touched (the dev DB
  has 0 today — the script exists to protect environments with legacy rows).
- **Idempotent**: a second run finds 0 ``premium`` users → pure no-op; a
  ``premium`` user that already has an active subscription only gets the
  tier fixed, never a duplicate subscription.
- **Conservative**: if the target plan does not exist, the user is left
  untouched and counted as skipped (the operator fixes the catalog first).

The caller owns the transaction (``commit``/``rollback``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import User
from app.services.plans import get_plan
from app.services.subscriptions import activate_subscription, get_active_subscription

logger = logging.getLogger(__name__)

LEGACY_TIER = "premium"
DEFAULT_TARGET_PLAN = "max"


@dataclass
class MigrationResult:
    """Summary of a migration run."""

    found: int = 0  # premium users discovered
    tier_only: int = 0  # tier fixed, existing subscription kept
    subscriptions_created: int = 0  # tier fixed + active subscription created
    skipped_missing_plan: int = 0  # target plan unavailable → left untouched

    @property
    def migrated(self) -> int:
        return self.tier_only + self.subscriptions_created

    @property
    def changed(self) -> bool:
        return self.migrated > 0


async def migrate_premium_tier(
    db: AsyncSession,
    target: str = DEFAULT_TARGET_PLAN,
) -> MigrationResult:
    """Map every user with ``tier == "premium"`` to ``target``.

    For each legacy user:

    1. If the target plan is missing from the catalog → skip (leave the
       user untouched) and count it, so the problem stays visible.
    2. If the user already has an active subscription → fix the tier only
       (never duplicate the subscription).
    3. Otherwise → ``activate_subscription`` (creates an active monthly,
       auto-renewing subscription and links the credit account).

    Safe to call repeatedly; the caller commits.
    """
    result = MigrationResult()

    rows = (await db.execute(select(User).where(User.tier == LEGACY_TIER))).scalars().all()
    result.found = len(rows)
    if not rows:
        logger.info("No-op: 0 users on tier '%s'", LEGACY_TIER)
        return result

    if await get_plan(db, target) is None:
        logger.error(
            "Migration target plan '%s' not found in the catalog — leaving %d user(s) untouched on '%s'",
            target,
            len(rows),
            LEGACY_TIER,
        )
        result.skipped_missing_plan = len(rows)
        return result

    for user in rows:
        existing = await get_active_subscription(db, user.id)
        if existing is not None:
            user.tier = target
            await db.flush()
            result.tier_only += 1
            logger.info(
                "Migrated tier %s→%s (kept existing subscription): user=%s plan=%s",
                LEGACY_TIER,
                target,
                user.id,
                existing.plan_key,
            )
            continue

        await activate_subscription(
            db,
            user,
            target,
            billing_cycle="monthly",
            source="migration",
            auto_renew=True,
            note=f"Legacy {LEGACY_TIER} tier migrated to {target}",
        )
        result.subscriptions_created += 1
        logger.info(
            "Migrated tier %s→%s + created active subscription: user=%s",
            LEGACY_TIER,
            target,
            user.id,
        )

    await db.flush()
    return result
