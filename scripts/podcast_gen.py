#!/usr/bin/env python3
"""
HBS Podcast Generator
Creates a ~20-minute NotebookLM audio overview for a class session.

Output: YYMMDD CLASSCODE Podcast.m4a  (saved in the session folder)

Usage:
  python3 ~/Desktop/Coursework/claude/scripts/podcast_gen.py 260902 LTV
  python3 ~/Desktop/Coursework/claude/scripts/podcast_gen.py 260908 CATS

How it works:
  1. Finds the session folder and reading PDFs
  2. Creates (or reuses) a NotebookLM notebook for the session
  3. Uploads readings + Canvas discussion questions as sources
  4. Generates a ~20-min audio overview and waits for it to finish
  5. Downloads the podcast to the session folder as an .m4a file

Prerequisites:
  - notebooklm-py installed: pip install 'notebooklm-py[browser]'
  - One-time login:
      ~/repos/hbs-course-helper/.venv/bin/notebooklm login
  - After that, cookies are cached in ~/.notebooklm/profiles/default/
    and all scripts use them automatically.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import path_config
import canvas_refresh as _cr

_paths  = path_config.resolve()
DEST_ROOT = _paths["coursework_root"]
_COURSES  = _paths["courses"]
COURSE_IDS = {a: d["canvas_id"] for a, d in _COURSES.items()}

_INSTRUCTIONS_BASE = """\
Create an in-depth, conversational podcast (approximately 30 minutes) for an MBA \
student preparing for this Harvard Business School class session.

Structure (approximate timing):
1. Case Overview — walk through the full company situation in depth: what happened, \
the key decision that needs to be made, who the players are, the numbers that matter, \
and what's really at stake (20 min)
2. Discussion Questions — work through each discussion question directly, \
referencing specific facts and figures from the case. Explore the tensions and \
tradeoffs honestly rather than giving tidy answers (10 min)

Style: two hosts having a substantive academic conversation at the level of an \
HBS second-year student. Prioritise depth and case specificity over broad generality. \
Use HBS case discussion conventions.\
"""

_INSTRUCTIONS_WITH_SUPPLEMENTAL = """\
Create an in-depth, conversational podcast (approximately 35 minutes) for an MBA \
student preparing for this Harvard Business School class session.

Structure (approximate timing):
1. Case Overview — walk through the full company situation in depth: what happened, \
the key decision that needs to be made, who the players are, the numbers that matter, \
and what's really at stake (20 min)
2. Frameworks & Class Prep — draw on the supplemental readings to build the \
analytical framework most relevant to this case; note the key concepts, how they \
apply here specifically, and what a strong participant contribution looks like (5 min)
3. Discussion Questions — work through each discussion question directly, \
referencing specific facts and figures from the case and applying the frameworks \
from the supplemental materials. Explore tensions and tradeoffs honestly (10 min)

