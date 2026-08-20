from app.db.models import Plan


async def seed_test_plans(db) -> None:
    plans = [
        Plan(
            key="free",
            name="Free",
            credits_per_period=2,
            refill_cadence="weekly",
            features=["cv_base", "cv_adapted"],
            sort_order=10,
        ),
        Plan(
            key="pro",
            name="Pro",
            price_monthly_usd=24.99,
            price_yearly_usd=249,
            credits_per_period=80,
            features=["cv_base", "cv_adapted"],
            sort_order=20,
        ),
        Plan(
            key="max",
            name="Max",
            price_monthly_usd=69.99,
            price_yearly_usd=699,
            credits_per_period=350,
            daily_quota=12,
            weekly_quota=50,
            features=["cv_base", "cv_adapted", "pipeline", "expand", "upskill"],
            sort_order=30,
        ),
    ]
    db.add_all(plans)
    await db.flush()
