"""
path_config.py — Runtime path resolution for Canvas scripts.

All paths are derived from this file's own location using Path(__file__),
so the entire 26F Coursework folder can be renamed, moved, or copied and
the scripts will still resolve correctly.

The only things that need scanning are:
  - The .env file (lives outside the coursework folder in the repo)
  - Course folder names (in case you rename e.g. "LTV" to something else)

Results are cached in claude/canvas_config.json and re-verified on each run.
If anything has moved, the config is updated and a one-line notice is printed.

Usage in other scripts:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    import path_config
    paths = path_config.resolve()
    # paths["env_file"], paths["courses"]["LTV"]["folder_path"], etc.
"""

import json
from pathlib import Path

# ── Derived from this file's location — always correct after any rename/move ──
SCRIPTS_DIR     = Path(__file__).resolve().parent       # claude/scripts/
CLAUDE_DIR      = SCRIPTS_DIR.parent                    # claude/
PROMPTS_DIR     = CLAUDE_DIR / "prompts"                # claude/prompts/
COURSEWORK_ROOT = CLAUDE_DIR.parent                     # 26F Coursework/
CONFIG_FILE     = CLAUDE_DIR / "canvas_config.json"
MASTER_PROMPT   = PROMPTS_DIR / "cheat_sheet_prompt.md"

# ── Canvas course IDs (permanent — assigned by Canvas, never change) ──────────
CANVAS_IDS: dict[str, int] = {
    "CATS": 16927,
    "CFO":  16968,
    "LME":  17009,
    "LTV":  17019,
}

# ── Config file I/O ───────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    except Exception:
        return {}


def _save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

# ── .env discovery ────────────────────────────────────────────────────────────

def _find_env_file(cached: str | None = None) -> Path | None:
    """Scan for a .env file containing CANVAS_API_TOKEN."""
    candidates: list[Path] = []
    if cached:
        candidates.append(Path(cached))

    candidates += [
        COURSEWORK_ROOT / ".env",
        CLAUDE_DIR / ".env",
        Path.home() / "repos" / "hbs-course-helper" / ".env",
        Path.home() / ".env",
    ]
    # Also search any ~/repos/*/.env
    candidates += sorted(Path.home().glob("repos/*/.env"))

    for p in candidates:
        try:
            if p.exists() and "CANVAS_API_TOKEN" in p.read_text():
                return p.resolve()
        except Exception:
            continue
    return None

# ── Course folder discovery ───────────────────────────────────────────────────

def _find_course_folder(abbrev: str, cached: str | None = None) -> str | None:
    """
    Find the actual subfolder name for a course abbreviation.
    Priority: cached name → exact match → case-insensitive → fuzzy word match.
    Returns the folder name (not full path), or None if not found.
    """
    # Try cached or default name first
    for name in filter(None, [cached, abbrev]):
        if (COURSEWORK_ROOT / name).is_dir():
            return name

    # Fuzzy scan of all non-hidden, non-claude subdirs
    exclude = {"claude"}
    abbrev_lower = abbrev.lower().replace(" ", "")
    abbrev_words = abbrev.lower().split()

    for d in COURSEWORK_ROOT.iterdir():
        if not d.is_dir() or d.name in exclude or d.name.startswith("."):
            continue
        name = d.name
        # Exact match ignoring spaces/case
        if name.lower().replace(" ", "") == abbrev_lower:
            return name
        # All words in abbrev appear in folder name
        if all(w in name.lower() for w in abbrev_words):
            return name

    return None

# ── Main resolution function ──────────────────────────────────────────────────

def resolve() -> dict:
    """
    Verify all paths and update canvas_config.json if anything changed.
    Called once at the top of each script. Prints a notice only when a path
    has changed since the last run — silent otherwise.

    Returns:
        {
          "env_file":        Path | None,
          "coursework_root": Path,
          "prompts_dir":     Path,
          "master_prompt":   Path,
          "courses": {
            "LTV": {
              "canvas_id":        17019,
              "folder_name":      "LTV",           # actual name on disk
              "folder_path":      Path(...),        # full path
              "refinement_prompt": Path(...) | None,
            },
            ...
          }
        }
    """
    cfg = _load_config()
    changed = False

    # ── .env file ─────────────────────────────────────────────────────────────
    env_file = _find_env_file(cfg.get("env_file"))
    new_env_str = str(env_file) if env_file else None
    if new_env_str != cfg.get("env_file"):
        if env_file:
            print(f"  [paths] env_file → {env_file}")
        else:
            print("  [paths] WARNING: .env file not found — API calls will fail")
        cfg["env_file"] = new_env_str
        changed = True

    # ── Course folders ─────────────────────────────────────────────────────────
    if "courses" not in cfg:
        cfg["courses"] = {}
        changed = True

    courses: dict[str, dict] = {}
    for abbrev, canvas_id in CANVAS_IDS.items():
        cached_name = cfg["courses"].get(abbrev, {}).get("folder_name")
        folder_name = _find_course_folder(abbrev, cached_name)

        if folder_name != cached_name:
            print(f"  [paths] {abbrev}: folder {cached_name!r} → {folder_name!r}")
            cfg["courses"][abbrev] = {"folder_name": folder_name}
            changed = True
        elif abbrev not in cfg["courses"]:
            cfg["courses"][abbrev] = {"folder_name": folder_name}
            changed = True

        code = abbrev.replace(" ", "_")
        refinement = PROMPTS_DIR / f"cheat_sheet_prompt_{code}_refinement.md"
        folder_path = (COURSEWORK_ROOT / folder_name) if folder_name else None

        courses[abbrev] = {
            "canvas_id":         canvas_id,
            "folder_name":       folder_name,
            "folder_path":       folder_path,
            "refinement_prompt": refinement if refinement.exists() else None,
        }

    if changed:
        _save_config(cfg)

    return {
        "env_file":        env_file,
        "coursework_root": COURSEWORK_ROOT,
        "prompts_dir":     PROMPTS_DIR,
        "master_prompt":   MASTER_PROMPT,
        "courses":         courses,
    }
