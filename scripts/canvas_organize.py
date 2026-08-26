#!/usr/bin/env python3
"""
canvas_organize.py — Deduplication and folder organization

Rules (applied in order, each file ends up in exactly one place):

  PPTX/PPT (slides) — always go to General/Slides/, regardless of where
  Canvas placed them (even if in a per-class session folder).

  PDFs / docs in session folders (YYMMDD ABBREV/) — stay there; any
  duplicate copy in General/ is removed.

  PDFs / docs in General/ root:
    - Course-level doc (syllabus, schedule, guide, etc.) → stays in General/
    - Otherwise → General/Supplemental/

  Supplemental/ and Slides/ files — never touched (already canonical).

Safe by design:
  - Only removes a duplicate if byte sizes match (size mismatch → warns, keeps both)
  - Idempotent — safe to re-run any time

Run manually:    python3 canvas_organize.py
Also called automatically after every canvas_refresh.py sync.
"""

import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import path_config

COURSEWORK_ROOT = path_config.COURSEWORK_ROOT
SESSION_RE = re.compile(r'^\d{6}\s')

SLIDE_EXTS = {'.pptx', '.ppt'}

COURSE_LEVEL_KEYWORDS = {
    "syllabus", "syllabi", "calendar", "schedule", "overview", "guide",
    "resource", "reference", "background", "appendix", "rubric", "grading",
    "policy", "handbook", "orientation", "welcome", "introduction",
}


def _is_course_level(filename: str) -> bool:
    stem = Path(filename).stem.lower()
    pattern = r'\b(' + '|'.join(re.escape(k) for k in COURSE_LEVEL_KEYWORDS) + r')\b'
    return bool(re.search(pattern, stem))


def _session_file_index(course_dir: Path) -> dict[str, list[Path]]:
    """Map filename → list of session folder paths where it appears."""
    index: dict[str, list[Path]] = {}
    for d in course_dir.iterdir():
        if d.is_dir() and SESSION_RE.match(d.name):
            for f in d.iterdir():
                if f.is_file() and not f.name.startswith('.'):
                    index.setdefault(f.name, []).append(f)
    return index


def _migrate_session_slides(course_dir: Path, slides_dir: Path, counts: dict) -> None:
    """
    Move any .pptx/.ppt files found in session folders → General/Slides/.
    PPTX always belongs in Slides/, never in a per-class folder.
    """
    for d in sorted(course_dir.iterdir()):
        if not (d.is_dir() and SESSION_RE.match(d.name)):
            continue
        for f in sorted(d.iterdir()):
            if f.is_file() and f.suffix.lower() in SLIDE_EXTS:
                slides_dir.mkdir(parents=True, exist_ok=True)
                dest = slides_dir / f.name
                if dest.exists():
                    if dest.stat().st_size == f.stat().st_size:
                        f.unlink()
                        counts["slides"] += 1
                        print(f"    ✗ duplicate slide removed from session: {f.name}  ({d.name})")
                    else:
                        counts["warned"] += 1
                        print(f"    ⚠ slide size mismatch, leaving both: {f.name}")
                else:
                    f.rename(dest)
                    counts["slides"] += 1
                    print(f"    → Slides/ (from {d.name}): {f.name}")


def organize_course(course_dir: Path, abbrev: str) -> dict:
    """Organize a single course directory. Returns counts of actions taken."""
    general = course_dir / "General"
    if not general.exists():
        return {}

    slides_dir       = general / "Slides"
    supplemental_dir = general / "Supplemental"

    supplemental_dir.mkdir(exist_ok=True)

    counts = {"removed": 0, "slides": 0, "supplemental": 0, "kept": 0, "warned": 0}

    # ── Step 0: Pull PPTX out of session folders → Slides/ ───────────────────
    _migrate_session_slides(course_dir, slides_dir, counts)

    # Rebuild session index after moving slides (so the index reflects current state)
    session_index = _session_file_index(course_dir)

    # ── Step 1-4: Process files in General/ root ──────────────────────────────
    for f in sorted(general.iterdir()):
        if f.is_dir() or f.name.startswith('.'):
            continue

        # 1. Duplicate of a session file → remove from General/
        if f.name in session_index:
            session_copies = session_index[f.name]
            identical = any(s.stat().st_size == f.stat().st_size for s in session_copies)
            if identical:
                f.unlink()
                counts["removed"] += 1
                locations = ", ".join(p.parent.name for p in session_copies)
                print(f"    ✗ duplicate removed: {f.name}  (in: {locations})")
            else:
                counts["warned"] += 1
                print(f"    ⚠ size mismatch, leaving both: {f.name}")
            continue

        # 2. Slides → General/Slides/
        if f.suffix.lower() in SLIDE_EXTS:
            slides_dir.mkdir(exist_ok=True)
            dest = slides_dir / f.name
            if dest.exists():
                f.unlink()
            else:
                f.rename(dest)
            counts["slides"] += 1
            print(f"    → Slides/: {f.name}")
            continue

        # 3. Course-level doc → keep in General/ root
        if _is_course_level(f.name):
            counts["kept"] += 1
            continue

        # 4. Everything else → General/Supplemental/
        dest = supplemental_dir / f.name
        if dest.exists():
            f.unlink()
        else:
            f.rename(dest)
        counts["supplemental"] += 1
        print(f"    → Supplemental/: {f.name}")

    return counts


