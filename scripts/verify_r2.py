#!/usr/bin/env python3
"""Verify Cloudflare R2 connection and configuration.

Usage:
    python scripts/verify_r2.py

Checks:
1. Settings are configured (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, etc.)
2. Can connect to R2 via boto3 S3 client
3. Bucket exists and is accessible
4. Can generate a signed URL
5. Can list objects (limited)
"""

from __future__ import annotations

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> None:
    print("=" * 60)
    print("Cloudflare R2 Connection Verification")
    print("=" * 60)

    # Step 1: Check settings
    print("\n[1/5] Checking settings...")
    try:
        from app.core.settings import get_settings

        settings = get_settings()
    except Exception as e:
        print(f"  [FAIL] Failed to load settings: {e}")
        sys.exit(1)

    required = ["r2_account_id", "r2_access_key_id", "r2_secret_access_key", "r2_bucket_name"]
    missing = [f for f in required if not getattr(settings, f, None)]
    if missing:
        print(f"  [WARN] Missing R2 settings: {', '.join(missing)}")
        print("  [INFO] R2 is not configured. The app will use local disk fallback.")
        print("\n  To configure R2, set these environment variables:")
        for f in required:
            print(f"    - {f.upper()}")
        sys.exit(0)

    print(f"  [OK] Account ID: {settings.r2_account_id[:8]}...")
    print(f"  [OK] Bucket: {settings.r2_bucket_name}")
    print(f"  [OK] Signed URL TTL: {settings.r2_signed_url_ttl}s")

    # Step 2: Create S3 client
    print("\n[2/5] Creating S3 client...")
    try:
        import boto3

        s3 = boto3.client(
            "s3",
            endpoint_url=f"https://{settings.r2_account_id}.r2.cloudflarestorage.com",
            aws_access_key_id=settings.r2_access_key_id,
            aws_secret_access_key=settings.r2_secret_access_key,
            region_name=getattr(settings, "r2_region", "auto"),
        )
        print("  [OK] S3 client created")
    except Exception as e:
        print(f"  [FAIL] Failed to create S3 client: {e}")
        sys.exit(1)

    # Step 3: Check bucket access
    print("\n[3/5] Checking bucket access...")
    try:
        s3.head_bucket(Bucket=settings.r2_bucket_name)
        print(f"  [OK] Bucket '{settings.r2_bucket_name}' exists and is accessible")
    except Exception as e:
        print(f"  [FAIL] Cannot access bucket: {e}")
        sys.exit(1)

    # Step 4: Generate signed URL
    print("\n[4/5] Generating test signed URL...")
    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": settings.r2_bucket_name, "Key": "test/verify.txt"},
            ExpiresIn=60,
        )
        print(f"  [OK] Signed URL generated ({len(url)} chars)")
        print(f"     {url[:80]}...")
    except Exception as e:
        print(f"  [FAIL] Failed to generate signed URL: {e}")
        sys.exit(1)

    # Step 5: List objects (limited)
    print("\n[5/5] Listing objects (first 10)...")
    try:
        resp = s3.list_objects_v2(Bucket=settings.r2_bucket_name, MaxKeys=10)
        objects = resp.get("Contents", [])
        print(f"  [OK] Found {resp.get('KeyCount', 0)} objects")
        for obj in objects[:5]:
            print(f"     - {obj['Key']} ({obj['Size']} bytes)")
        if resp.get("IsTruncated"):
            print("     ... and more")
    except Exception as e:
        print(f"  [WARN] Could not list objects: {e}")
        # Non-fatal — listing might be restricted

    print("\n" + "=" * 60)
    print("[OK] All checks passed! R2 is configured correctly.")
    print("=" * 60)


if __name__ == "__main__":
    main()