"""Quick in-process end-to-end test."""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from fastapi.testclient import TestClient
from app.main import create_app

app = create_app()
client = TestClient(app)

print("=== 1. Login ===")
resp = client.post("/api/v1/auth/login", json={"email": "demo@example.com", "password": "demo1234"})
assert resp.status_code == 200, f"Login failed: {resp.json()}"
token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}
print("  OK")

print("\n=== 2. Search jobs ===")
resp = client.post("/api/v1/jobs/search", json={"keywords": "developer", "limit": 5}, headers=headers)
print(f"  Status: {resp.status_code}")
search = resp.json()
jobs = search.get("jobs", [])
print(f"  Jobs: {len(jobs)}, fresh: {search.get('fresh')}")
if search.get("ingest_job_id"):
    print(f"  Ingest triggered: {search['ingest_job_id']}")

if jobs:
    job_ids = [j["id"] for j in jobs[:2]]
    print(f"\n=== 3. Rank {len(job_ids)} jobs ===")
    resp = client.post("/api/v1/rank/", json={"top_n": 5, "job_ids": job_ids}, headers=headers)
    print(f"  Status: {resp.status_code}")
    rank = resp.json()
    if resp.status_code == 202:
        print(f"  accepted={rank['accepted_jobs']}, total={rank['total_jobs']}")
        print(f"  ✅" if rank['accepted_jobs'] == len(job_ids) else f"  ⚠️")
    else:
        print(f"  Response: {rank}")
else:
    print("\n=== 3. Rank with empty list (edge case) ===")
    resp = client.post("/api/v1/rank/", json={"top_n": 5}, headers=headers)
    print(f"  Status: {resp.status_code}, Response: {resp.json()}")

print("\n=== DONE ===")
