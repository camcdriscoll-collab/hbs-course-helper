#!/usr/bin/env python3
"""
HBS Cheat Sheet Generator
Generates a case prep Notes file for a class session.

Output: YYMMDD CLASSCODE Notes.md  (saved in the session folder)

Usage:
  python3 ~/Desktop/cheat_sheet.py 260902 LTV
  python3 ~/Desktop/cheat_sheet.py 260908 CATS

How it works:
  1. Finds the session folder ~/Desktop/26F Coursework/LTV/260902 LTV/
  2. Reads any PDF/PPTX/DOCX files in that folder as reading materials
  3. Pulls the Canvas assignment description (discussion questions) for that session
  4. Combines with the master prompt + class-specific notes
  5. Calls Claude to generate the cheat sheet
  6. Saves result as 260902 LTV Notes.md in the session folder

Adding readings: drop PDFs into the session folder before running.
Canvas PDFs are synced automatically; HBS case PDFs must be added manually.
"""

import base64
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

# ── Path resolution (tolerates folder renames/moves) ─────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
import path_config
_paths = path_config.resolve()

DEST_ROOT   = _paths["coursework_root"]
PROMPT_FILE = _paths["master_prompt"]
ENV_FILE    = _paths["env_file"] or Path("/dev/null")
_COURSES    = _paths["courses"]   # abbrev → {canvas_id, folder_path, refinement_prompt, ...}

CANVAS_BASE = "https://hbs.instructure.com/api/v1"
BOSTON = timezone(timedelta(hours=-4))
MODEL = "claude-sonnet-4-6"

# Canvas course ID by abbreviation (derived from path_config)
COURSE_IDS = {a: d["canvas_id"] for a, d in _COURSES.items()}

# Supported reading file extensions
READING_EXTS = {".pdf", ".docx", ".pptx", ".doc", ".ppt", ".txt", ".md"}

# ── Config loading ────────────────────────────────────────────────────────────

def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    for src in [ENV_FILE]:
        if src.exists():
            for line in src.read_text().splitlines():
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip().strip('"').strip("'")
    return env

ENV = load_env()

def get_config(key: str) -> str:
    return os.getenv(key, ENV.get(key, ""))


def require_config(key: str) -> str:
    val = get_config(key)
    if not val:
        sys.exit(
            f"\nMissing {key}.\n"
            f"Add it to {ENV_FILE} as:\n  {key}=your_key_here\n"
            "or set it as an environment variable."
        )
    return val

# ── Canvas API ────────────────────────────────────────────────────────────────

class _CrossDomainRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req:
            from urllib.parse import urlparse
            if urlparse(newurl).netloc != urlparse(req.full_url).netloc:
                new_req.remove_header("Authorization")
        return new_req

_opener = build_opener(_CrossDomainRedirect())


def canvas_get(path: str, params: dict | None = None) -> list | dict:
    canvas_token = require_config("CANVAS_API_TOKEN")
    url = f"{CANVAS_BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urlencode(params)
    results = []
    while url:
        req = Request(url, headers={"Authorization": f"Bearer {canvas_token}"})
        try:
            resp = _opener.open(req, timeout=30)
        except HTTPError as e:
            print(f"  Canvas HTTP {e.code}: {url}")
            return []
        data = json.loads(resp.read())
        if isinstance(data, list):
            results.extend(data)
        else:
            return data
        link = resp.headers.get("Link", "")
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                m = re.search(r"<(.+?)>", part)
                if m:
                    url = m.group(1)
    return results


def fetch_assignment(course_id: int, date_str: str) -> dict | None:
    """Find the assignment whose due date matches YYMMDD in Boston time."""
    assignments = canvas_get(f"courses/{course_id}/assignments", {"per_page": 100})
    for a in assignments:
        if not a.get("due_at"):
            continue
        dt = datetime.fromisoformat(a["due_at"].replace("Z", "+00:00")).astimezone(BOSTON)
        if dt.strftime("%y%m%d") == date_str:
            return a
    return None


def strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

# ── File reading ──────────────────────────────────────────────────────────────

