"""Tests for app.scripts.audit_artifacts.

The audit script needs a live DB for full reports, so these tests target the
pure classification functions and the CLI defaults wiring (no DB required).
"""

from pathlib import Path

import pytest

from app.scripts import audit_artifacts


def _make_pdf(root: Path, rel: str) -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"%PDF-1.4")
    return p


# ── CV classification ──────────────────────────────────────────────


def test_classify_cv_files_active_deleted_orphan_unexpected(tmp_path):
    root = tmp_path / "generated_cvs"
    _make_pdf(root, "user-a/abc.pdf")           # active
    _make_pdf(root, "user-a/def.pdf")           # deleted
    _make_pdf(root, "user-b/zzz.pdf")           # orphan
    _make_pdf(root, "nested/other/whatever.pdf")  # still a {user}/{id} layout → orphan
    _make_pdf(root, "root.pdf")                 # single component → unexpected

    rows = {"abc": ("user-a", False), "def": ("user-a", True)}

    items, unexpected = audit_artifacts._classify_cv_files(root, rows)

    by_path = {i["rel"]: i["category"] for i in items}
    assert by_path["user-a/abc.pdf"] == "active"
    assert by_path["user-a/def.pdf"] == "deleted"
    assert by_path["user-b/zzz.pdf"] == "orphan"
    assert by_path["nested/other/whatever.pdf"] == "orphan"
    assert by_path["root.pdf"] == "unexpected"
    assert unexpected == [root / "root.pdf"]


def test_classify_cv_files_ignores_empty_root(tmp_path):
    items, unexpected = audit_artifacts._classify_cv_files(
        tmp_path / "missing", {}
    )
    assert items == []
    assert unexpected == []


# ── Apply classification ───────────────────────────────────────────


def test_classify_apply_files_referenced_and_orphan(tmp_path):
    root = tmp_path / "generated"
    keep = _make_pdf(root, "user-1/job-a/cv_x.pdf")
    _make_pdf(root, "user-1/job-b/cv_y.pdf")

    items = audit_artifacts._classify_apply_files(root, {keep.absolute()})

    by_path = {i["rel"]: i["category"] for i in items}
    assert by_path["user-1/job-a/cv_x.pdf"] == "referenced"
    assert by_path["user-1/job-b/cv_y.pdf"] == "orphan"


# ── CLI defaults follow settings (no hardcoded root) ───────────────


def test_generated_root_default_uses_settings(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)

    class _Settings:
        cv_storage_path = "generated_cvs_test"
        generated_storage_path = "generated_test"
        tracker_path = "documents/tracker.json"

    monkeypatch.setattr(audit_artifacts, "get_settings", lambda: _Settings())

    args = audit_artifacts._parse_args([])

    assert args.storage_root == str(tmp_path / "generated_cvs_test")
    assert args.generated_root == str(tmp_path / "generated_test")