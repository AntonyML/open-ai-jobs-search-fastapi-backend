"""Tests for the unified access gate and LLM usage accumulation on the ledger.

Follows the same service-level style as ``test_rank.py`` / ``test_credits.py``:
in-memory SQLite + seeded plan catalog, no HTTP client, no network.

Covers the single source of truth for apply/rank gating:
- 402 when the action's credit cost cannot be paid (free/pro: credit-only).
- 429 when a quota-bearing plan (max) exhausts its daily/weekly quota.
- 200-equivalent success path: credits consumed and a correlation_id returned.
- ``credits.record_llm_usage`` accumulating real tokens/cost onto the same
  ledger row the gate created (usage accounting, not a second gate).
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db.models import Base, CreditTransaction, User
from app.services import credits
from app.services.access_gate import enforce_action_gate
from app.services.plans import get_credit_costs, get_plan
from tests.unit.plan_helpers import seed_test_plans
from app.services.subscriptions import activate_subscription


@pytest.fixture
async def db_session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        await seed_test_plans(session)
        await session.commit()
        yield session

    await engine.dispose()


@pytest.fixture
async def free_user(db_session):
    u = User(id="free-1", email="free@example.com", hashed_password="x", role="client", tier="free")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def max_user(db_session):
    u = User(id="max-1", email="max@example.com", hashed_password="x", role="client", tier="max")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


@pytest.fixture
async def pro_user(db_session):
    u = User(id="pro-1", email="pro@example.com", hashed_password="x", role="client", tier="free")
    db_session.add(u)
    await db_session.commit()
    await db_session.refresh(u)
    return u


def _client_ctx(user_id: str) -> dict:
    """The user dict the routers pass to the gate (JWT shape)."""
    return {"sub": user_id, "role": "client"}


# ── Gate: HTTP 402 without credits (apply + rank) ───────────────────


async def test_gate_cv_base_402_without_credits(db_session, free_user):
    """Free user with zero credits → gate('cv_base') raises HTTP 402."""
    await activate_subscription(db_session, free_user, "free", source="signup_bonus")
    await db_session.commit()

    # Free plan refills 2 credits on first check; spend them both.
    bal = await credits.get_balance(db_session, free_user.id)
    assert bal["balance"] == 2
    await credits.consume_credits(db_session, free_user.id, "cv_base", 2)
    assert (await credits.get_balance(db_session, free_user.id))["balance"] == 0

    with pytest.raises(HTTPException) as exc:
        await enforce_action_gate(
            db_session, _client_ctx(free_user.id), "cv_base",
            label="Base CV generation",
        )
    assert exc.value.status_code == 402


async def test_gate_cv_adapted_402_without_credits(db_session, free_user):
    """Free user with zero credits → gate('cv_adapted') raises HTTP 402."""
    await activate_subscription(db_session, free_user, "free", source="signup_bonus")
    await db_session.commit()

    await credits.consume_credits(db_session, free_user.id, "cv_base", 2)
    assert (await credits.get_balance(db_session, free_user.id))["balance"] == 0

    with pytest.raises(HTTPException) as exc:
        await enforce_action_gate(db_session, _client_ctx(free_user.id), "cv_adapted")
    assert exc.value.status_code == 402


async def test_gate_verify_402_without_subscription(db_session, free_user):
    """No subscription/plan and no credits → gate('verify') (ungated action)
    reaches the credit check and raises HTTP 402."""
    with pytest.raises(HTTPException) as exc:
        await enforce_action_gate(db_session, _client_ctx(free_user.id), "verify")
    assert exc.value.status_code == 402


# ── Gate: HTTP 429 on quota exhaustion (max) ────────────────────────


async def test_gate_rank_429_quota_exhausted(db_session, max_user):
    """Max user past the daily quota → gate('rank') raises HTTP 429 before
    any credit is charged."""
    await activate_subscription(db_session, max_user, "max", source="admin")
    await db_session.commit()

    plan = await get_plan(db_session, "max")
    assert plan is not None and plan.daily_quota > 0
    for _ in range(plan.daily_quota):
        await credits.consume_quota(db_session, max_user.id, plan)
    bal = await credits.get_balance(db_session, max_user.id)
    assert bal["quota_day_used"] == plan.daily_quota

    with pytest.raises(HTTPException) as exc:
        await enforce_action_gate(db_session, _client_ctx(max_user.id), "rank")
    assert exc.value.status_code == 429


async def test_gate_apply_429_quota_exhausted(db_session, max_user):
    """Same quota circuit breaker for the apply action."""
    await activate_subscription(db_session, max_user, "max", source="admin")
    await db_session.commit()

    plan = await get_plan(db_session, "max")
    for _ in range(plan.daily_quota):
        await credits.consume_quota(db_session, max_user.id, plan)

    with pytest.raises(HTTPException) as exc:
        await enforce_action_gate(db_session, _client_ctx(max_user.id), "apply")
    assert exc.value.status_code == 429


# ── Enriched 402/429 detail (plan.md §4) ───────────────────────────


async def test_gate_402_detail_is_structured(db_session, free_user):
    """402 detail carries code/balance/next_reset_at/topup_packs/correlation_id
    so the frontend can key on code and render the top-up modal."""
    await activate_subscription(db_session, free_user, "free", source="signup_bonus")
    await db_session.commit()
    await credits.consume_credits(db_session, free_user.id, "cv_base", 2)

    with pytest.raises(HTTPException) as exc:
        await enforce_action_gate(db_session, _client_ctx(free_user.id), "cv_base")
    detail = exc.value.detail
    assert detail["code"] == "insufficient_credits"
    assert detail["balance"] == 0
    assert detail["correlation_id"]  # preserved from the old string detail
    assert {p["credits"] for p in detail["topup_packs"]} == {50, 120}
    # free (weekly cadence) → next_refill = last_refill + 7 days, as ISO string
    assert isinstance(detail["next_reset_at"], str)


async def test_gate_402_next_reset_at_is_period_end_for_paid(db_session, pro_user):
    """Paid plans refill per period → next_reset_at mirrors the period_end."""
    sub = await activate_subscription(db_session, pro_user, "pro", source="admin")
    await db_session.commit()
    # Spend the whole period allowance.
    bal = await credits.get_balance(db_session, pro_user.id)
    await credits.consume_credits(db_session, pro_user.id, "cv_base", bal["balance"])

    with pytest.raises(HTTPException) as exc:
        await enforce_action_gate(db_session, _client_ctx(pro_user.id), "cv_adapted")
    detail = exc.value.detail
    assert detail["code"] == "insufficient_credits"
    assert detail["next_reset_at"] == sub.period_end.isoformat()


async def test_gate_429_detail_is_structured(db_session, max_user):
    """429 detail carries quota fields + next_reset_at and never a
    correlation_id (quotas are not monetizable — no top-up CTA)."""
    await activate_subscription(db_session, max_user, "max", source="admin")
    await db_session.commit()
    plan = await get_plan(db_session, "max")
    for _ in range(plan.daily_quota):
        await credits.consume_quota(db_session, max_user.id, plan)

    with pytest.raises(HTTPException) as exc:
        await enforce_action_gate(db_session, _client_ctx(max_user.id), "rank")
    detail = exc.value.detail
    assert detail["code"] == "quota_exceeded"
    assert detail["quota_week_used"] == plan.daily_quota  # day quota also counts weekly
    assert detail["quota_week_limit"] == plan.weekly_quota
    assert detail["balance"] == 350  # period allowance granted by the refill
    assert isinstance(detail["next_reset_at"], str)
    assert {p["credits"] for p in detail["topup_packs"]} == {50, 120}
    assert "correlation_id" not in detail


# ── Gate: success path ──────────────────────────────────────────────


async def test_gate_cv_base_success_consumes_credit_and_returns_correlation_id(db_session, free_user):
    """Free user with credits → gate('cv_base') charges the action cost and
    returns a correlation_id for usage accounting."""
    await activate_subscription(db_session, free_user, "free", source="signup_bonus")
    await db_session.commit()
    assert (await credits.get_balance(db_session, free_user.id))["balance"] == 2

    cid = await enforce_action_gate(
        db_session, _client_ctx(free_user.id), "cv_base", label="Base CV generation"
    )
    assert cid is not None

    bal = await credits.get_balance(db_session, free_user.id)
    assert bal["balance"] == 1  # cv_base costs 1 credit

    txn = (await db_session.execute(
        select(CreditTransaction).where(CreditTransaction.correlation_id == cid)
    )).scalar_one()
    assert txn.action == "cv_base"
    assert txn.credits_delta == -1


async def test_gate_max_charges_quota_and_credit(db_session, max_user):
    """Max is quota-gated AND credit-charged in the same call."""
    await activate_subscription(db_session, max_user, "max", source="admin")
    await db_session.commit()
    costs = await get_credit_costs(db_session)
    assert costs["rank"] == 1

    cid = await enforce_action_gate(db_session, _client_ctx(max_user.id), "rank")
    assert cid is not None

    bal = await credits.get_balance(db_session, max_user.id)
    assert bal["quota_day_used"] == 1
    assert bal["balance"] == 350 - 1


async def test_gate_admin_bypass(db_session, free_user):
    """Admin bypasses the gate entirely: no credit consumed, no correlation_id."""
    admin = User(
        id="admin-1", email="admin@example.com", hashed_password="x",
        role="admin", tier="free",
    )
    db_session.add(admin)
    await db_session.commit()

    cid = await enforce_action_gate(
        db_session, {"sub": admin.id, "role": "admin"}, "apply"
    )
    assert cid is None
    # No credit account/ledger rows created for the admin.
    rows = (await db_session.execute(select(CreditTransaction))).scalars().all()
    assert rows == []


# ── Feature gate (plan.md §8.2 F4) ─────────────────────────────────


async def test_gate_feature_403_when_plan_lacks_feature(db_session, free_user):
    """Free plan (no 'pipeline') calling 'apply' → 403 feature_required,
    raised BEFORE any quota/credit is consumed."""
    await activate_subscription(db_session, free_user, "free", source="signup_bonus")
    await db_session.commit()
    bal_before = (await credits.get_balance(db_session, free_user.id))["balance"]

    with pytest.raises(HTTPException) as exc:
        await enforce_action_gate(db_session, _client_ctx(free_user.id), "apply")
    assert exc.value.status_code == 403
    assert exc.value.detail["code"] == "feature_required"
    assert exc.value.detail["feature"] == "pipeline"
    # nothing consumed
    assert (await credits.get_balance(db_session, free_user.id))["balance"] == bal_before


async def test_gate_feature_403_interview_on_pro(db_session, pro_user):
    """Pro plan (CV only, no pipeline) calling 'interview' → 403."""
    await activate_subscription(db_session, pro_user, "pro", source="admin")
    await db_session.commit()

    with pytest.raises(HTTPException) as exc:
        await enforce_action_gate(db_session, _client_ctx(pro_user.id), "interview")
    assert exc.value.status_code == 403
    assert exc.value.detail["feature"] == "pipeline"


async def test_gate_feature_passes_when_plan_has_feature(db_session, max_user):
    """Max plan (pipeline) calling 'apply' passes the feature gate and reaches
    the credit check (402 here because the quota refill hasn't run credits)."""
    await activate_subscription(db_session, max_user, "max", source="admin")
    await db_session.commit()

    cid = await enforce_action_gate(db_session, _client_ctx(max_user.id), "apply")
    assert cid is not None  # charged (max has credits + pipeline)
    assert (await credits.get_balance(db_session, max_user.id))["balance"] == 350 - 1


async def test_gate_verify_not_gated(db_session, free_user):
    """verify has feature_gate=None → available on every plan; the gate only
    enforces credits (402 here once the free allowance is spent)."""
    await activate_subscription(db_session, free_user, "free", source="signup_bonus")
    await db_session.commit()

    cid = await enforce_action_gate(db_session, _client_ctx(free_user.id), "verify")
    assert cid is not None  # 2 credits available
    assert (await credits.get_balance(db_session, free_user.id))["balance"] == 1


# ── Usage accumulation on the ledger ────────────────────────────────


async def test_record_llm_usage_accumulates_on_ledger_row(db_session, free_user):
    """record_llm_usage accumulates real tokens/cost onto the ledger row the
    gate created — repeated sub-calls add up on the same correlation_id."""
    await activate_subscription(db_session, free_user, "free", source="signup_bonus")
    await db_session.commit()

    cid = await enforce_action_gate(db_session, _client_ctx(free_user.id), "cv_base")
    assert cid is not None

    # First LLM sub-call
    await credits.record_llm_usage(
        db_session, cid,
        model_used="anthropic/claude-sonnet-4-5",
        tokens_input=100, tokens_output=50, cost_usd_cents=2,
    )
    # Second LLM sub-call (same request) — must accumulate, not overwrite
    await credits.record_llm_usage(
        db_session, cid,
        model_used="anthropic/claude-sonnet-4-5",
        tokens_input=50, tokens_output=25, cost_usd_cents=3,
    )

    txn = (await db_session.execute(
        select(CreditTransaction).where(CreditTransaction.correlation_id == cid)
    )).scalar_one()
    assert txn.tokens_input == 150
    assert txn.tokens_output == 75
    assert txn.cost_usd_cents == 5
    assert txn.model_used == "anthropic/claude-sonnet-4-5"
    assert txn.credits_delta == -1  # immutable delta untouched


async def test_record_llm_usage_unknown_correlation_id_is_noop(db_session, free_user):
    """Unknown correlation_id (e.g. admin bypass) → record_llm_usage no-ops
    without raising."""
    result = await credits.record_llm_usage(
        db_session, "does-not-exist",
        tokens_input=10, tokens_output=5, cost_usd_cents=1,
    )
    assert result is None