def file_to_content_block(path: Path) -> dict:
    """Return an Anthropic content block for a reading file (PDF native or text)."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        data = base64.standard_b64encode(path.read_bytes()).decode()
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": data,
            },
            "title": path.name,
        }
    else:
        # For non-PDF files, send as plain text (best effort)
        try:
            text = path.read_text(errors="replace")
        except Exception:
            text = f"[Could not read {path.name}]"
        return {
            "type": "text",
            "text": f"=== {path.name} ===\n{text}",
        }

# ── Prompt assembly ───────────────────────────────────────────────────────────

def build_prompt(abbrev: str, assignment: dict | None, class_notes_file: Path) -> str:
    master = PROMPT_FILE.read_text() if PROMPT_FILE.exists() else ""

    # Append class-specific notes
    class_notes = ""
    if class_notes_file.exists():
        raw = class_notes_file.read_text().strip()
        # Strip markdown comment block — only include actual notes
        raw = re.sub(r"<!--.*?-->", "", raw, flags=re.DOTALL).strip()
        if raw and raw != "# CLASS-SPECIFIC NOTES":
            class_notes = f"\n\n{raw}"

    # Inject class notes into the [CLASS-SPECIFIC NOTES] placeholder
    if class_notes:
        master = re.sub(
            r"\[CLASS-SPECIFIC NOTES.*?\].*",
            f"[CLASS-SPECIFIC NOTES]\n{class_notes}",
            master,
            flags=re.DOTALL,
        )

    # Canvas assignment block
    canvas_block = ""
    if assignment:
        name = assignment.get("name", "")
        desc = strip_html(assignment.get("description") or "")
        canvas_block = (
            f"\n\n=== CANVAS ASSIGNMENT POSTING ===\n"
            f"Title: {name}\n\n"
            f"{desc}"
        )

    return master + canvas_block

# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    date_str = sys.argv[1]   # e.g. 260902
    abbrev = " ".join(sys.argv[2:]).upper()   # e.g. LTV  or  ENT FIN

    if abbrev not in COURSE_IDS:
        sys.exit(f"Unknown course '{abbrev}'. Known: {', '.join(COURSE_IDS)}")

    course_id = COURSE_IDS[abbrev]
    course_folder = (_COURSES.get(abbrev, {}).get("folder_path") or DEST_ROOT / abbrev)
    session_folder_name = f"{date_str} {abbrev}"
    session_dir = course_folder / session_folder_name
    code = abbrev.replace(" ", "_")
    class_notes_file = (_COURSES.get(abbrev, {}).get("refinement_prompt")
                        or path_config.PROMPTS_DIR / f"cheat_sheet_prompt_{code}_refinement.md")
    output_file = session_dir / f"{session_folder_name} Notes.docx"

    print(f"\nCheat sheet: {abbrev} {date_str}")
    print(f"Session folder: {session_dir}")

    session_dir.mkdir(parents=True, exist_ok=True)

    # ── Reading files ──────────────────────────────────────────────────────────
    # Sort: largest PDFs first (proxy for "main case"), non-PDFs after
    reading_files = sorted(
        (f for f in session_dir.iterdir()
         if f.is_file() and f.suffix.lower() in READING_EXTS and "Notes" not in f.name),
        key=lambda f: (-f.stat().st_size if f.suffix.lower() == ".pdf" else 0, f.name),
    )

    print(f"Readings found: {len(reading_files)}")
    for f in reading_files:
        print(f"  • {f.name}")

    # ── Canvas assignment ──────────────────────────────────────────────────────
    print("Fetching Canvas assignment...", end=" ", flush=True)
    assignment = fetch_assignment(course_id, date_str)
    if assignment:
        print(f"found: {assignment['name']}")
    else:
        print("not found (will rely on readings only)")

    if not reading_files and not assignment:
        sys.exit("No readings and no Canvas assignment found. Nothing to generate.")

    # ── Build prompt ───────────────────────────────────────────────────────────
    prompt_text = build_prompt(abbrev, assignment, class_notes_file)

    # ── Call Claude ────────────────────────────────────────────────────────────
    import canvas_refresh as _cr  # for pdf_page_count, PDF_PAGE_LIMIT, markdown_to_docx
    api_key = require_config("ANTHROPIC_API_KEY")

    try:
        import anthropic as ant
    except ImportError:
        sys.exit("anthropic not installed. Run: pip install anthropic")

    client = ant.Anthropic(api_key=api_key)

    # Build message content: system prompt + reading docs
    content: list[dict] = []

    skipped: list[str] = []
    if reading_files:
        content.append({"type": "text", "text": "Here are the assigned readings:"})
        for f in reading_files:
            if f.suffix.lower() == ".pdf":
                pages = _cr.pdf_page_count(f)
                if pages > _cr.PDF_PAGE_LIMIT:
                    print(f"  ⚠ Skipped ({pages}p > {_cr.PDF_PAGE_LIMIT}-page limit): {f.name}")
                    skipped.append(f"{f.name} ({pages}p, too long)")
                    content.append({"type": "text", "text": (
                        f"=== {f.name} ===\n"
                        f"[Skipped: {pages} pages exceeds the {_cr.PDF_PAGE_LIMIT}-page limit.]"
                    )})
                    continue
            print(f"  Encoding {f.name}...")
            content.append(file_to_content_block(f))

    content.append({"type": "text", "text": prompt_text})

    print(f"\nCalling Claude ({MODEL})...", flush=True)
    message = client.messages.create(
        model=MODEL,
        max_tokens=8192,
        messages=[{"role": "user", "content": content}],
    )
    if message.stop_reason == "max_tokens":
        print("⚠ Output truncated (hit max_tokens limit) — consider removing some readings")
    cost = (message.usage.input_tokens * 3 + message.usage.output_tokens * 15) / 1_000_000
    print(f"Tokens: {message.usage.input_tokens:,} in / {message.usage.output_tokens:,} out  (~${cost:.3f})")

    result = message.content[0].text

    # Prepend verbatim Canvas posting so it's always at the top of the doc in class
    if assignment:
        desc_text = strip_html(assignment.get("description") or "")
        if desc_text:
            result = f"## Canvas: {assignment.get('name', '')}\n\n{desc_text}\n\n---\n\n" + result

    # Save canvas hash for staleness detection on future runs
    canvas_hash = ""
    if assignment:
        canvas_hash = hashlib.md5(
            strip_html(assignment.get("description") or "").encode()
        ).hexdigest()[:12]
    (session_dir / ".notes_meta.json").write_text(json.dumps({"canvas_hash": canvas_hash}))

    # ── Save output as .docx ──────────────────────────────────────────────────
    metadata: dict[str, str] = {
        "Generated": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    if assignment:
        metadata["Canvas"] = assignment["name"]
    skipped_names = {s.split(" (")[0] for s in skipped}
    included = [f.name for f in reading_files if f.name not in skipped_names]
    if included:
        metadata["Readings"] = ", ".join(included)
    if skipped:
        metadata["Skipped"] = ", ".join(skipped)

    _cr.markdown_to_docx(
        md_text     = result,
        output_path = output_file,
        title       = f"{session_folder_name} Notes",
        metadata    = metadata,
    )
    print(f"\n✅ Saved: {output_file}")
    print(f"   Open: open '{output_file}'")


if __name__ == "__main__":
    main()
