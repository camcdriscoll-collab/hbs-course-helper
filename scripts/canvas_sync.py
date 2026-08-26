#!/usr/bin/env python3
"""
HBS Canvas 26F Sync
───────────────────
Mirrors Canvas course files to ~/Desktop/26F Coursework/

Folder structure created:
  26F Coursework/
    CATS/
      General/          ← syllabus, misc course docs
      260908 CATS/      ← class session folders (YYMMDD ABBREV)
      261124 CATS/
    CFO/
      General/
      260902 CFO/       ← per-class case files
      ...

Re-run any time throughout the term — already-downloaded files are skipped.

Run:
  python3 ~/Desktop/canvas_sync.py
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen, build_opener, HTTPRedirectHandler

# ── Config ────────────────────────────────────────────────────────────────────

ENV_FILE = Path.home() / "repos" / "hbs-course-helper" / ".env"
CANVAS_BASE = "https://hbs.instructure.com/api/v1"
DEST_ROOT = Path.home() / "Desktop" / "Coursework"

# Boston EDT (UTC-4) — active through fall term Sep–Nov
BOSTON = timezone(timedelta(hours=-4))

# Canvas course ID → short abbreviation (used in folder names)
COURSES = {
    16927: "CATS",
    16968: "CFO",
    17014: "ENT FIN",
    16952: "INNOV SCAL",
    17019: "LTV",
    17025: "MCAS",
    17040: "MHC",
    16966: "TAF",
}

# ── Token ─────────────────────────────────────────────────────────────────────

def load_token() -> str:
    token = os.getenv("CANVAS_API_TOKEN", "")
    if token:
        return token
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("CANVAS_API_TOKEN=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"No CANVAS_API_TOKEN found. Set env var or check {ENV_FILE}")


TOKEN = load_token()

# ── HTTP helpers ──────────────────────────────────────────────────────────────

class _CrossDomainRedirect(HTTPRedirectHandler):
    """Follow redirects, stripping Authorization on cross-domain hops (e.g. → S3)."""
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req:
            from urllib.parse import urlparse
            if urlparse(newurl).netloc != urlparse(req.full_url).netloc:
                new_req.remove_header("Authorization")
        return new_req


_opener = build_opener(_CrossDomainRedirect())


def _api_request(url: str, auth: bool = True) -> bytes:
    headers = {"Authorization": f"Bearer {TOKEN}"} if auth else {}
    req = Request(url, headers=headers)
    return _opener.open(req, timeout=60).read()


def api_get(path: str, params: dict | None = None) -> list | dict:
    """Canvas GET with automatic pagination. Returns list or dict."""
    url = f"{CANVAS_BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urlencode(params)
    results = []
    while url:
        req = Request(url, headers={"Authorization": f"Bearer {TOKEN}"})
        try:
            resp = _opener.open(req, timeout=30)
        except HTTPError as e:
            print(f"    HTTP {e.code}: {url}")
            return []
        data = json.loads(resp.read())
        if isinstance(data, list):
            results.extend(data)
        else:
            return data
        # Pagination via Link header
        link_header = resp.headers.get("Link", "")
        url = None
        for part in link_header.split(","):
            if 'rel="next"' in part:
                m = re.search(r"<(.+?)>", part)
                if m:
                    url = m.group(1)
    return results


def download(url: str, dest: Path) -> bool:
    """Download url → dest. Returns True if newly downloaded, False if skipped."""
    if dest.exists():
        return False
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = _api_request(url)
        dest.write_bytes(data)
        return True
    except (HTTPError, URLError, OSError) as e:
        print(f"      ✗ {e}")
        return False

# ── Utilities ─────────────────────────────────────────────────────────────────

def safe_name(s: str) -> str:
    """Sanitize string for use as a macOS filename."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", s)
    s = re.sub(r"-{2,}", "-", s).strip(". -")
    return s[:200]


def to_date(iso: str) -> str:
    """ISO 8601 UTC → YYMMDD in Boston time."""
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BOSTON)
    return dt.strftime("%y%m%d")


def extract_canvas_file_ids(html: str, course_id: int) -> list[int]:
    """Find Canvas file IDs referenced in assignment description HTML."""
    ids: list[int] = []
    patterns = [
        rf"instructure\.com/courses/{course_id}/files/(\d+)",
        r"instructure\.com/files/(\d+)",
        rf"/courses/{course_id}/files/(\d+)",
        r"/files/(\d+)",
    ]
    for p in patterns:
        ids.extend(int(x) for x in re.findall(p, html))
    return list(dict.fromkeys(ids))  # deduped, order preserved


def class_number(text: str) -> int | None:
    """Extract 'Class N' number from a string, e.g. 'CFO | Class 3: ...' → 3."""
    m = re.search(r"\bclass\s+(\d+)\b", text, re.IGNORECASE)
    return int(m.group(1)) if m else None

# ── Per-course sync ───────────────────────────────────────────────────────────

