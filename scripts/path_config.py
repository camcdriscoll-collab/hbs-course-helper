"""
path_config.py — Runtime path resolution for Canvas scripts.

All paths are derived from this file's own location using Path(__file__),
so the entire Coursework folder can be renamed, moved, or copied and the
scripts will still resolve correctly.

Courses are auto-discovered from Canvas API on first run and cached for
24 hours in canvas_config.json — no manual course ID configuration needed.

Required .env keys:
    CANVAS_API_TOKEN  — Canvas personal access token
    CANVAS_BASE_URL   — Your Canvas domain, e.g. https://yourschool.instructure.com
                        (also accepts CANVAS_API_URL with /api/v1 appended)
    ANTHROPIC_API_KEY — For AI notes generation

Results are cached in claude/canvas_config.json and updated when stale.
If anything has moved or changed, a one-line notice is printed; silent otherwise.

Usage in other scripts:
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    import path_config
    paths = path_config.resolve()
    # paths["canvas_base"]  — Canvas API base URL (includes /api/v1)
    # paths["env_file"]     — Path to .env file
    # paths["courses"]["LTV"]["folder_path"] etc.
"""

import json
import os
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ── Derived from this file's location — always correct after any rename/move ──
SCRIPTS_DIR     = Path(__file__).resolve().parent       # claude/scripts/  (or repo/scripts/)
CLAUDE_DIR      = SCRIPTS_DIR.parent                    # claude/          (or repo/)
PROMPTS_DIR     = CLAUDE_DIR / "prompts"                # claude/prompts/


def _coursework_root() -> Path:
    """
    Where the course folders live.

    By default this is the parent of the scripts folder, which assumes the
    scripts have been copied to Coursework/claude/scripts/. Setting
    COURSEWORK_ROOT (env var, or a line in .env) points them at the folder
    directly, so a git clone can run in place with no second copy to keep
    in sync.

    Read before the full .env discovery below, which needs a root to search.
    """
    raw = os.getenv("COURSEWORK_ROOT", "")
    if not raw:
        for candidate in (CLAUDE_DIR / ".env", CLAUDE_DIR.parent / ".env"):
            try:
                if not candidate.exists():
                    continue
                for line in candidate.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("COURSEWORK_ROOT") and "=" in line:
                        raw = line.partition("=")[2].strip().strip('"').strip("'")
                        break
            except Exception:
                continue
            if raw:
                break
    return Path(raw).expanduser().resolve() if raw else CLAUDE_DIR.parent


COURSEWORK_ROOT = _coursework_root()
CONFIG_FILE     = CLAUDE_DIR / "canvas_config.json"
MASTER_PROMPT   = PROMPTS_DIR / "cheat_sheet_prompt.md"

# ── Populated by resolve() — do not edit directly ────────────────────────────
# These are dicts so existing callers holding a reference still see updates.
CANVAS_IDS:   dict[str, int] = {}   # abbrev → Canvas course ID
COURSE_NAMES: dict[str, str] = {}   # abbrev → full course name
CANVAS_BASE:  str = ""              # e.g. "https://hbs.instructure.com/api/v1"

# ── Course cache TTL ──────────────────────────────────────────────────────────
_COURSE_TTL_HOURS = 24

# ── Config file I/O ───────────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text()) if CONFIG_FILE.exists() else {}
    except Exception:
        return {}


def _save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, indent=2))

# ── .env parsing ──────────────────────────────────────────────────────────────

def _read_env_file(env_file: "Path | None") -> dict[str, str]:
    """Read key=value pairs from an env file."""
    env: dict[str, str] = {}
    if not env_file or not env_file.exists():
        return env
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def _get_canvas_base(env: dict[str, str]) -> str:
    """
    Return the canonical Canvas API base URL (always ends with /api/v1).
    Accepts CANVAS_BASE_URL (domain only) or CANVAS_API_URL (with /api/v1).
    """
    raw = env.get("CANVAS_BASE_URL", "") or env.get("CANVAS_API_URL", "")
    raw = raw.rstrip("/")
    if not raw:
        return ""
    # Normalise: strip /api/v1 if already present, then re-add
    if raw.endswith("/api/v1"):
        raw = raw[: -len("/api/v1")]
    return raw + "/api/v1"

# ── .env file discovery ───────────────────────────────────────────────────────

