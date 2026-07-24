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
    # Map of standard binary names -> miktex-prefixed names
    _BINARY_ALIASES = {
        "lualatex.exe": "miktex-luatex.exe",
        "xelatex.exe": "miktex-xetex.exe",
        "pdfinfo.exe": "miktex-pdfinfo.exe",
        "pdftotext.exe": "miktex-pdftotext.exe",
    }
    default_install = Path("C:/miktex-portable/texmfs/install")
    default_bin = default_install / "miktex" / "bin" / "x64"

    print(" extrayendo (puede tomar varios minutos)...")

    # 1. Intentar instalación directa en nuestra carpeta
    result = subprocess.run(
        [
            str(MIKTEX_INSTALLER),
            "--unattended",
            "--portable",
            str(MIKTEX_TARGET),
        ],
        capture_output=True, text=True, timeout=600,
    )

    # Verificar por binarios, no por exit code (post-install warnings son inofensivos)
    _ensure_binary_aliases(MIKTEX_BIN_DIR, _BINARY_ALIASES)
    if _is_installed():
        print(f"OK: Binarios en {MIKTEX_BIN_DIR}")
        return True

    # 2. Fallback: el instalador ignoró --portable y fue a C:\miktex-portable\
    if default_bin.is_dir():
        print("  (instalado en ruta default, copiando al proyecto...)", file=sys.stderr)
        MIKTEX_TARGET.mkdir(parents=True, exist_ok=True)
        for item in default_install.iterdir():
            dst = MIKTEX_TARGET / item.name
            if item.is_dir():
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(item, dst)
        shutil.rmtree("C:/miktex-portable", ignore_errors=True)
        _ensure_binary_aliases(MIKTEX_BIN_DIR, _BINARY_ALIASES)
        if _is_installed():
            print(f"OK: Binarios en {MIKTEX_BIN_DIR}")
            return True

    print("ERROR: No se encontraron binarios tras la instalación.", file=sys.stderr)
    print(f"  Buscado en: {MIKTEX_BIN_DIR}", file=sys.stderr)
    print(f"  Buscado en: {default_bin}", file=sys.stderr)
    if result.stdout:
        _filter_miktex_output(result.stdout)
    if result.stderr:
        _filter_miktex_output(result.stderr, prefix="stderr")
    return False


def _ensure_binary_aliases(bin_dir: Path, aliases: dict[str, str]) -> None:
    """Crear wrappers para nombres estándar (lualatex.exe → miktex-luatex.exe)."""
    import shutil
    if not bin_dir.is_dir():
        return
    for std_name, miktex_name in aliases.items():
        std_path = bin_dir / std_name
        miktex_path = bin_dir / miktex_name
        if not std_path.is_file() and miktex_path.is_file():
            shutil.copy2(miktex_path, std_path)
            print(f"  -> {std_name}")


def _filter_miktex_output(text: str, prefix: str = "stdout") -> None:
    """Mostrar output del instalador filtrando warnings conocidos."""
    _SAFE_LINES = [
        "option --admin only makes sense for a shared MiKTeX setup",
        "The executed process did not succeed",
        "log4cxx: No appender could be found",
        "log4cxx: Please initialize the log4cxx system properly",
    ]
    for line in text.splitlines():
        if not any(s in line for s in _SAFE_LINES):
            print(f"  [{prefix}] {line}")


_FMT_ENGINES = {
    "lualatex.fmt": ("miktex-luatex.exe", ["-ini", "-jobname=lualatex", "lualatex.ini"]),
    "xelatex.fmt":  ("miktex-xetex.exe",  ["-ini", "-etex", "-jobname=xelatex", "xelatex.ini"]),
}


def _build_formats() -> None:
    """Build LaTeX format files (lualatex.fmt, xelatex.fmt) via engine -ini."""
    fmt_dir = MIKTEX_TARGET / "miktex" / "fmt"
    if all((fmt_dir / f).is_file() for f in _FMT_ENGINES):
        return

    print(" Generando formatos LaTeX...")
    fmt_dir.mkdir(parents=True, exist_ok=True)
    cwd = Path.cwd()

    for fmt_name, (engine, args) in _FMT_ENGINES.items():
        fmt_path = fmt_dir / fmt_name
        if fmt_path.is_file():
            continue
        engine_path = MIKTEX_BIN_DIR / engine
        if not engine_path.is_file():
            print(f"WARNING: {engine} no encontrado, saltando {fmt_name}", file=sys.stderr)
            continue
        print(f"  {fmt_name} ...")
        subprocess.run(
            [str(engine_path)] + args,
            timeout=120, capture_output=True,
        )
        src = cwd / fmt_name
        if src.is_file():
            src.replace(fmt_path)
            print(f"    OK")
        else:
            print(f"    ERROR: no se generó {fmt_name}", file=sys.stderr)

    if all((fmt_dir / f).is_file() for f in _FMT_ENGINES):
        print(f"OK: Formatos en {fmt_dir}")
    else:
        print("WARNING: Faltan formatos (LaTeX puede fallar)", file=sys.stderr)


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
        _build_formats()
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

    _build_formats()

    _write_env()
    print("OK: MiKTeX Portable listo para usar")
    return 0


if __name__ == "__main__":
    sys.exit(main())
