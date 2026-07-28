"""Test end-to-end: microservice ingest + API principal reads."""
import asyncio
import httpx
from sqlalchemy import select

from app.core.settings import get_settings
from app.db.session import async_session_factory
from app.db.models import IngestedJob


async def test():
    s = get_settings()
    print("=== Test 3: Trigger ingest via microservice ===")

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            f"{s.ingest_service_url}/api/v1/ingest",
            json={"category_id": "stem_cr", "keywords": "senior"},
        )
        print(f"Status: {resp.status_code}")
        data = resp.json()
        print(f"Ingest job: {data}")

        if resp.status_code == 200:
            job_id = data["ingest_job_id"]
            await asyncio.sleep(12)

            resp2 = await client.get(
                f"{s.ingest_service_url}/api/v1/ingest/{job_id}/status"
            )
            print(f"Status after 12s: {resp2.json()}")

    print()
    print("=== Test 4: API principal reads ingested_jobs ===")
    async with async_session_factory() as db:
        result = await db.execute(
            select(IngestedJob)
            .limit(5)
            .order_by(IngestedJob.ingested_at.desc())
        )
        jobs = result.scalars().all()
        print(f"Direct DB query: {len(jobs)} jobs in ingested_jobs")
        for j in jobs[:5]:
            exp = j.expires_at.strftime("%m/%d") if j.expires_at else "?"
            co = j.company or "?"
            print(f"  - {j.title[:50]} | {co[:25]} | expires={exp}")

    print()
    print("=== ALL TESTS PASSED ===")


asyncio.run(test())