def _find_env_file(cached: "str | None" = None) -> "Path | None":
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
    candidates += sorted(Path.home().glob("repos/*/.env"))

    for p in candidates:
        try:
            if p.exists() and "CANVAS_API_TOKEN" in p.read_text():
                return p.resolve()
        except Exception:
            continue
    return None

# ── Canvas course discovery ───────────────────────────────────────────────────

def _abbrev_from_course(course: dict, taken: set) -> str:
    """
    Derive a short, folder-safe abbreviation from a Canvas course object.

    Priority:
      1. First all-uppercase token (2–8 chars) in course_code
         e.g. "CATS 26F" → "CATS",  "MBA 600 26F" → "MBA"
      2. First alphabetic token from course_code, uppercased
      3. Initials from course name (articles/prepositions skipped)
      4. Fallback: "C{id}"
    """
    code = course.get("course_code", "")
    name = course.get("name", "")

    # 1. First all-caps word (2–8 letters)
    base = ""
    for m in re.finditer(r"\b([A-Z]{2,8})\b", code):
        base = m.group(1)
        break

    if not base:
        # 2. Strip non-alpha, take first word
        letters = re.sub(r"[^A-Za-z\s]", "", code).strip()
        parts = letters.split()
        base = parts[0].upper() if parts else ""

    if len(base) < 2:
        # 3. Initials from course name
        skip = {"a", "an", "the", "and", "or", "of", "for", "in", "on", "to", "at"}
        words = [w for w in name.split() if w.lower() not in skip and w[:1].isalpha()]
        base = "".join(w[0].upper() for w in words[:4]) or f"C{course['id']}"

    # Deduplicate: CATS → CATS2 → CATS3 ...
    if base not in taken:
        return base
    for i in range(2, 20):
        candidate = f"{base}{i}"
        if candidate not in taken:
            return candidate
    return f"C{course['id']}"


def _clean_course_name(raw: str) -> str:
    """
    Extract the human-readable name from Canvas's internal course title format.

    Canvas often formats titles as:  "ABBREV - 00 Full Course Name 1234"
    or:                              "ABBREV EXTRA - 00 Full Course Name (TAG) 1234"
    This strips the section prefix and trailing numeric/tag codes.
    If the pattern doesn't match (e.g. already a plain name), returns raw unchanged.
    """
    raw = raw.strip()
    # Split on the first " - " to isolate the abbreviation prefix
    parts = raw.split(" - ", 1)
    if len(parts) != 2:
        return raw
    rest = parts[1].strip()  # e.g. "00 Capitalism and the State 1120"
    # Strip leading section number (1–3 digits followed by a space)
    m = re.match(r"^\d{1,3}\s+(.+)", rest)
    if not m:
        return raw
    name = m.group(1).strip()  # e.g. "Capitalism and the State 1120"
    # Strip trailing 3–5 digit course code
    name = re.sub(r"\s+\d{3,5}\s*$", "", name).strip()
    # Strip trailing " (TAG)" suffix Canvas sometimes appends
    name = re.sub(r"\s+\([^)]{1,15}\)\s*$", "", name).strip()
    return name if name else raw


def _fetch_enrolled_courses(token: str, base_url: str) -> list:
    """Return active student-enrolled Canvas courses via the API."""
    params = urlencode({
        "enrollment_type":  "student",
        "enrollment_state": "active",
        "per_page":         "100",
    })
    url = f"{base_url}/courses?{params}"
    try:
        req = Request(url, headers={"Authorization": f"Bearer {token}"})
        with urlopen(req, timeout=15) as resp:
            return json.loads(resp.read())
    except URLError as e:
        print(f"  [paths] WARNING: Canvas course discovery failed ({e})")
        return []
    except Exception as e:
        print(f"  [paths] WARNING: Unexpected error during course discovery ({e})")
        return []


def _should_refresh(cfg: dict) -> bool:
    """Return True if the course cache is missing, stale, or in the old format."""
    courses = cfg.get("courses", {})
    if not courses:
        return True
    # Old format: entries have no canvas_id (just folder_name)
    if any(not v.get("canvas_id") for v in courses.values()):
        return True
    last = cfg.get("courses_refreshed_at")
    if not last:
        return True
    try:
        age = datetime.now(timezone.utc) - datetime.fromisoformat(last)
        return age >= timedelta(hours=_COURSE_TTL_HOURS)
    except Exception:
        return True


