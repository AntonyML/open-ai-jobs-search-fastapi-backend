"""Full end-to-end: search → select → rank with job_ids from ingested_jobs."""
import asyncio, httpx, json, sys

async def main():
    client = httpx.AsyncClient(timeout=60)

    # 1. Login (API principal)
    print("=== 1. Login ===")
    resp = await client.post("http://127.0.0.1:8000/api/v1/auth/login",
        json={"email": "demo@example.com", "password": "demo1234"})
    print(f"  Status: {resp.status_code}")
    data = resp.json()
    token = data.get("access_token")
    if not token:
        print(f"  FAIL: {data}")
        return
    headers = {"Authorization": f"Bearer {token}"}
    print("  OK — token obtained")

    # 2. Ensure ingested_jobs has data — trigger ingest if needed
    print("\n=== 2. Search for jobs ===")
    resp = await client.post("http://127.0.0.1:8000/api/v1/jobs/search",
        headers=headers,
        json={"keywords": "developer", "limit": 10})
    print(f"  Status: {resp.status_code}")
    search = resp.json()
    print(f"  Response: {json.dumps(search, indent=2)[:500]}")

    jobs = search.get("jobs", [])
    if not jobs:
        # Trigger ingest via microservice
        print("\n=== 2b. No jobs found, triggering ingest ===")
        resp = await client.post("http://127.0.0.1:8001/api/v1/ingest",
            json={"category_id": "stem_cr", "keywords": "developer"})
        ingest = resp.json()
        print(f"  Ingest: {ingest}")
        job_id = ingest.get("ingest_job_id")
        if job_id:
            for _ in range(10):
                await asyncio.sleep(3)
                resp = await client.get(f"http://127.0.0.1:8001/api/v1/ingest/{job_id}/status")
                status = resp.json()
                print(f"  Status: {status}")
                if status.get("status") == "done":
                    break
        # Retry search
        resp = await client.post("http://127.0.0.1:8000/api/v1/jobs/search",
            headers=headers,
            json={"keywords": "developer", "limit": 10})
        jobs = resp.json().get("jobs", [])
        print(f"  After ingest: {len(jobs)} jobs found")

    if not jobs:
        print("  FAIL: no jobs available to rank")
        return

    # 3. Select 2 jobs and call rank with job_ids
    print("\n=== 3. Rank with specific job_ids ===")
    selected_ids = [j["id"] for j in jobs[:2]]
    print(f"  Selected {len(selected_ids)} jobs: {selected_ids}")

    resp = await client.post("http://127.0.0.1:8000/api/v1/rank/",
        headers=headers,
        json={"top_n": 5, "job_ids": selected_ids})
    print(f"  Status: {resp.status_code}")
    rank = resp.json()
    print(f"  Response: {json.dumps(rank, indent=2)}")

    if resp.status_code != 202:
        print(f"  FAIL: rank endpoint returned {resp.status_code}")
        return

    rank_job_id = rank.get("job_id")
    accepted = rank.get("accepted_jobs", 0)
    total = rank.get("total_jobs", 0)
    print(f"  Rank job: {rank_job_id}, total={total}, accepted={accepted}")

    if accepted == 2 and total == 2:
        print("  ✅ CORRECT: accepted 2 jobs, total 2 (the 2 we selected)")
    elif accepted == 0 and total == 2:
        print("  ⚠️  accepted=0 but total=2 — possible race condition in worker")
    else:
        print(f"  ⚠️  unexpected: accepted={accepted}, total={total}")

    # 4. Verify the imported jobs exist in job_postings
    print("\n=== 4. Verify jobs in job_postings ===")
    from app.db.session import async_session_factory
    from app.db.models import JobPosting
    from sqlalchemy import select
    async with async_session_factory() as db:
        result = await db.execute(
            select(JobPosting).where(JobPosting.id.in_(selected_ids))
        )
        imported = result.scalars().all()
        print(f"  Found {len(imported)} of {len(selected_ids)} imported JobPosting records")
        for jp in imported:
            print(f"    - {jp.id[:12]}... {jp.title[:50]} | {jp.company} | status={jp.status}")

    # 5. Check rank endpoint lists them
    print("\n=== 5. GET /rank/jobs ===")
    resp = await client.get("http://127.0.0.1:8000/api/v1/rank/jobs",
        headers=headers)
    ranked = resp.json()
    print(f"  {len(ranked)} ranked jobs returned:")
    for rj in ranked[:5]:
        print(f"    - {rj['id'][:12]}... {rj['title'][:50]} | score={rj.get('rank_score')} | verdict={rj.get('rank_verdict')}")

    await client.aclose()
    print("\n=== ALL DONE ===")

asyncio.run(main())
