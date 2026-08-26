#!/usr/bin/env python3
"""
Weekly Overview Generator
Produces a scannable markdown summary of the upcoming week.

For each course: reading titles + total page count, and any deliverables due.
Output: "Week of YYYY-MM-DD Overview.md" at the coursework root.

Called automatically from canvas_refresh.py --weekly after the sync step.
Can also be run standalone:
  python3 scripts/weekly_overview.py
"""

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import path_config
import canvas_refresh as cr

_paths    = path_config.resolve()
DEST_ROOT = _paths["coursework_root"]
_COURSES  = _paths["courses"]
BOSTON    = timezone(timedelta(hours=-4))

# submission_types that indicate a class-session prep assignment (not a deliverable)
_SESSION_TYPES = {("not_graded",), ("none",)}

# submission_types that require a student action
_ACTION_TYPES = {
    "online_upload", "online_quiz", "online_text_entry",
    "media_recording", "on_paper", "external_tool", "online_url",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _week_window() -> tuple[datetime, datetime]:
    """Mon 00:00 → Mon 00:00 of the upcoming week (as seen from today/Sunday)."""
    now = datetime.now(tz=BOSTON)
    days_until_monday = (7 - now.weekday()) % 7 or 7
    week_start = (now + timedelta(days=days_until_monday)).replace(
        hour=0, minute=0, second=0, microsecond=0)
    return week_start, week_start + timedelta(days=7)


def _count_pages(session_dir: Path) -> int:
    total = 0
    if not session_dir.exists():
        return 0
    for f in session_dir.iterdir():
        if f.suffix.lower() == ".pdf" and "Notes" not in f.name:
            total += cr.pdf_page_count(f)
    return total


def _classify(sub_types: list) -> str:
    """'session', 'deliverable', or 'ambiguous'."""
    t = tuple(sorted(sub_types or []))
    if t in _SESSION_TYPES:
        return "session"
    if any(s in _ACTION_TYPES for s in sub_types):
        return "deliverable"
    return "ambiguous"


def _short_name(full_name: str, abbrev: str) -> str:
    """Strip leading 'COURSE | Class N |' prefix to keep titles readable."""
    # e.g. "CATS | Class 1 | Capitalism and the State" → "Capitalism and the State"
    # e.g. "CFO | Class 2: Strategic Planning..." → "Class 2: Strategic Planning..."
    parts = full_name.split("|")
    return parts[-1].strip() if len(parts) > 1 else full_name.strip()


def _due_label(dt: datetime) -> str:
    """'Thu 5:00pm', 'Wed 11:59pm'."""
    s = dt.strftime("%a %-I:%M%p")
    return s.replace("AM", "am").replace("PM", "pm")


def _sub_label(sub_types: list) -> str:
    for s in sub_types:
        if s == "online_upload":    return "upload"
        if s == "online_quiz":      return "quiz"
        if s == "online_text_entry":return "text"
        if s == "on_paper":         return "on paper"
        if s == "external_tool":    return "external"
    return "/".join(sub_types)


# ── Main ──────────────────────────────────────────────────────────────────────

def generate(week_start: datetime | None = None) -> Path:
    if week_start is None:
        week_start, week_end = _week_window()
    else:
        week_end = week_start + timedelta(days=7)

    week_label = week_start.strftime("%Y-%m-%d")
    out_file   = DEST_ROOT / f"Week of {week_label} Overview.md"

    lines = [f"# Week of {week_label}", ""]

    for abbrev in sorted(_COURSES):
        info = _COURSES[abbrev]
        if not info.get("folder_path"):
            continue
        cid = info["canvas_id"]

        assignments = cr.canvas_get(f"courses/{cid}/assignments", {"per_page": 100})

        sessions     = []   # (dt, short_name, pages)
        deliverables = []   # (dt, full_name, sub_label)
        ambiguous    = []   # (dt, full_name, sub_types)

        for a in assignments:
            if not a.get("due_at"):
                continue
            dt = datetime.fromisoformat(
                a["due_at"].replace("Z", "+00:00")).astimezone(BOSTON)
            if not (week_start <= dt < week_end):
                continue

            sub  = a.get("submission_types") or []
            kind = _classify(sub)
            name = a["name"]

            if kind == "session":
                date_str    = dt.strftime("%y%m%d")
                session_dir = info["folder_path"] / f"{date_str} {abbrev}"
                pages       = _count_pages(session_dir)
                sessions.append((dt, _short_name(name, abbrev), pages))
            elif kind == "deliverable":
                deliverables.append((dt, name, _sub_label(sub)))
            else:
                ambiguous.append((dt, name, sub))

        sessions.sort(key=lambda x: x[0])
        deliverables.sort(key=lambda x: x[0])

        total_pages = sum(p for _, _, p in sessions)

        lines.append(f"## {abbrev}")

        if sessions:
            case_names  = ", ".join(n for _, n, _ in sessions)
            n           = len(sessions)
            page_note   = f"~{total_pages}p" if total_pages > 0 else "0p — files not yet synced"
            lines.append(f"- Cases: {case_names} ({n} {'session' if n == 1 else 'sessions'}, {page_note})")
        else:
            lines.append("- Cases: nothing scheduled this week")

        if deliverables:
            for dt, name, sub in deliverables:
                lines.append(f"- Due {_due_label(dt)}: {name} ({sub})")
        else:
            lines.append("- Due: nothing this week")

        if ambiguous:
            for dt, name, sub in ambiguous:
                lines.append(f"- ⚠ Check manually: {name} — submission_types={sub}")

        lines.append("")

    out_file.write_text("\n".join(lines))
    return out_file


if __name__ == "__main__":
    out = generate()
    print(f"\n✅ {out}")
    print(out.read_text())