Style: two hosts having a substantive academic conversation at the level of an \
HBS second-year student. Prioritise depth and case specificity over broad generality. \
Use HBS case discussion conventions.\
"""


def _build_instructions(reading_files: list) -> str:
    """Use extended instructions only when supplemental readings are present."""
    # First file is the main case (largest PDF). Any additional files are supplemental.
    has_supplemental = len(reading_files) > 1
    return _INSTRUCTIONS_WITH_SUPPLEMENTAL if has_supplemental else _INSTRUCTIONS_BASE


async def _generate(date_str: str, abbrev: str):
    from notebooklm import NotebookLMClient

    course_folder = _COURSES.get(abbrev, {}).get("folder_path") or DEST_ROOT / abbrev
    session_dir   = course_folder / f"{date_str} {abbrev}"
    session_label = f"{date_str} {abbrev}"
    podcast_file  = session_dir / f"{session_label} Podcast.m4a"

    if podcast_file.exists():
        print(f"Already exists: {podcast_file}")
        return

    session_dir.mkdir(parents=True, exist_ok=True)

    # ── Reading files ──────────────────────────────────────────────────────────
    reading_files = sorted(
        (f for f in session_dir.iterdir()
         if f.is_file() and f.suffix.lower() in _cr.READING_EXTS and "Notes" not in f.name
         and f.suffix.lower() != ".m4a"),
        key=lambda f: (-f.stat().st_size if f.suffix.lower() == ".pdf" else 0, f.name),
    )
    # Exclude PDFs over the page limit (too long to index well)
    usable = []
    for f in reading_files:
        if f.suffix.lower() == ".pdf":
            pages = _cr.pdf_page_count(f)
            if pages > _cr.PDF_PAGE_LIMIT:
                print(f"  ⚠ Skipping ({pages}p > {_cr.PDF_PAGE_LIMIT}p limit): {f.name}")
                continue
        usable.append(f)
    reading_files = usable

    print(f"\nPodcast: {abbrev} {date_str}")
    print(f"Session: {session_dir}")
    print(f"Readings ({len(reading_files)}):")
    for f in reading_files:
        print(f"  • {f.name}")

    # ── Canvas assignment (for discussion questions) ────────────────────────────
    course_id  = COURSE_IDS[abbrev]
    print("Fetching Canvas assignment...", end=" ", flush=True)
    assignment = None
    for a in _cr.canvas_get(f"courses/{course_id}/assignments", {"per_page": 100}):
        if a.get("due_at") and _cr.yymmdd(_cr.boston_date(a["due_at"])) == date_str:
            assignment = a
            break
    print(f"found: {assignment['name']}" if assignment else "not found")

    if not reading_files and not assignment:
        sys.exit("No readings and no Canvas assignment — nothing to generate from.")

    # ── NotebookLM ─────────────────────────────────────────────────────────────
    async with NotebookLMClient.from_storage() as client:

        # Find or create notebook
        notebooks = await client.notebooks.list()
        nb = next((n for n in notebooks if n.title == session_label), None)
        if nb:
            print(f"Reusing notebook: {nb.title}")
        else:
            nb = await client.notebooks.create(session_label)
            print(f"Created notebook:  {nb.title}")

        # Upload sources only if notebook is empty (avoids re-uploading on retry)
        existing = await client.sources.list(nb.id)
        if existing:
            print(f"  {len(existing)} source(s) already in notebook — skipping upload")
        else:
            for f in reading_files:
                print(f"  ↑ Uploading {f.name}...")
                await client.sources.add_file(nb.id, str(f), wait=True, wait_timeout=180.0)

            if assignment:
                title = assignment.get("name", f"{session_label} Assignment")
                desc  = _cr.strip_html(assignment.get("description") or "")
                print(f"  + Adding Canvas assignment: {title}")
                await client.sources.add_text(nb.id, title, desc, wait=True)

        # Generate
        instructions = _build_instructions(reading_files)
        has_supplemental = len(reading_files) > 1
        print(f"\nGenerating audio overview (~5–15 min)"
              f"{' [with supplemental frameworks]' if has_supplemental else ''}...", flush=True)
        status = await client.artifacts.generate_audio(
            nb.id,
            instructions=instructions,
        )
        print(f"  Task: {status.task_id}")

        # Wait
        def _on_change(s):
            print(f"  → {s.status}")

        await client.artifacts.wait_for_completion(
            nb.id,
            status.task_id,
            timeout=1200.0,      # 20-minute ceiling
            on_status_change=_on_change,
        )

        # Download
        print(f"  ↓ Downloading...")
        await client.artifacts.download_audio(nb.id, str(podcast_file))

    print(f"\n✅ Saved: {podcast_file}")
    print(f"   Play:  open '{podcast_file}'")


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)

    date_str = sys.argv[1]
    abbrev   = " ".join(sys.argv[2:]).upper()

    if abbrev not in COURSE_IDS:
        sys.exit(f"Unknown course '{abbrev}'. Known: {', '.join(COURSE_IDS)}")

    try:
        asyncio.run(_generate(date_str, abbrev))
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