def _discover_courses(token: str, base_url: str, cfg: dict) -> "tuple[dict, bool]":
    """
    Fetch enrolled courses from Canvas and merge them into the existing config.

    Merging, not replacing: Canvas has been observed returning a partial
    enrolment list (10 courses one hour, 6 the next, with no `rel="next"` page
    to follow). Rebuilding the map from a single response deletes every course
    missing from it, and a dropped course stops syncing silently — taking a
    term of session folders out of scope with it. Removing a course you have
    genuinely dropped is a one-line edit to canvas_config.json; losing one
    without noticing is not.

    Honours `abbrev_overrides` in canvas_config.json — {canvas_id: "ABBREV"} —
    so a course can use your folder name rather than the code Canvas reports.

    Returns (courses_dict, changed).
    """
    print("  [paths] Refreshing course list from Canvas...")
    raw = _fetch_enrolled_courses(token, base_url)
    if not raw:
        return cfg.get("courses", {}), False

    existing  = cfg.get("courses", {})
    overrides = {str(k): v for k, v in cfg.get("abbrev_overrides", {}).items()}

    merged: dict = {a: dict(e) for a, e in existing.items()}
    by_id: dict  = {str(e["canvas_id"]): a
                    for a, e in existing.items() if e.get("canvas_id")}
    taken: set   = set(merged)   # so a new course can't take a known abbrev
    seen: list   = []

    for c in raw:
        if not isinstance(c, dict) or "id" not in c:
            continue
        cid = str(c["id"])
        # An explicit override wins, then the abbrev this course already uses,
        # then a freshly derived one deduped against everything known.
        abbrev = overrides.get(cid) or by_id.get(cid) or _abbrev_from_course(c, taken)
        taken.add(abbrev)
        merged[abbrev] = {
            "canvas_id":   c["id"],
            "full_name":   _clean_course_name(c.get("name", abbrev)),
            "folder_name": merged.get(abbrev, {}).get("folder_name"),
        }
        seen.append(abbrev)

    # Apply overrides across the whole map rather than only this response, so a
    # course Canvas didn't return still ends up under the abbrev you chose.
    for abbrev in list(merged):
        cid  = str(merged[abbrev].get("canvas_id"))
        want = overrides.get(cid)
        if not want or want == abbrev:
            continue
        if want not in merged:
            merged[want] = merged.pop(abbrev)
        elif str(merged[want].get("canvas_id")) == cid:
            # This response already wrote the course under `want`; keep that
            # entry but don't lose a folder we had already resolved.
            if not merged[want].get("folder_name"):
                merged[want]["folder_name"] = merged[abbrev].get("folder_name")
            del merged[abbrev]
        else:
            print(f"  [paths] WARNING: abbrev_overrides wants {abbrev} → {want}, "
                  f"but {want} is already course {merged[want].get('canvas_id')} — skipping")
            continue
        if abbrev in seen:
            seen[seen.index(abbrev)] = want
        print(f"  [paths] {abbrev} → {want} (abbrev_overrides)")

    print(f"  [paths] Found {len(seen)} enrolled course(s): {', '.join(sorted(seen))}")
    held = sorted(set(merged) - set(seen))
    if held:
        print(f"  [paths] Canvas did not return {len(held)} known course(s) this time; "
              f"keeping them: {', '.join(held)}")
    return merged, True

# ── Course folder resolution ──────────────────────────────────────────────────

def _find_course_folder(abbrev: str, cached: "str | None" = None) -> "str | None":
    """
    Find the actual subfolder name for a course abbreviation.
    Priority: cached name → exact match → case-insensitive → fuzzy word match.
    Returns the folder name (not full path), or None if not found.
    """
    for name in filter(None, [cached, abbrev]):
        if (COURSEWORK_ROOT / name).is_dir():
            return name

    exclude = {"claude"}
    abbrev_lower = abbrev.lower().replace(" ", "")
    abbrev_words = abbrev.lower().split()

    for d in COURSEWORK_ROOT.iterdir():
        if not d.is_dir() or d.name in exclude or d.name.startswith("."):
            continue
        name = d.name
        if name.lower().replace(" ", "") == abbrev_lower:
            return name
        if all(w in name.lower() for w in abbrev_words):
            return name

    return None

# ── Main resolution function ──────────────────────────────────────────────────