def sync_course(course_id: int, abbrev: str):
    print(f"\n{'─'*55}")
    print(f"  {abbrev}  (course {course_id})")
    print(f"{'─'*55}")

    course_dir = DEST_ROOT / abbrev
    general_dir = course_dir / "General"
    general_dir.mkdir(parents=True, exist_ok=True)

    # ── Folders ──────────────────────────────────────────────────────────────
    folders = api_get(f"courses/{course_id}/folders", {"per_page": 100})

    # Per-class Canvas folders ("Class N:" pattern) → keyed by class number
    class_folders: dict[int, dict] = {}
    class_folder_ids: set[int] = set()
    for f in folders:
        n = class_number(f.get("name", ""))
        if n is not None:
            class_folders[n] = f
            class_folder_ids.add(f["id"])

    # ── Files → General ───────────────────────────────────────────────────────
    all_files = api_get(f"courses/{course_id}/files", {"per_page": 100})
    file_by_id: dict[int, dict] = {f["id"]: f for f in all_files}

    # Build index of filenames already in session folders (to skip duplicates)
    import re as _re
    _session_names: set[str] = set()
    for d in course_dir.iterdir():
        if d.is_dir() and _re.match(r'^\d{6}\s', d.name):
            _session_names.update(f.name for f in d.iterdir() if f.is_file())

    slides_dir = general_dir / "Slides"
    (general_dir / "Supplemental").mkdir(exist_ok=True)

    # Full placed-file index: session folders + Slides/ + Supplemental/
    _placed: set[str] = set(_session_names)
    for _sub in ("Slides", "Supplemental"):
        _subdir = general_dir / _sub
        if _subdir.exists():
            _placed.update(f2.name for f2 in _subdir.iterdir() if f2.is_file())

    # Files NOT in a per-class folder: PPTX → General/Slides/, others → General/
    general_files = [f for f in all_files if f.get("folder_id") not in class_folder_ids]
    if general_files:
        print(f"  General ({len(general_files)} file{'s' if len(general_files)!=1 else ''}):")
    for f in general_files:
        fname = safe_name(f["display_name"])
        if fname in _placed:
            print(f"    – skipped (already on disk): {f['display_name']}")
            continue
        if Path(fname).suffix.lower() in {'.pptx', '.ppt'}:
            slides_dir.mkdir(exist_ok=True)
            dest = slides_dir / fname
        else:
            dest = general_dir / fname
        ok = download(f["url"], dest)
        print(f"    {'↓' if ok else '✓'} {f['display_name']}")

    # ── Assignments → session folders ─────────────────────────────────────────
    assignments = api_get(f"courses/{course_id}/assignments", {"per_page": 100})

    # Only assignments that represent class sessions (have a due date)
    sessions = [a for a in assignments if a.get("due_at")]
    if not sessions:
        print("  No class session assignments found.")
        return

    sessions.sort(key=lambda a: a["due_at"])
    print(f"  Sessions: {len(sessions)}")

    for a in sessions:
        date_str = to_date(a["due_at"])
        session_name = f"{date_str} {abbrev}"
        session_dir = course_dir / session_name
        cn = class_number(a.get("name", ""))

        found_files = False

        # A) Per-class Canvas folder (e.g. CFO "Class 3: ...")
        if cn is not None and cn in class_folders:
            folder = class_folders[cn]
            class_files = api_get(f"folders/{folder['id']}/files", {"per_page": 100})
            if class_files:
                found_files = True
                session_dir.mkdir(parents=True, exist_ok=True)
                print(f"  [{date_str}] Class {cn} — {folder['name']}")
                for f in class_files:
                    fname = safe_name(f["display_name"])
                    # PPTX always goes to General/Slides/, not session folder
                    if Path(fname).suffix.lower() in {'.pptx', '.ppt'}:
                        slides_dir.mkdir(parents=True, exist_ok=True)
                        dest = slides_dir / fname
                    else:
                        dest = session_dir / fname
                    ok = download(f["url"], dest)
                    print(f"    {'↓' if ok else '✓'} {f['display_name']}")

        # B) Canvas files linked inside the assignment description
        desc = a.get("description") or ""
        linked_ids = extract_canvas_file_ids(desc, course_id)
        linked_files = [file_by_id[fid] for fid in linked_ids if fid in file_by_id]
        if linked_files:
            if not found_files:
                session_dir.mkdir(parents=True, exist_ok=True)
                print(f"  [{date_str}] {a['name'][:60]}")
            found_files = True
            for f in linked_files:
                fname = safe_name(f["display_name"])
                if Path(fname).suffix.lower() in {'.pptx', '.ppt'}:
                    slides_dir.mkdir(parents=True, exist_ok=True)
                    dest = slides_dir / fname
                else:
                    dest = session_dir / fname
                ok = download(f["url"], dest)
                print(f"    {'↓' if ok else '✓'} [linked] {f['display_name']}")

        if not found_files:
            # Session exists in Canvas but has no downloadable files yet
            pass  # folder will be created on next sync when files appear

# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    print(f"Canvas 26F Sync")
    print(f"Destination: {DEST_ROOT}")
    DEST_ROOT.mkdir(parents=True, exist_ok=True)

    for course_id, abbrev in COURSES.items():
        try:
            sync_course(course_id, abbrev)
        except Exception as e:
            print(f"\n  ERROR in {abbrev}: {e}")

    print(f"\n{'─'*55}")
    print("  ✅  Sync complete.")
    print(f"{'─'*55}\n")


if __name__ == "__main__":
    main()
