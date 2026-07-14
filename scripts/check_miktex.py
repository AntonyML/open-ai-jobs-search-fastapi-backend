#!/usr/bin/env python3
"""Verify MiKTeX Portable is installed and available.

Run this script manually or it will be called automatically during app startup.
"""

import sys
from pathlib import Path

# Expected location of the MiKTeX Portable installer
MIKTEX_INSTALLER = Path("MikTex/miktex-portable.exe")
# Expected location of the extracted binaries
MIKTEX_BIN_DIR = Path("app/external/latex/miktex-portable/miktex/bin/x64")

REQUIRED_BINARIES = ["lualatex.exe", "xelatex.exe", "pdfinfo.exe", "pdftotext.exe"]

DOWNLOAD_URL = "https://miktex.org/howto/portable-edition"


def check_miktex_portable() -> tuple[bool, list[str]]:
    """Check if MiKTeX Portable is properly installed.

    Returns:
        Tuple of (success: bool, messages: list[str])
    """
    messages = []

    # Check if installer exists (optional, but good to have)
    if not MIKTEX_INSTALLER.exists():
        messages.append(f"WARNING: Instalador no encontrado: {MIKTEX_INSTALLER}")
        messages.append(f"   Descargá desde: {DOWNLOAD_URL}")
        messages.append(f"   Colocalo en: {MIKTEX_INSTALLER.absolute()}")
    else:
        messages.append(f"OK: Instalador encontrado: {MIKTEX_INSTALLER}")

    # Check if binaries directory exists
    if not MIKTEX_BIN_DIR.exists():
        messages.append(f"ERROR: Directorio de binarios no existe: {MIKTEX_BIN_DIR}")
        messages.append("   Ejecutá el instalador (miktex-portable.exe) para extraer los binarios")
        return False, messages

    messages.append(f"OK: Directorio de binarios existe: {MIKTEX_BIN_DIR}")

    # Check each required binary
    missing = []
    for binary in REQUIRED_BINARIES:
        binary_path = MIKTEX_BIN_DIR / binary
        if not binary_path.exists():
            missing.append(binary)
        else:
            messages.append(f"OK: {binary} encontrado")

    if missing:
        messages.append(f"ERROR: Binarios faltantes: {', '.join(missing)}")
        messages.append("   Ejecutá el instalador y completá la extracción")
        return False, messages

    return True, messages


def main() -> int:
    """Main entry point. Returns 0 if OK, 1 if MiKTeX is not ready."""
    print("=" * 60)
    print("Verificando MiKTeX Portable...")
    print("=" * 60)

    success, messages = check_miktex_portable()

    for msg in messages:
        print(msg)

    print("=" * 60)

    if success:
        print("OK: MiKTeX Portable listo para usar")
        return 0
    else:
        print("ERROR: MiKTeX Portable NO esta listo")
        print()
        print("Pasos para solucionarlo:")
        print("   1. Descargá el instalador portable desde:")
        print(f"      {DOWNLOAD_URL}")
        print("   2. Renombrá el archivo descargado a: miktex-portable.exe")
        print(f"   3. Colocalo en: {MIKTEX_INSTALLER.absolute()}")
        print("   4. Ejecutalo (doble click o desde terminal)")
        print("   5. Se extraera en: app/external/latex/miktex-portable/")
        print("   6. Volvé a ejecutar este script para verificar")
        return 1


if __name__ == "__main__":
    sys.exit(main())