"""Central artifact storage service (Fase 1b).

Single source of truth for where generated artifacts (compiled PDFs) live on
disk. It sanitizes every path component and provides safe resolve/remove
helpers so download, verification and cleanup code can never escape the
configured storage roots.

Two scopes map to settings attributes:

* ``"cv"``    -> ``settings.cv_storage_path``        (default: ``generated_cvs``)
* ``"apply"`` -> ``settings.generated_storage_path`` (default: ``generated``)

Path convention: persisted paths keep the legacy format (relative to the
working directory, with the scope root as the first component), so rows
already in the DB keep resolving. ``resolve()`` enforces that any relative
path resolved for reading stays inside its scope root.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.core.logging import get_logger
from app.core.settings import get_settings
from app.services import r2_storage

logger = get_logger(__name__)

# scope -> settings attribute holding the storage root.
SCOPE_SETTINGS = {
    "cv": "cv_storage_path",
    "apply": "generated_storage_path",
}

# Everything that must never appear inside a persisted path component.
_FORBIDDEN_NAME_RE = re.compile(r'[/\\:*?"<>|\x00-\x1f]+')

_MAX_NAME_LEN = 120


def safe_name(value: str, fallback: str = "artifact") -> str:
    """Sanitize a display-derived filename segment (e.g. company/title).

    Removes path separators and characters that are invalid or dangerous on
    common filesystems, collapses whitespace and repeated dots, and trims
    leading/trailing dots so the result is always a safe, single component.
    """
    s = str(value or "").strip()
    s = _FORBIDDEN_NAME_RE.sub("-", s)
    s = re.sub(r"\s+", " ", s)
    s = s.strip(" .")
    s = re.sub(r"\.{2,}", ".", s)
    s = s[:_MAX_NAME_LEN].rstrip(" .")
    if not s or s in {".", ".."}:
        s = fallback
    return s


def _check_component(value: str, label: str) -> str:
    """Validate a caller-supplied path component (ids, user ids). Raises."""
    s = str(value or "").strip()
    if not s or s in {".", ".."} or "/" in s or "\\" in s or "\x00" in s:
        raise ValueError(f"Invalid {label} {value!r}: must be a single safe path component.")
    return s


def _scope_attr(scope: str) -> str:
    """Settings attribute holding the storage root for a scope. Raises."""
    attr = SCOPE_SETTINGS.get(scope)
    if attr is None:
        raise ValueError(f"Unknown artifact scope {scope!r} (known: {sorted(SCOPE_SETTINGS)})")
    return attr


def _root(scope: str) -> Path:
    """Absolute root directory for a scope (anchored to CWD if relative)."""
    return Path(getattr(get_settings(), _scope_attr(scope))).resolve()


def new_output_path(scope: str, user_id: str, *parts: str) -> tuple[Path, str]:
    """Plan a new artifact: returns ``(absolute_path, stored_rel_path)``.

    ``stored_rel_path`` is what callers should persist (legacy convention:
    relative to the working directory, scope root first). All parent
    directories are created. The last ``part`` is treated as a display-derived
    filename and sanitized; the remaining parts must be clean id segments.
    """
    user = _check_component(user_id, "user_id")
    safe_parts: list[str] = []
    for i, part in enumerate(parts):
        if i == len(parts) - 1:
            safe_parts.append(safe_name(part))
        else:
            safe_parts.append(_check_component(part, "artifact segment"))

    root_val = Path(getattr(get_settings(), _scope_attr(scope)))
    stored_rel = root_val / user / Path(*safe_parts)
    abs_path = (_root(scope) / user / Path(*safe_parts)).resolve(strict=False)
    abs_path.parent.mkdir(parents=True, exist_ok=True)
    return abs_path, stored_rel.as_posix()


def resolve(scope: str, rel: str | Path) -> Path:
    """Resolve a stored relative path to an absolute one, safely.

    Raises ``ValueError`` if the path is absolute or would escape the scope
    root (e.g. via ``..`` traversal). Relative paths are anchored to the
    working directory, matching the legacy convention.
    """
    if not rel:
        raise ValueError("Cannot resolve an empty artifact path.")
    p = Path(str(rel))
    if p.is_absolute():
        return p
    root = _root(scope)
    candidate = (Path.cwd() / p).resolve(strict=False)
    if not candidate.is_relative_to(root):
        raise ValueError(f"Artifact path {rel!r} escapes scope {scope!r} root.")
    return candidate


def resolve_existing(scope: str, value: str | Path | None) -> Path | None:
    """Lenient resolve used by readers (download, verification).

    Absolute paths pass through (admin-configured absolute roots). Relative
    paths are resolved within the scope root; on failure (legacy/odd values)
    falls back to CWD resolution to preserve previous behaviour.
    """
    if not value:
        return None
    p = Path(str(value))
    if p.is_absolute():
        return p
    try:
        return resolve(scope, str(value))
    except ValueError:
        return (Path.cwd() / p).resolve(strict=False)


def write_bytes(scope: str, rel: str | Path, data: bytes) -> Path:
    """Write raw bytes to a stored artifact path (creating parents)."""
    path = resolve(scope, rel)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def remove_file(scope: str, rel: str | Path | None) -> bool:
    """Best-effort, idempotent removal of a single artifact file.

    When R2 is configured, deletes from R2 instead of local disk.
    """
    if not rel:
        return False
    # R2 configured: delete from cloud storage
    if r2_storage._r2_configured():
        return r2_storage.delete_pdf(str(rel))
    # Fallback: delete from local disk
    try:
        resolve(scope, rel).unlink(missing_ok=True)
        return True
    except (OSError, ValueError):
        logger.warning("Failed to remove artifact %r (scope=%s)", rel, scope, exc_info=True)
        return False


def remove_user_dir(scope: str, user_id: str) -> bool:
    """Best-effort, idempotent removal of a user's whole artifact folder.

    When R2 is configured, deletes the user's prefix from R2.
    """
    # R2 configured: delete user prefix from cloud storage
    if r2_storage._r2_configured():
        deleted = r2_storage.delete_user_prefix(user_id)
        return deleted >= 0  # No failure if no objects existed
    # Fallback: delete local directory
    try:
        user = _check_component(user_id, "user_id")
    except ValueError:
        return False
    target = _root(scope) / user
    try:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            return not target.exists()
        return True
    except OSError:
        return False
