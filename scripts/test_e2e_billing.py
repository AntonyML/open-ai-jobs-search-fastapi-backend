"""End-to-end smoke test for the billing lifecycle (Fases 1-4).

Run against the running API (dev server on 127.0.0.1:8000, see
``dev.ps1``) with a seeded demo user.  Covers:

- ``GET /billing/status`` → ``next_reset_at`` is exposed (weekly quota bar);
- ``GET /billing/catalog`` → exactly 2 ``topup_packs`` (plan.md §9.5);
- ``POST /billing/topup`` → 403 ``topup_requires_plan`` for free users;
- ``POST /billing/upgrade`` → 422 ``not_an_upgrade`` for same-plan requests;
- ``GET /billing/transactions`` → ledger readable.

State-dependent steps (top-up on a paid account, upgrade with an active
subscription) are reported as INFO rather than asserted — the smoke test
must pass for both free and paid demo accounts.

Usage:
    python scripts/test_e2e_billing.py
"""

import sys

import httpx

API = "http://127.0.0.1:8000"
EMAIL = "demo@example.com"
PASSWORD = "demo1234"

failures = []


def check(ok: bool, label: str, extra: str = "") -> None:
    status = "✅" if ok else "❌"
    print(f"  {status} {label}{f' — {extra}' if extra else ''}")
    if not ok:
        failures.append(label)


def main() -> None:
    client = httpx.Client(base_url=API, timeout=20)

    print("=== 1. Login ===")
    resp = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    if resp.status_code != 200:
        print(f"  FAIL: {resp.status_code} {resp.text[:300]}")
        print("  → Seed the demo user first (or edit EMAIL/PASSWORD at the top).")
        sys.exit(1)
    token = resp.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    print("  OK")

    print("\n=== 2. Billing status (next_reset_at) ===")
    resp = client.get("/api/v1/billing/status", headers=headers)
    check(resp.status_code == 200, f"status 200 (got {resp.status_code})")
    status = resp.json()
    check(
        "next_reset_at" in status,
        "status exposes next_reset_at",
        f"value={status.get('next_reset_at')!r}",
    )
    check(
        status.get("next_reset_at") is None or isinstance(status["next_reset_at"], str),
        "next_reset_at is ISO string or null",
    )
    plan_key = status.get("plan_key")
    print(f"  INFO plan={plan_key!r} tier={status.get('tier')} balance={status.get('credits_balance')}")

    print("\n=== 3. Catalog (topup packs locked to 2) ===")
    resp = client.get("/api/v1/billing/catalog", headers=headers)
    check(resp.status_code == 200, "catalog 200")
    catalog = resp.json()
    packs = catalog.get("topup_packs", [])
    check(len(packs) == 2, "exactly 2 topup_packs", f"got {len(packs)}")
    check(
        all(p["credits"] > 0 and p["price_usd"] > 0 for p in packs),
        "packs have positive credits + price",
        f"packs={packs}",
    )

    print("\n=== 4. Top-up request ===")
    resp = client.post(
        "/api/v1/billing/topup",
        headers=headers,
        json={"pack_credits": packs[0]["credits"], "method": "sinpe", "phone": "8888-8888"},
    )
    if plan_key == "free":
        check(resp.status_code == 403, "free user blocked with 403")
        body = resp.json().get("detail", {})
        check(body.get("code") == "topup_requires_plan", "detail.code = topup_requires_plan", str(body))
    else:
        print(f"  INFO paid account → topup returned {resp.status_code} (creates an admin notification)")

    print("\n=== 5. Upgrade request (same plan must be rejected) ===")
    resp = client.post(
        "/api/v1/billing/upgrade",
        headers=headers,
        json={"plan_key": plan_key or "pro", "method": "whatsapp"},
    )
    if plan_key and plan_key != "free":
        body = resp.json().get("detail", {})
        check(resp.status_code == 422, "same-plan upgrade rejected with 422")
        check(
            (isinstance(body, dict) and body.get("code") == "not_an_upgrade") or "not an upgrade" in str(body).lower(),
            "detail.code = not_an_upgrade",
            str(body),
        )
    else:
        print(f"  INFO no active subscription → upgrade returned {resp.status_code} (expected 404)")

    print("\n=== 6. Transactions (ledger readable) ===")
    resp = client.get("/api/v1/billing/transactions", headers=headers)
    check(resp.status_code == 200, "transactions 200")
    txns = resp.json()
    check(isinstance(txns, list), "transactions is a list", f"count={len(txns)}")

    print()
    if failures:
        print(f"=== FAILED: {len(failures)} check(s) ===")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print("=== ALL BILLING E2E CHECKS PASSED ===")


if __name__ == "__main__":
    main()