def resolve() -> dict:
    """
    Resolve all paths, auto-discover Canvas courses if the cache is stale,
    and update canvas_config.json when anything changes.

    Populates the module-level CANVAS_IDS, COURSE_NAMES, and CANVAS_BASE
    dicts/strings so existing callers holding references see the updates.

    Returns:
        {
          "canvas_base":     str,          # e.g. "https://hbs.instructure.com/api/v1"
          "env_file":        Path | None,
          "coursework_root": Path,
          "prompts_dir":     Path,
          "master_prompt":   Path,
          "courses": {
            "LTV": {
              "canvas_id":         17019,
              "full_name":         "Launching Tech Ventures in the Age of AI",
              "folder_name":       "LTV",
              "folder_path":       Path(...),
              "refinement_prompt": Path(...) | None,
            },
            ...
          }
        }
    """
    global CANVAS_BASE
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

    # ── Canvas base URL ────────────────────────────────────────────────────────
    env_vars = _read_env_file(env_file)
    canvas_base = _get_canvas_base(env_vars)
    token = env_vars.get("CANVAS_API_TOKEN", "")

    if canvas_base != cfg.get("canvas_base_url"):
        cfg["canvas_base_url"] = canvas_base
        changed = True

    CANVAS_BASE = canvas_base  # expose at module level

    # ── Course discovery ───────────────────────────────────────────────────────
    if _should_refresh(cfg):
        if canvas_base and token:
            discovered, disc_changed = _discover_courses(token, canvas_base, cfg)
            if disc_changed:
                cfg["courses"] = discovered
                cfg["courses_refreshed_at"] = datetime.now(timezone.utc).isoformat()
                changed = True
        else:
            if not token:
                print("  [paths] WARNING: CANVAS_API_TOKEN not set — cannot discover courses")
            if not canvas_base:
                print("  [paths] WARNING: CANVAS_BASE_URL not set — cannot discover courses")

    if "courses" not in cfg:
        cfg["courses"] = {}
        changed = True

    # ── Folder resolution + module-level dicts ─────────────────────────────────
    CANVAS_IDS.clear()
    COURSE_NAMES.clear()
    courses: dict = {}

    ignored = {str(i) for i in cfg.get("ignored_courses", [])}

    for abbrev, entry in cfg.get("courses", {}).items():
        canvas_id = entry.get("canvas_id")
        if not canvas_id:
            continue
        if str(canvas_id) in ignored:
            continue   # listed in ignored_courses — e.g. an admin or kickoff shell

        cached_name = entry.get("folder_name")
        folder_name = _find_course_folder(abbrev, cached_name)
        if folder_name != cached_name:
            print(f"  [paths] {abbrev}: folder {cached_name!r} → {folder_name!r}")
            cfg["courses"][abbrev]["folder_name"] = folder_name
            changed = True

        # A course with no folder used to be dropped from every loop in
        # canvas_refresh, which meant it never got a folder created and so could
        # never resolve on a later run either — a silent, permanent dead end.
        # Create the folder so the course joins the sync from here on.
        if folder_name is None:
            new_dir = COURSEWORK_ROOT / abbrev
            try:
                new_dir.mkdir(parents=True, exist_ok=True)
                folder_name = abbrev
                cfg["courses"][abbrev]["folder_name"] = folder_name
                changed = True
                print(f"  [paths] {abbrev}: created course folder {new_dir}")
                print(f"  [paths]   (add {canvas_id} to \"ignored_courses\" in "
                      f"canvas_config.json to skip this course instead)")
            except OSError as e:
                print(f"  [paths] WARNING: could not create folder for {abbrev}: {e}")

        full_name = entry.get("full_name", abbrev)
        code = abbrev.replace(" ", "_")
        refinement = PROMPTS_DIR / f"cheat_sheet_prompt_{code}_refinement.md"
        folder_path = (COURSEWORK_ROOT / folder_name) if folder_name else None

        CANVAS_IDS[abbrev]   = canvas_id
        COURSE_NAMES[abbrev] = full_name
        courses[abbrev] = {
            "canvas_id":         canvas_id,
            "full_name":         full_name,
            "folder_name":       folder_name,
            "folder_path":       folder_path,
            "refinement_prompt": refinement if refinement.exists() else None,
        }

    if not courses and not token:
        print("  [paths] WARNING: No courses found. Add CANVAS_BASE_URL and")
        print("          CANVAS_API_TOKEN to your .env file and re-run.")

    if changed:
        _save_config(cfg)

    return {
        "canvas_base":     canvas_base,
        "env_file":        env_file,
        "coursework_root": COURSEWORK_ROOT,
        "prompts_dir":     PROMPTS_DIR,
        "master_prompt":   MASTER_PROMPT,
        "courses":         courses,
    }
