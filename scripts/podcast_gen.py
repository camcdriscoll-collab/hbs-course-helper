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
import re as _re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import path_config
import canvas_refresh as _cr

_paths  = path_config.resolve()
DEST_ROOT = _paths["coursework_root"]
_COURSES  = _paths["courses"]
COURSE_IDS = {a: d["canvas_id"] for a, d in _COURSES.items()}

_PROMPTS_DIR = _paths["prompts_dir"]


def _build_instructions(reading_files: list, abbrev: str) -> str:
    """Load podcast prompt from file, append course-specific notes if present."""
    has_supplemental = len(reading_files) > 1
    prompt_file = (
        _PROMPTS_DIR / "podcast_prompt_supplemental.md"
        if has_supplemental
        else _PROMPTS_DIR / "podcast_prompt.md"
    )
    base = prompt_file.read_text() if prompt_file.exists() else ""

    # Load per-course refinement (same pattern as cheat sheet)
    code = abbrev.replace(" ", "_")
    refinement_file = _PROMPTS_DIR / f"podcast_prompt_{code}_refinement.md"
    class_notes = ""
    if refinement_file.exists():
        raw = refinement_file.read_text().strip()
        raw = _re.sub(r"<!--.*?-->", "", raw, flags=_re.DOTALL).strip()
        if raw and raw != "# CLASS-SPECIFIC NOTES":
            class_notes = f"\n\n{raw}"

    if class_notes:
        instructions = _re.sub(r"\[CLASS-SPECIFIC NOTES\].*", class_notes, base, flags=_re.DOTALL)
    else:
        instructions = _re.sub(r"\n*\[CLASS-SPECIFIC NOTES\].*", "", base, flags=_re.DOTALL)

    return instructions.strip()


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
        instructions = _build_instructions(reading_files, abbrev)
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
