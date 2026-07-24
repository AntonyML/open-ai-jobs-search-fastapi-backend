#!/usr/bin/env python3
"""Auto-install MiKTeX Portable for local Windows development.

Downloads from GitHub Releases (or MIKTEX_DOWNLOAD_URL env var),
extracts silently via NSIS, verifies binaries, and writes
LATEX_BIN_DIR to .env.

Idempotent — skips download/extract if binaries already present.
Windows-only; Linux/Mac prints a message and exits 0.
"""

import os
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MIKTEX_TARGET = PROJECT_ROOT / "app" / "external" / "latex" / "miktex-portable"
MIKTEX_BIN_DIR = MIKTEX_TARGET / "miktex" / "bin" / "x64"
MIKTEX_INSTALLER_DIR = PROJECT_ROOT / "MikTex"
MIKTEX_INSTALLER = MIKTEX_INSTALLER_DIR / "miktex-portable.exe"
REQUIRED_BINARIES = ["lualatex.exe", "xelatex.exe", "pdfinfo.exe", "pdftotext.exe"]
ENV_FILE = PROJECT_ROOT / ".env"

# Constructed from git remote, or overridden via MIKTEX_DOWNLOAD_URL
_GITHUB_RELEASE_TPL = (
    "https://github.com/{owner}/{repo}/releases/download/latex-v1/miktex-portable.exe"
)


def _get_github_repo() -> tuple[str, str] | None:
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, cwd=PROJECT_ROOT, timeout=10,
        )
        m = re.search(
            r"(?:github\.com[/:])"
            r"([\w\-]+)/([\w\-]+?)(?:\.git)?$",
            r.stdout.strip(),
        )
        if m:
            return m.group(1), m.group(2)
    except Exception:
        pass
    return None


def _resolve_download_url() -> str:
    url = os.environ.get("MIKTEX_DOWNLOAD_URL")
    if url:
        return url
    repo = _get_github_repo()
    if repo:
        return _GITHUB_RELEASE_TPL.format(owner=repo[0], repo=repo[1])
    print(
        "WARNING: No se pudo determinar URL de descarga. "
        "Definí MIKTEX_DOWNLOAD_URL en .env o crea un Release latex-v1.",
        file=sys.stderr,
    )
    return ""


def _is_installed() -> bool:
    if not MIKTEX_BIN_DIR.is_dir():
        return False
    return all((MIKTEX_BIN_DIR / b).is_file() for b in REQUIRED_BINARIES)


def _download_installer(url: str) -> None:
    MIKTEX_INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Descargando MiKTeX Portable desde:\n  {url}")
    urllib.request.urlretrieve(url, MIKTEX_INSTALLER)
    print(f"OK: Descargado a {MIKTEX_INSTALLER}")


def _extract_installer() -> None:
    print(f" extrayendo a {MIKTEX_TARGET} ...")
    MIKTEX_TARGET.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(MIKTEX_INSTALLER), "/S", f"/D={MIKTEX_TARGET}"],
        check=True, timeout=300,
    )
    print("OK: Extracción completada")


def _write_env() -> None:
    if not ENV_FILE.is_file():
        print("WARNING: .env no existe, no se puede escribir LATEX_BIN_DIR")
        return
    rel = MIKTEX_BIN_DIR.relative_to(PROJECT_ROOT).as_posix()
    line = f"LATEX_BIN_DIR={rel}"
    content = ENV_FILE.read_text(encoding="utf-8")
    if re.search(r"^LATEX_BIN_DIR=", content, re.MULTILINE):
        content = re.sub(r"^LATEX_BIN_DIR=.*$", line, content, flags=re.MULTILINE)
    else:
        content += f"\n# LaTeX binaries (auto-set by setup_latex.py)\n{line}\n"
    ENV_FILE.write_text(content, encoding="utf-8")
    print(f"OK: LATEX_BIN_DIR={rel} escrito en .env")


def main() -> int:
    if sys.platform != "win32":
        print("Nota: setup_latex.py es solo para Windows.")
        print("  En Docker: MiKTeX se instala via apt (ver Dockerfile).")
        print("  En Linux/Mac: instala TeX Live manualmente o usa Docker.")
        return 0

    print("=== setup_latex.py ===")

    if _is_installed():
        print(f"OK: MiKTeX Portable ya instalado en {MIKTEX_BIN_DIR}")
        return 0

    url = _resolve_download_url()
    if not url:
        print("ERROR: No hay URL de descarga. Abortando.", file=sys.stderr)
        return 1

    if not MIKTEX_INSTALLER.is_file():
        _download_installer(url)
    else:
        print(f"OK: Instalador ya existe: {MIKTEX_INSTALLER}")

    _extract_installer()

    if not _is_installed():
        print("ERROR: La extracción no produjo los binarios esperados", file=sys.stderr)
        return 1

    _write_env()
    print("OK: MiKTeX Portable listo para usar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
