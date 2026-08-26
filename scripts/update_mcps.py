#!/usr/bin/env python3
"""
update_mcps.py — Keep Python dependencies up to date.

Checks PyPI for newer versions of key packages and upgrades them in the venv.

Run standalone:
  python3 scripts/update_mcps.py

Also called automatically at the start of canvas_refresh.py --weekly.
"""

import json
import subprocess
import sys
from importlib.metadata import version as pkg_version, PackageNotFoundError
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError

# ── Config ────────────────────────────────────────────────────────────────────

PACKAGES = [
    "anthropic",
    "notebooklm-py",
    "python-docx",
    "pypdf",
    "python-pptx",
]

# Venv lives in the hbs-course-helper repo root
_HERE = Path(__file__).resolve().parent.parent
_VENV_PYTHON = _HERE / ".venv" / "bin" / "python3"
# Fallback: canvas-mcp-hbs2026 venv (legacy location)
_FALLBACK_PYTHON = Path.home() / "repos" / "canvas-mcp-hbs2026" / ".venv" / "bin" / "python3"


def _venv_python() -> Path:
    if _VENV_PYTHON.exists():
        return _VENV_PYTHON
    if _FALLBACK_PYTHON.exists():
        return _FALLBACK_PYTHON
    return Path(sys.executable)


# ── PyPI version checks ───────────────────────────────────────────────────────

def _pypi_latest(package: str) -> str | None:
    try:
        with urlopen(f"https://pypi.org/pypi/{package}/json", timeout=10) as r:
            return json.load(r)["info"]["version"]
    except (URLError, KeyError):
        return None


def _installed_version(package: str) -> str | None:
    try:
        return pkg_version(package)
    except PackageNotFoundError:
        return None


def _upgrade_package(package: str) -> bool:
    py = str(_venv_python())
    result = subprocess.run(
        [py, "-m", "pip", "install", "--upgrade", "--quiet", package],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def check_packages(auto_upgrade: bool = True) -> list[str]:
    updated = []
    print("  Checking package versions...")
    for pkg in PACKAGES:
        installed = _installed_version(pkg)
        latest    = _pypi_latest(pkg)
        if not latest:
            continue
        if installed is None:
            print(f"    {pkg}: not installed (latest {latest})")
            updated.append(pkg)
        elif installed == latest:
            print(f"    {pkg}: {installed} ✓")
        else:
            print(f"    {pkg}: {installed} → {latest}", end="")
            if auto_upgrade:
                ok = _upgrade_package(pkg)
                print(" ✅ upgraded" if ok else " ✗ upgrade failed")
                if ok:
                    updated.append(pkg)
            else:
                print(" (run with auto_upgrade=True to update)")
                updated.append(pkg)
    return updated


# ── Entry point ───────────────────────────────────────────────────────────────

def run(auto_upgrade: bool = True) -> None:
    print(f"\n{'─'*55}")
    print("  DEPENDENCY UPDATE CHECK")
    print(f"{'─'*55}")
    upgraded = check_packages(auto_upgrade=auto_upgrade)
    print(f"{'─'*55}")
    if not upgraded:
        print("  All packages up to date.")
    else:
        print(f"  Upgraded: {', '.join(upgraded)}")
    print(f"{'─'*55}\n")


if __name__ == "__main__":
    run()
