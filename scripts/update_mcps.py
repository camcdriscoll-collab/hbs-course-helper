#!/usr/bin/env python3
"""
update_mcps.py — Keep dependencies and MCP forks up to date.

Checks:
  1. PyPI: newer versions of key packages → auto-upgrades in the venv
  2. GitHub: new commits in the upstream notebooklm-mcp and canvas-mcp repos
             → reports them (does NOT auto-merge; your custom changes could conflict)

Run standalone:
  python3 scripts/update_mcps.py

Also called automatically at the start of canvas_refresh.py --weekly.
"""

import json
import subprocess
import sys
from importlib.metadata import version as pkg_version, PackageNotFoundError
from pathlib import Path
from urllib.request import urlopen
from urllib.error import URLError

# ── Config ────────────────────────────────────────────────────────────────────

# Packages to keep up to date in the venv
PACKAGES = [
    "anthropic",
    "notebooklm-py",
    "python-docx",
    "pypdf",
    "python-pptx",
]

# Upstream repos to monitor for new commits
# Format: (fork_owner/repo, upstream_owner/repo, description)
UPSTREAM_REPOS = [
    (
        "camcdriscoll-collab/notebooklm-mcp",
        "alfredang/notebooklm-mcp",
        "NotebookLM MCP server",
    ),
    (
        "camcdriscoll-collab/canvas-mcp-hbs2026",
        "vishalsachdev/canvas-mcp",
        "Canvas MCP server",
    ),
]

# The venv lives next to this repo (or alongside canvas-mcp-hbs2026)
_HERE = Path(__file__).resolve().parent.parent
_VENV_PYTHON = _HERE / ".venv" / "bin" / "python3"
# Fallback: canvas-mcp-hbs2026 venv (original location)
_FALLBACK_PYTHON = Path.home() / "repos" / "canvas-mcp-hbs2026" / ".venv" / "bin" / "python3"

def _venv_python() -> Path:
    if _VENV_PYTHON.exists():
        return _VENV_PYTHON
    if _FALLBACK_PYTHON.exists():
        return _FALLBACK_PYTHON
    return Path(sys.executable)

# ── PyPI version checks ───────────────────────────────────────────────────────

def _pypi_latest(package: str) -> str | None:
    """Return latest version on PyPI, or None on error."""
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
    """Upgrade a package in the venv. Returns True on success."""
    py = str(_venv_python())
    result = subprocess.run(
        [py, "-m", "pip", "install", "--upgrade", "--quiet", package],
        capture_output=True, text=True,
    )
    return result.returncode == 0


def check_packages(auto_upgrade: bool = True) -> list[str]:
    """
    Check PyPI for updates. Upgrades automatically if auto_upgrade=True.
    Returns list of packages that were (or need to be) upgraded.
    """
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

# ── GitHub upstream checks ────────────────────────────────────────────────────

def _github_ahead(fork: str, upstream: str) -> int | None:
    """
    Return how many commits upstream is ahead of the fork's main branch.
    Uses GitHub's compare API (unauthenticated, 60 req/hr limit — fine for weekly use).
    """
    fork_owner, fork_repo = fork.split("/")
    url = (
        f"https://api.github.com/repos/{fork}/compare"
        f"/{fork_owner}:main...{upstream.split('/')[0]}:main"
    )
    try:
        req_headers = {"Accept": "application/vnd.github+json",
                       "User-Agent": "hbs-coursework-automation"}
        from urllib.request import Request
        with urlopen(Request(url, headers=req_headers), timeout=10) as r:
            data = json.load(r)
            return data.get("ahead_by", 0)
    except Exception:
        return None


def check_upstream_repos() -> list[str]:
    """
    Report upstream commits not yet in the forks.
    Does NOT auto-merge — custom changes to server.py would conflict.
    Returns list of repos that have available updates.
    """
    needs_update = []
    print("  Checking upstream MCP repos...")
    for fork, upstream, label in UPSTREAM_REPOS:
        ahead = _github_ahead(fork, upstream)
        if ahead is None:
            print(f"    {label}: could not reach GitHub")
        elif ahead == 0:
            print(f"    {label}: up to date ✓")
        else:
            print(f"    {label}: upstream is {ahead} commit(s) ahead ⚠")
            print(f"      → Review: https://github.com/{fork}/compare/main...{upstream.split('/')[0]}:main")
            print(f"      → To merge: cd ~/repos/{fork.split('/')[1]} && "
                  f"git fetch https://github.com/{upstream}.git main && git merge FETCH_HEAD")
            needs_update.append(fork)
    return needs_update

# ── Entry point ───────────────────────────────────────────────────────────────

def run(auto_upgrade: bool = True) -> None:
    print(f"\n{'─'*55}")
    print("  MCP UPDATE CHECK")
    print(f"{'─'*55}")
    upgraded  = check_packages(auto_upgrade=auto_upgrade)
    outdated  = check_upstream_repos()
    print(f"{'─'*55}")
    if not upgraded and not outdated:
        print("  Everything up to date.")
    else:
        if upgraded:
            print(f"  Packages upgraded: {', '.join(upgraded)}")
        if outdated:
            print(f"  Upstream updates available — review and merge manually.")
    print(f"{'─'*55}\n")


if __name__ == "__main__":
    run()
