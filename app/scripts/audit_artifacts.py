"""Fase 0 audit — classify files under the artifact storage dirs.

Dry-run only: this script never mutates the disk or the database. It reports
how the files under ``generated_cvs/`` (CV generator) and ``generated/``
(apply pipeline) relate to their database rows so we get the real numbers
before touching any production code.

Classification (``{cv_storage_path}/{user}/{cv_id}.pdf``, keyed by cv_id):

- active   : referenced by a ``generated_cvs`` row with ``is_deleted = False``
- deleted  : referenced by a row with ``is_deleted = True``
- orphan   : no matching row (disk leak)
- unexpected: file layout that does not match ``{user}/{uuid}.pdf``

Classification (``{generated_path}/{user}/{job}/{name}.pdf``, apply pipeline):

- referenced: exact path stored in an ``Application.cv_pdf_path`` /
              ``cover_letter_pdf_path`` column
- orphan    : not referenced by any ``Application`` row

The report also flags:
- active rows whose file is missing on disk (broken download)
- rows with ``pdf_path = NULL`` (compilation failed or never produced)
- empty user directories

Usage:
    python -m app.scripts.audit_artifacts \
        [--storage-root PATH] [--generated-root PATH] [--output PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text

from app.core.settings import get_settings
from app.services import artifact_store

# ── Filesystem ──────────────────────────────────────────────────────


def _resolve(path: str | Path) -> Path:
    p = Path(path)
    return p if p.is_absolute() else Path.cwd() / p


def _pdf_files(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return [p for p in sorted(root.rglob("*.pdf")) if p.is_file()]


def _empty_dirs(root: Path) -> list[Path]:
    if not root.exists() or not root.is_dir():
        return []
    return [d for d in sorted(root.rglob("*")) if d.is_dir() and not any(d.iterdir())]


def _human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} GB"


# ── Database ────────────────────────────────────────────────────────


async def _collect_cv_rows() -> dict[str, tuple[str, bool]]:
    """Return {cv_id: (user_id, is_deleted)} for every generated_cvs row."""
    from app.db.session import engine

    async with engine.connect() as conn:
        result = await conn.execute(
            text("SELECT id, user_id, is_deleted FROM generated_cvs")
        )
        return {row[0]: (row[1], bool(row[2])) for row in result.all()}


async def _collect_application_paths() -> set[Path]:
    """Return the set of normalized files referenced by Application PDF columns."""
    from app.db.session import engine

    async with engine.connect() as conn:
        result = await conn.execute(
            text(
                "SELECT cv_pdf_path, cover_letter_pdf_path FROM applications "
                "WHERE cv_pdf_path IS NOT NULL OR cover_letter_pdf_path IS NOT NULL"
            )
        )
    paths: set[Path] = set()
    for row in result.all():
        for value in row:
            resolved = artifact_store.resolve_existing("apply", value)
            if resolved is not None:
                paths.add(resolved.absolute())
    return paths


# ── Classification ──────────────────────────────────────────────────


def _classify_cv_files(
    root: Path,
    rows: dict[str, tuple[str, bool]],
) -> tuple[list[dict[str, Any]], list[Path]]:
    classified: list[dict[str, Any]] = []
    unexpected: list[Path] = []
    for file in _pdf_files(root):
        rel = file.relative_to(root)
        parts = rel.parts
        size = file.stat().st_size
        if len(parts) >= 2 and rel.suffix.lower() == ".pdf":
            key = file.stem
            if key in rows:
                _, is_deleted = rows[key]
                category = "deleted" if is_deleted else "active"
            else:
                category = "orphan"
        else:
            category = "unexpected"
            unexpected.append(file)
        classified.append(
            {"file": file, "rel": rel.as_posix(), "size": size, "category": category}
        )
    return classified, unexpected


def _classify_apply_files(
    root: Path,
    referenced: set[Path],
) -> list[dict[str, Any]]:
    classified: list[dict[str, Any]] = []
    for file in _pdf_files(root):
        absolute = file.absolute()
        category = "referenced" if absolute in referenced else "orphan"
        classified.append(
            {
                "file": file,
                "rel": file.relative_to(root).as_posix(),
                "size": file.stat().st_size,
                "category": category,
            }
        )
    return classified


# ── Report ──────────────────────────────────────────────────────────


def _summarize(items: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    summary: dict[str, dict[str, int]] = {}
    for item in items:
        cat = item["category"]
        entry = summary.setdefault(cat, {"count": 0, "bytes": 0})
        entry["count"] += 1
        entry["bytes"] += item["size"]
    return summary


def _render_markdown(
    *,
    cv_root: Path,
    apply_root: Path,
    cv_items: list[dict[str, Any]],
    apply_items: list[dict[str, Any]],
    empty_dirs: list[Path],
    db_ok: bool,
    active_missing: list[dict[str, Any]],
    rows_no_pdf: int,
) -> str:
    lines: list[str] = []
    lines.append("# Artifact storage audit")
    lines.append("")
    lines.append(
        f"- Storage roots: `{cv_root}` (CV generator), `{apply_root}` (apply pipeline)"
    )
    lines.append(f"- Database reachable: {db_ok}")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append("| Category | Count | Bytes | Recommended action |")
    lines.append("|---|---:|---:|---|")
    order = {
        "active": "keep",
        "referenced": "keep",
        "deleted": "remove file (retention 0 — file is derived; row kept for audit)",
        "orphan": "remove file",
        "unexpected": "review before removing",
    }
    for cat in ("active", "referenced", "deleted", "orphan", "unexpected"):
        if cat not in order:
            continue
        merged = _merge_categories(cat, cv_items, apply_items)
        action = order.get(cat, "review")
        lines.append(
            f"| {cat} | {merged['count']} | {_human(merged['bytes'])} | {action} |"
        )
    lines.append("")
    lines.append(f"- Empty directories: **{len(empty_dirs)}**")
    lines.append(f"- Active rows with a missing file (broken download): **{len(active_missing)}**")
    lines.append(f"- Rows with `pdf_path = NULL` (compile failed): **{rows_no_pdf}**")

    if db_ok and cv_items:
        lines.append("")
        lines.append("## CV generator files")
        lines.append("")
        lines.append("| Category | File | Bytes |")
        lines.append("|---|---:|---:|")
        for item in cv_items:
            lines.append(f"| {item['category']} | `{item['rel']}` | {item['size']} |")
    if db_ok and apply_items:
        lines.append("")
        lines.append("## Apply pipeline files (`generated/`)")
        lines.append("")
        lines.append("| Category | File | Bytes |")
        lines.append("|---|---:|---:|")
        for item in apply_items:
            lines.append(f"| {item['category']} | `{item['rel']}` | {item['size']} |")
    if empty_dirs:
        lines.append("")
        lines.append("## Empty directories")
        lines.append("")
        for d in empty_dirs:
            lines.append(f"- `{d}`")
    lines.append("")
    return "\n".join(lines)


def _merge_categories(cat: str, *groups: list[dict[str, Any]]) -> dict[str, int]:
    count = 0
    total = 0
    for group in groups:
        for item in group:
            if item["category"] == cat:
                count += 1
                total += item["size"]
    return {"count": count, "bytes": total}


# ── Entrypoint ──────────────────────────────────────────────────────


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Dry-run audit of artifact storage vs database rows."
    )
    settings = get_settings()
    parser.add_argument(
        "--storage-root",
        default=str(_resolve(settings.cv_storage_path)),
        help="Root of the CV generator PDFs (default: settings.cv_storage_path).",
    )
    parser.add_argument(
        "--generated-root",
        default=str(_resolve(settings.generated_storage_path)),
        help="Root of the apply pipeline PDFs (default: settings.generated_storage_path).",
    )
    parser.add_argument(
        "--output",
        default=str(Path(__file__).resolve().parents[2] / "docs" / "artifact-audit-results.md"),
        help="Path for the markdown report (default: docs/artifact-audit-results.md).",
    )
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    cv_root = _resolve(args.storage_root)
    apply_root = _resolve(args.generated_root)

    db_ok = True
    rows: dict[str, tuple[str, bool]] = {}
    referenced: set[Path] = set()
    try:
        rows = await _collect_cv_rows()
        referenced = await _collect_application_paths()
    except Exception as exc:  # noqa: BLE001 — report must not crash on DB issues
        db_ok = False
        print(f"[audit] database unavailable — filesystem-only audit: {exc}", file=sys.stderr)

    cv_items, _unexpected = _classify_cv_files(cv_root, rows)
    apply_items = _classify_apply_files(apply_root, referenced)

    # Row-level findings.
    active_missing: list[dict[str, Any]] = []
    rows_no_pdf = 0
    if db_ok and cv_items:
        from app.db.session import engine

        async with engine.connect() as conn:
            result = await conn.execute(
                text("SELECT id, pdf_path, is_deleted FROM generated_cvs")
            )
            for row in result.all():
                cv_id, pdf_path, is_deleted = row[0], row[1], bool(row[2])
                if pdf_path is None:
                    rows_no_pdf += 1
                    continue
                if is_deleted:
                    continue
                resolved = artifact_store.resolve_existing("cv", pdf_path)
                if resolved is None or not resolved.exists():
                    active_missing.append({"id": cv_id, "pdf_path": pdf_path})

    empty_dirs = _empty_dirs(cv_root) + _empty_dirs(apply_root)

    report = _render_markdown(
        cv_root=cv_root,
        apply_root=apply_root,
        cv_items=cv_items,
        apply_items=apply_items,
        empty_dirs=empty_dirs,
        db_ok=db_ok,
        active_missing=active_missing,
        rows_no_pdf=rows_no_pdf,
    )

    summary = _summarize(cv_items + apply_items)
    lines_out = (
        f"{cat:>11}: {count:>3} files, {_human(bytes_)}"
        for cat, count, bytes_ in (
            (cat, e["count"], e["bytes"]) for cat, e in summary.items()
        )
    )
    print("\n".join(lines_out))
    if not db_ok:
        print("DB unavailable — active/deleted classification missing")
    empty_msg = (
        f"empty dirs: {len(empty_dirs)} | active rows missing file: "
        f"{len(active_missing)} | rows without pdf: {rows_no_pdf}"
    )
    print(empty_msg)

    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(f"[audit] report written to {out}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
