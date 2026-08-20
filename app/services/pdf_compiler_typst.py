"""Typst PDF compiler — in-process, no subprocess, no temp files.

Usage:
    from app.services.pdf_compiler_typst import compile_cv
    pdf_bytes = compile_cv(cv_dict)     # returns bytes
    compile_cv(cv_dict, output_path)     # writes to file
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import typst

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "external" / "typst"
ENTRY_TYP = TEMPLATE_DIR / "entry.typ"
STAGING_JSON = TEMPLATE_DIR / "_cv_data.json"


def compile_cv(
    cv_data: dict[str, Any],
    output: str | Path | None = None,
) -> bytes | None:
    """Render a CV dict to PDF using Typst.

    Args:
        cv_data: The ``GenerateCVOutput`` dict (with ``cv`` and ``metadata``)
                 or just the ``CV`` dict (renderable part only).
        output: Optional output path. If None, returns PDF bytes.

    Returns:
        PDF bytes if ``output`` is None, else None.
    """
    if "cv" not in cv_data:
        cv_data = {"cv": cv_data}

    with open(STAGING_JSON, "w", encoding="utf-8") as f:
        json.dump(cv_data, f, ensure_ascii=False)

    try:
        result = typst.compile(
            str(ENTRY_TYP),
            output=str(output) if output else None,
            root=str(TEMPLATE_DIR),
        )
        if output is None:
            return result
        return None
    finally:
        if STAGING_JSON.exists():
            STAGING_JSON.unlink()