def _trash(p: Path) -> None:
    """Move a file to macOS Trash, handling name collisions."""
    trash_dir = Path.home() / ".Trash"
    dest = trash_dir / p.name
    i = 1
    while dest.exists():
        dest = trash_dir / f"{p.stem}_{i}{p.suffix}"
        i += 1
    shutil.move(str(p), str(dest))


def _classify_priority(p: Path, coursework_root: Path) -> int:
    """
    Return priority tier for a file (lower number = keep this copy).
      0 — General/Slides/      (canonical home for PPTX)
      1 — session folder       (canonical for PDFs tied to a day)
      2 — General/Supplemental/
      3 — General/ root
      5 — course root (file directly under COURSE/, not in a subfolder)
    """
    try:
        parts = list(p.relative_to(coursework_root).parts)
    except ValueError:
        return 9
    if len(parts) <= 2:
        return 5
    sub = parts[1]
    if SESSION_RE.match(sub):
        return 1
    if sub == "General":
        if len(parts) >= 4:
            if parts[2] == "Slides":        return 0
            if parts[2] == "Supplemental":  return 2
        return 3
    return 5


def dedup_to_trash(verbose: bool = True) -> int:
    """
    Scan all course folders for duplicate filenames and move lower-priority
    copies to macOS Trash. Returns count of files trashed.

    Priority (highest = keep):
      General/Slides/  >  session folder  >  Supplemental/  >  General/ root
    For duplicate session-folder copies, keep the earliest date.
    """
    paths = path_config.resolve()
    root = paths["coursework_root"]

    by_name: dict[str, list[Path]] = defaultdict(list)
    for f in sorted(root.rglob("*")):
        if (not f.is_file() or f.name.startswith(".")
                or "claude" in f.parts):
            continue
        by_name[f.name].append(f)

    trashed = 0
    for name, file_paths in sorted(by_name.items()):
        if len(file_paths) == 1:
            continue

        suffix = Path(name).suffix.lower()
        is_slide = suffix in SLIDE_EXTS

        if is_slide:
            slides = [p for p in file_paths if _classify_priority(p, root) == 0]
            others = [p for p in file_paths if _classify_priority(p, root) != 0]
            if slides:
                for p in others:
                    if verbose:
                        print(f"    🗑  duplicate slide → Trash: {p.relative_to(root)}")
                    _trash(p)
                    trashed += 1
            else:
                ranked = sorted(file_paths, key=lambda p: _classify_priority(p, root))
                for p in ranked[1:]:
                    if verbose:
                        print(f"    🗑  duplicate → Trash: {p.relative_to(root)}")
                    _trash(p)
                    trashed += 1
        else:
            # Keep: earliest session copy > supplemental > general_root > course_root
            sessions = sorted(
                [p for p in file_paths if _classify_priority(p, root) == 1],
                key=lambda p: next(
                    (pt for pt in p.relative_to(root).parts if SESSION_RE.match(pt)),
                    "999999"
                ),
            )
            ranked = (sessions[:1]
                      + [p for p in file_paths if _classify_priority(p, root) == 2]
                      + [p for p in file_paths if _classify_priority(p, root) == 3]
                      + [p for p in file_paths if _classify_priority(p, root) == 5])
            keep = ranked[0] if ranked else None
            for p in file_paths:
                if p != keep:
                    if verbose:
                        print(f"    🗑  duplicate → Trash: {p.relative_to(root)}")
                    _trash(p)
                    trashed += 1

    return trashed


def organize_all(verbose: bool = True) -> None:
    """Run organize_course for every course in COURSEWORK_ROOT."""
    paths = path_config.resolve()
    any_action = False

    for abbrev, data in paths["courses"].items():
        folder_path = data.get("folder_path")
        if not folder_path or not folder_path.exists():
            continue

        counts = organize_course(folder_path, abbrev)
        actions = (counts.get("removed", 0) + counts.get("slides", 0)
                   + counts.get("supplemental", 0) + counts.get("warned", 0))

        if actions > 0:
            any_action = True
            if verbose:
                parts = []
                if counts["removed"]:      parts.append(f"{counts['removed']} duplicates removed")
                if counts["slides"]:       parts.append(f"{counts['slides']} slides → Slides/")
                if counts["supplemental"]: parts.append(f"{counts['supplemental']} → Supplemental/")
                if counts["warned"]:       parts.append(f"{counts['warned']} conflicts skipped")
                print(f"  {abbrev}: {', '.join(parts)}")

    if not any_action and verbose:
        print("  All folders already clean — nothing to do.")


if __name__ == "__main__":
    print(f"\nOrganizing: {COURSEWORK_ROOT}\n")
    organize_all(verbose=True)
    print("\n✅ Done.\n")
