"""Tests for the central artifact storage service (Fase 1b).

Uses ``monkeypatch.chdir`` so the scope roots resolve inside a temp dir,
mirroring the default (CWD-relative) configuration used by the app.
"""

import pytest

from app.services import artifact_store
from app.services.artifact_store import (
    new_output_path,
    remove_file,
    remove_user_dir,
    resolve,
    resolve_existing,
    safe_name,
    write_bytes,
)


@pytest.fixture
def settings(monkeypatch, tmp_path):
    """Point artifact_store at temp storage roots, anchored to a tmp CWD."""
    monkeypatch.chdir(tmp_path)

    class _Settings:
        cv_storage_path = "generated_cvs"
        generated_storage_path = "generated"

    monkeypatch.setattr(artifact_store, "get_settings", lambda: _Settings())
    return _Settings


# ── new_output_path ────────────────────────────────────────────────


def test_new_output_path_creates_dirs_and_returns_rel_path(settings, tmp_path):
    abs_path, rel = new_output_path("cv", "user-1", "abc123.pdf")

    assert abs_path.is_absolute()
    assert abs_path.parent.exists()
    assert rel == "generated_cvs/user-1/abc123.pdf"
    assert abs_path == tmp_path / "generated_cvs" / "user-1" / "abc123.pdf"


def test_new_output_path_sanitizes_display_filename(settings):
    _, rel = new_output_path("apply", "user-1", "job-abc", "cv_Acme/Sales Manager:NY.pdf ")
    # '/' ':' and trailing/leading junk replaced/stripped; no sub-directories.
    assert rel == "generated/user-1/job-abc/cv_Acme-Sales Manager-NY.pdf"
    assert "/" not in rel.split("cv_")[1]


def test_new_output_path_rejects_unsafe_user_id(settings):
    with pytest.raises(ValueError):
        new_output_path("cv", "../evil", "x.pdf")
    with pytest.raises(ValueError):
        new_output_path("cv", "a/b", "x.pdf")


def test_new_output_path_unknown_scope(settings):
    with pytest.raises(ValueError):
        new_output_path("nope", "user-1", "x.pdf")


# ── safe_name ──────────────────────────────────────────────────────


def test_safe_name_strips_dangerous_characters():
    assert safe_name('a/b\\c:d*e?f"g<h>i|j', fallback="f") == "a-b-c-d-e-f-g-h-i-j"


def test_safe_name_collapses_dots_and_falls_back(settings):
    assert safe_name("..") == "artifact"
    assert safe_name("../../../..") not in {".", ".."}  # never a traversal form
    assert "/" not in safe_name("../../../..")
    assert safe_name("Name..with..dots") == "Name.with.dots"
    assert safe_name("   ") == "artifact"


# ── resolve / resolve_existing ─────────────────────────────────────


def test_resolve_stays_inside_root(settings):
    abs_path, rel = new_output_path("cv", "user-1", "abc123.pdf")
    abs_path.write_bytes(b"%PDF")

    assert resolve("cv", rel) == abs_path
    assert resolve("cv", rel).read_bytes() == b"%PDF"


def test_resolve_rejects_traversal(settings):
    with pytest.raises(ValueError):
        resolve("cv", "generated_cvs/../../secret.txt")
    with pytest.raises(ValueError):
        resolve("cv", "../../etc/passwd")


def test_resolve_rejects_empty(settings):
    with pytest.raises(ValueError):
        resolve("cv", "")


def test_resolve_absolute_passthrough(settings, tmp_path):
    target = tmp_path / "elsewhere" / "x.pdf"
    target.parent.mkdir()
    target.write_bytes(b"data")
    assert resolve("cv", str(target)) == target


def test_resolve_existing_handles_none_empty_and_absolute(settings, tmp_path):
    assert resolve_existing("cv", None) is None
    assert resolve_existing("cv", "") is None
    target = tmp_path / "abs.pdf"
    target.write_bytes(b"d")
    assert resolve_existing("cv", str(target)) == target


# ── write_bytes / remove_file ──────────────────────────────────────


def test_write_bytes_stores_payload(settings):
    rel = "generated_cvs/user-1/out.pdf"
    path = write_bytes("cv", rel, b"%PDF-1.4")
    assert path.read_bytes() == b"%PDF-1.4"
    assert resolve("cv", rel).exists()


def test_remove_file_idempotent(settings):
    _, rel = new_output_path("cv", "user-1", "todelete.pdf")
    write_bytes("cv", rel, b"data")

    assert remove_file("cv", rel) is True
    assert not resolve("cv", rel).exists()
    # Second call (file already gone) must not raise.
    assert remove_file("cv", rel) is True
    # None / empty handled gracefully.
    assert remove_file("cv", None) is False


def test_remove_file_rejects_traversal(settings):
    assert remove_file("cv", "../escape.pdf") is False


# ── remove_user_dir ───────────────────────────────────────────────


def test_remove_user_dir_removes_whole_folder(settings):
    new_output_path("apply", "user-1", "job-a", "cv_x.pdf")
    new_output_path("apply", "user-1", "job-b", "cv_y.pdf")
    write_bytes("apply", "generated/user-1/job-a/cv_x.pdf", b"a")
    write_bytes("apply", "generated/user-1/job-b/cv_y.pdf", b"b")

    assert remove_user_dir("apply", "user-1") is True
    assert not resolve("apply", "generated/user-1").exists()


def test_remove_user_dir_idempotent_and_safe(settings):
    assert remove_user_dir("apply", "ghost-user") is True  # nothing to remove
    assert remove_user_dir("apply", "../evil") is False  # never escapes
    assert remove_user_dir("apply", "a/b") is False
