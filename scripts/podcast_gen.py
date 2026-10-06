#!/usr/bin/env python3
"""
HBS Podcast Generator
Creates a ~20-minute NotebookLM audio overview for a class session.

Output: YYMMDD CLASSCODE Podcast.m4a  (saved in the session folder)

Usage:
  ./.venv/bin/python scripts/podcast_gen.py 260902 LTV
  ./.venv/bin/python scripts/podcast_gen.py 260908 CATS
  ./.venv/bin/python scripts/podcast_gen.py --per-reading 260929 CATS

Flags:
  --per-reading  Generate one 15–20 min podcast per reading file instead of a
                 single combined podcast for the session.
  --force        Delete and regenerate even if the output file already exists.

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
from notebooklm.exceptions import ArtifactInProgressTimeoutError

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


def _build_per_reading_instructions(abbrev: str) -> str:
    """Load per-reading (15–20 min) podcast prompt."""
    prompt_file = _PROMPTS_DIR / "podcast_prompt_per_reading.md"
    base = prompt_file.read_text() if prompt_file.exists() else (
        "Create a focused 15–20 minute podcast on this single reading for an HBS MBA student."
    )
    return _re.sub(r"\n*\[CLASS-SPECIFIC NOTES\].*", "", base, flags=_re.DOTALL).strip()


async def _generate(date_str: str, abbrev: str, force: bool = False):
    from notebooklm import NotebookLMClient

    course_folder = _COURSES.get(abbrev, {}).get("folder_path") or DEST_ROOT / abbrev
    session_dir   = course_folder / f"{date_str} {abbrev}"
    session_label = f"{date_str} {abbrev}"
    podcast_file  = session_dir / f"{session_label} Podcast.m4a"

    if podcast_file.exists():
        if force:
            podcast_file.unlink()
            print(f"--force: deleted {podcast_file.name}")
        else:
            print(f"Already exists: {podcast_file}")
            return

    session_dir.mkdir(parents=True, exist_ok=True)

    # ── Reading files ──────────────────────────────────────────────────────────
    reading_files = sorted(
        (f for f in session_dir.iterdir()
         if f.is_file() and f.suffix.lower() in _cr.READING_EXTS and "Notes" not in f.name
         and f.suffix.lower() != ".m4a"
         # "(skipped)" stubs say a reading was left out — uploading one as a
         # source tells the hosts about a file they cannot see. "~$" files are
         # Word lock files, not documents.
         and "(skipped)" not in f.name and not f.name.startswith("~$")),
        key=lambda f: (-f.stat().st_size if f.suffix.lower() == ".pdf" else 0, f.name),
    )
    # Exclude PDFs over the page limit, and byte-identical repeats of a reading
    # Canvas attached in two places.
    import hashlib
    usable, seen = [], {}
    for f in reading_files:
        if f.suffix.lower() == ".pdf":
            pages = _cr.pdf_page_count(f)
            if pages > _cr.PDF_PAGE_LIMIT:
                print(f"  ⚠ Skipping ({pages}p > {_cr.PDF_PAGE_LIMIT}p limit): {f.name}")
                continue
        digest = hashlib.md5(f.read_bytes()).hexdigest()
        if digest in seen:
            print(f"  – Duplicate of {seen[digest]}, uploading once: {f.name}")
            continue
        seen[digest] = f.name
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
        # A render that outran the poll ceiling keeps going on NotebookLM's side.
        # Collect a finished one rather than paying for the same audio twice —
        # without this, every retry queued a second render and waited again.
        existing_audio = None
        try:
            for art in await client.artifacts.list_audio(nb.id):
                if art.is_completed:
                    existing_audio = art
                    break
        except Exception as e:
            print(f"  (could not list existing audio: {e})")

        if force and existing_audio is not None:
            print("  --force: ignoring existing completed audio, regenerating.")
            existing_audio = None

        if existing_audio is not None:
            mins = int((existing_audio.duration_seconds or 0) // 60)
            print(f"\n  Audio from an earlier run has finished ({mins} min) — "
                  f"collecting it instead of generating again.")
        else:
            print(f"\nGenerating audio overview (~5–15 min)"
                  f"{' [with supplemental frameworks]' if has_supplemental else ''}...",
                  flush=True)
            status = await client.artifacts.generate_audio(
                nb.id,
                instructions=instructions,
            )
            print(f"  Task: {status.task_id}")

            def _on_change(s):
                print(f"  → {s.status}")

            try:
                await client.artifacts.wait_for_completion(
                    nb.id,
                    status.task_id,
                    # A six-source notebook regularly runs past 20 minutes. The
                    # old ceiling abandoned finished work and reported failure.
                    timeout=2700.0,
                    on_status_change=_on_change,
                )
            except ArtifactInProgressTimeoutError:
                print(f"\n  Still rendering after 45 min. NotebookLM keeps going "
                      f"without us — re-run this command later and it will "
                      f"download the finished audio:")
                print(f"    ./.venv/bin/python scripts/podcast_gen.py "
                      f"{date_str} {abbrev}")
                return

        # Download
        print(f"  ↓ Downloading...")
        await client.artifacts.download_audio(nb.id, str(podcast_file))

    print(f"\n✅ Saved: {podcast_file}")
    print(f"   Play:  open '{podcast_file}'")


async def _generate_per_reading(date_str: str, abbrev: str, force: bool = False):
    """Generate one 15–20 min podcast per reading file for the session."""
    from notebooklm import NotebookLMClient

    course_folder = _COURSES.get(abbrev, {}).get("folder_path") or DEST_ROOT / abbrev
    session_dir   = course_folder / f"{date_str} {abbrev}"
    session_label = f"{date_str} {abbrev}"

    session_dir.mkdir(parents=True, exist_ok=True)

    # ── Reading files ──────────────────────────────────────────────────────────
    reading_files = sorted(
        (f for f in session_dir.iterdir()
         if f.is_file() and f.suffix.lower() in _cr.READING_EXTS and "Notes" not in f.name
         and f.suffix.lower() != ".m4a"
         and "(skipped)" not in f.name and not f.name.startswith("~$")),
        key=lambda f: (-f.stat().st_size if f.suffix.lower() == ".pdf" else 0, f.name),
    )
    import hashlib
    usable, seen_digests = [], {}
    for f in reading_files:
        if f.suffix.lower() == ".pdf":
            pages = _cr.pdf_page_count(f)
            if pages > _cr.PDF_PAGE_LIMIT:
                print(f"  ⚠ Skipping ({pages}p > {_cr.PDF_PAGE_LIMIT}p limit): {f.name}")
                continue
        digest = hashlib.md5(f.read_bytes()).hexdigest()
        if digest in seen_digests:
            continue
        seen_digests[digest] = f.name
        usable.append(f)
    reading_files = usable

    if not reading_files:
        sys.exit("No reading files found in session directory.")

    print(f"\nPer-reading podcasts: {abbrev} {date_str}")
    print(f"Session: {session_dir}")
    print(f"Readings ({len(reading_files)}):")
    for f in reading_files:
        print(f"  • {f.name}")

    # ── Canvas assignment (for discussion questions) ────────────────────────────
    course_id = COURSE_IDS[abbrev]
    print("Fetching Canvas assignment...", end=" ", flush=True)
    assignment = None
    for a in _cr.canvas_get(f"courses/{course_id}/assignments", {"per_page": 100}):
        if a.get("due_at") and _cr.yymmdd(_cr.boston_date(a["due_at"])) == date_str:
            assignment = a
            break
    print(f"found: {assignment['name']}" if assignment else "not found")

    instructions = _build_per_reading_instructions(abbrev)

    failed_readings: list[str] = []
    async with NotebookLMClient.from_storage() as client:
        for i, reading_file in enumerate(reading_files, 1):
            reading_stem = reading_file.stem
            nb_title     = f"{session_label} - {reading_stem}"
            podcast_file = session_dir / f"{session_label} Podcast - {reading_stem}.m4a"

            print(f"\n[{i}/{len(reading_files)}] {reading_file.name}")

            if podcast_file.exists():
                if force:
                    podcast_file.unlink()
                    print(f"  --force: deleted {podcast_file.name}")
                else:
                    print(f"  Already exists: {podcast_file.name} — skipping")
                    continue

            try:
                # Find or create notebook
                notebooks = await client.notebooks.list()
                nb = next((n for n in notebooks if n.title == nb_title), None)
                if nb:
                    print(f"  Reusing notebook: {nb.title}")
                else:
                    nb = await client.notebooks.create(nb_title)
                    print(f"  Created notebook:  {nb.title}")

                # Upload sources only if notebook is empty
                existing = await client.sources.list(nb.id)
                if existing:
                    print(f"  {len(existing)} source(s) already in notebook — skipping upload")
                else:
                    print(f"  ↑ Uploading {reading_file.name}...")
                    await client.sources.add_file(nb.id, str(reading_file), wait=True, wait_timeout=180.0)

                    if assignment:
                        title = assignment.get("name", f"{session_label} Assignment")
                        desc  = _cr.strip_html(assignment.get("description") or "")
                        print(f"  + Adding Canvas assignment: {title}")
                        await client.sources.add_text(nb.id, title, desc, wait=True)

                # Check for existing completed audio
                existing_audio = None
                try:
                    for art in await client.artifacts.list_audio(nb.id):
                        if art.is_completed:
                            existing_audio = art
                            break
                except Exception as e:
                    print(f"  (could not list existing audio: {e})")

                if force and existing_audio is not None:
                    print("  --force: ignoring existing completed audio, regenerating.")
                    existing_audio = None

                if existing_audio is not None:
                    mins = int((existing_audio.duration_seconds or 0) // 60)
                    print(f"  Audio from an earlier run has finished ({mins} min) — collecting it.")
                else:
                    print(f"  Generating audio overview (~5–10 min)...", flush=True)
                    status = await client.artifacts.generate_audio(nb.id, instructions=instructions)
                    print(f"  Task: {status.task_id}")

                    def _on_change(s):
                        print(f"  → {s.status}")

                    try:
                        await client.artifacts.wait_for_completion(
                            nb.id, status.task_id,
                            timeout=2700.0,
                            on_status_change=_on_change,
                        )
                    except ArtifactInProgressTimeoutError:
                        print(f"\n  Still rendering after 45 min. Re-run later:")
                        print(f"    ./.venv/bin/python scripts/podcast_gen.py "
                              f"--per-reading {date_str} {abbrev}")
                        continue

                print(f"  ↓ Downloading...")
                await client.artifacts.download_audio(nb.id, str(podcast_file))
                print(f"  ✅ Saved: {podcast_file.name}")

            except Exception as e:
                print(f"  ✗ Failed ({type(e).__name__}: {e}) — skipping, re-run to retry")
                failed_readings.append(reading_file.name)

    made = len(reading_files) - len(failed_readings)
    print(f"\nDone — {made}/{len(reading_files)} per-reading podcast(s) for {session_label}.")
    if failed_readings:
        print(f"  Failed ({len(failed_readings)}): {', '.join(failed_readings)}")
        print(f"  Re-run to retry: ./.venv/bin/python scripts/podcast_gen.py --per-reading {date_str} {abbrev}")


def main():
    args = [a for a in sys.argv[1:] if a not in ("--force", "--per-reading")]
    force       = "--force" in sys.argv
    per_reading = "--per-reading" in sys.argv

    if len(args) < 2:
        print(__doc__)
        sys.exit(1)

    date_str = args[0]
    abbrev   = " ".join(args[1:]).upper()

    if abbrev not in COURSE_IDS:
        sys.exit(f"Unknown course '{abbrev}'. Known: {', '.join(COURSE_IDS)}")

    try:
        if per_reading:
            asyncio.run(_generate_per_reading(date_str, abbrev, force=force))
        else:
            asyncio.run(_generate(date_str, abbrev, force=force))
    except KeyboardInterrupt:
        print("\nInterrupted.")


if __name__ == "__main__":
    main()
