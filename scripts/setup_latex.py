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

# Public repo con el installer (para no depender de auth)
_PUBLIC_DOWNLOAD_URL = (
    "https://github.com/AntonyML/miktex-portable/releases/download/latex-v1/miktex-portable.exe"
)


def _resolve_download_url() -> str:
    return os.environ.get("MIKTEX_DOWNLOAD_URL") or _PUBLIC_DOWNLOAD_URL


def _is_installed() -> bool:
    if not MIKTEX_BIN_DIR.is_dir():
        return False
    return all((MIKTEX_BIN_DIR / b).is_file() for b in REQUIRED_BINARIES)


def _download_installer(url: str) -> None:
    MIKTEX_INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Descargando MiKTeX Portable desde:\n  {url}")
    urllib.request.urlretrieve(url, MIKTEX_INSTALLER)
    print(f"OK: Descargado a {MIKTEX_INSTALLER}")


def _extract_installer() -> bool:
    import shutil
    default_install = Path("C:/miktex-portable/texmfs/install")

    print(" extrayendo...")
    subprocess.run([str(MIKTEX_INSTALLER), "/S"], check=True, timeout=300)

    src_bin = default_install / "miktex" / "bin" / "x64"
    if not src_bin.is_dir():
        print(f"ERROR: No se encuentra {src_bin} tras la extracción", file=sys.stderr)
        return False

    MIKTEX_TARGET.mkdir(parents=True, exist_ok=True)
    for item in default_install.iterdir():
        dst = MIKTEX_TARGET / item.name
        if item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)
        else:
            shutil.copy2(item, dst)

    shutil.rmtree("C:/miktex-portable", ignore_errors=True)
    print(f"OK: Binarios en {MIKTEX_BIN_DIR}")
    return True


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

    if not _extract_installer():
        return 1

    if not _is_installed():
        print("ERROR: La extracción no produjo los binarios esperados", file=sys.stderr)
        return 1

    _write_env()
    print("OK: MiKTeX Portable listo para usar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
