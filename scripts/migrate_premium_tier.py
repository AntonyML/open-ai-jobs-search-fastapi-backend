"""One-off migration guard: map legacy ``premium`` users to a real plan.

Usage:
    python scripts/migrate_premium_tier.py [--target max] [--dry-run]

Reads ``DATABASE_URL`` from ``.env`` (same settings as the API).  With 0
``premium`` users it logs a no-op and exits 0.  Running it twice is safe:
the second run is a no-op (no duplicate subscriptions).

Exit codes: 0 = ok, 1 = error.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from app.db.session import async_session_factory
from app.services.premium_migration import (
    DEFAULT_TARGET_PLAN,
    LEGACY_TIER,
    migrate_premium_tier,
)

logger = logging.getLogger("migrate_premium_tier")


async def run(target: str, dry_run: bool) -> int:
    async with async_session_factory() as db:
        result = await migrate_premium_tier(db, target=target)
        if dry_run:
            await db.rollback()
            logger.info("DRY-RUN — no changes were committed")
        else:
            await db.commit()
            logger.info("Changes committed")

    if result.found == 0:
        print(f"No-op: 0 users on tier '{LEGACY_TIER}'")
    else:
        print(
            f"Premium migration: found={result.found} "
            f"migrated={result.migrated} "
            f"(tier_only={result.tier_only}, subscriptions_created={result.subscriptions_created}) "
            f"skipped_missing_plan={result.skipped_missing_plan} target={target}"
        )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy premium users to a real plan (idempotent guard).")
    parser.add_argument(
        "--target",
        default=DEFAULT_TARGET_PLAN,
        help=f"Plan to map premium users to (default: {DEFAULT_TARGET_PLAN}).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without committing.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    try:
        return asyncio.run(run(args.target, args.dry_run))
    except Exception as exc:  # noqa: BLE001 — CLI boundary: report and exit 1
        logger.error("Migration failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
