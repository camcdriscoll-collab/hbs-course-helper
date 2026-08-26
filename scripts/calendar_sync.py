#!/usr/bin/env python3
"""
Calendar Sync — Canvas deadlines → Apple Calendar (auto-syncs to Google Calendar)

Pulls every Canvas assignment that requires student action and creates an all-day
event on the due date. Events in Apple Calendar sync to Google Calendar automatically.

Event title format:  "5:00pm — LTV Writing Assignment #1 (LTV)"

Idempotent: state is tracked in ~/.canvas_calendar_state.json — reruns won't
create duplicates.

Run standalone:
  python3 scripts/calendar_sync.py            # live
  python3 scripts/calendar_sync.py --dry-run  # print only, no events created

Also called from canvas_refresh.py --daily and --weekly.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import path_config
import canvas_refresh as cr

_paths   = path_config.resolve()
_COURSES = _paths["courses"]
BOSTON   = timezone(timedelta(hours=-4))

CALENDAR_NAME = "camcdriscoll@gmail.com"
STATE_FILE    = Path.home() / ".canvas_calendar_state.json"

# Submission type classification (same logic as weekly_overview.py)
_SESSION_TYPES = {("not_graded",), ("none",)}
_ACTION_TYPES  = {
    "online_upload", "online_quiz", "online_text_entry",
    "media_recording", "on_paper", "external_tool", "online_url",
}


# ── Helpers ───────────────────────────────────────────────────────────────────

def _classify(sub_types: list) -> str:
    t = tuple(sorted(sub_types or []))
    if t in _SESSION_TYPES:
        return "session"
    if any(s in _ACTION_TYPES for s in sub_types):
        return "deliverable"
    return "ambiguous"


def _event_title(name: str, abbrev: str, dt: datetime) -> str:
    time_str = dt.strftime("%-I:%M%p").replace("AM", "am").replace("PM", "pm")
    return f"{time_str} — {name} ({abbrev})"


def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text()) if STATE_FILE.exists() else {}
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, indent=2, sort_keys=True))


def _osascript_create(title: str, date_iso: str) -> str:
    """
    Create an all-day event via Apple Calendar (osascript).
    Returns: 'created', 'exists', or 'error: <message>'.
    """
    dt       = datetime.fromisoformat(date_iso)
    date_str = dt.strftime("%B %-d, %Y")   # "September 10, 2026"

    # Escape any double-quotes in the title before embedding in AppleScript
    safe_title = title.replace('"', '\\"')

    script = f"""
tell application "Calendar"
    tell calendar "{CALENDAR_NAME}"
        set evt_date to date "{date_str}"
        set matching to (every event whose summary = "{safe_title}" and start date = evt_date)
        if (count of matching) > 0 then
            return "exists"
        end if
        make new event with properties {{summary:"{safe_title}", start date:evt_date, end date:evt_date, allday event:true}}
        return "created"
    end tell
end tell
"""
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=15,
    )
    if result.returncode != 0:
        return f"error: {result.stderr.strip() or result.stdout.strip()}"
    return result.stdout.strip()


# ── Main ──────────────────────────────────────────────────────────────────────

def run(dry_run: bool = False) -> None:
    state    = _load_state()
    created  = skipped = already = errors = 0
    ambiguous_found: list[tuple] = []

    print(f"\n{'─'*55}")
    print(f"  CALENDAR SYNC{'  [dry run]' if dry_run else ''}")
    print(f"{'─'*55}")

    for abbrev in sorted(_COURSES):
        info = _COURSES[abbrev]
        if not info.get("canvas_id"):
            continue
        cid = info["canvas_id"]

        for a in cr.canvas_get(f"courses/{cid}/assignments", {"per_page": 100}):
            if not a.get("due_at"):
                continue

            sub  = a.get("submission_types") or []
            kind = _classify(sub)

            if kind == "session":
                continue

            dt = datetime.fromisoformat(
                a["due_at"].replace("Z", "+00:00")).astimezone(BOSTON)

            if kind == "ambiguous":
                ambiguous_found.append((abbrev, a["name"], sub, dt))
                continue

            title     = _event_title(a["name"], abbrev, dt)
            date_iso  = dt.date().isoformat()
            state_key = f"{date_iso}|{title}"

            if state_key in state:
                already += 1
                continue

            if dry_run:
                print(f"  + {dt.strftime('%b %d')}  {title}")
                created += 1
                continue

            outcome = _osascript_create(title, date_iso)

            if outcome == "created":
                print(f"  + {dt.strftime('%b %d')}  {title}")
                state[state_key] = dt.isoformat()
                created += 1
            elif outcome == "exists":
                state[state_key] = dt.isoformat()   # backfill state
                already += 1
            else:
                print(f"  ✗ {title}: {outcome}")
                errors += 1

    if not dry_run and (created or already):
        _save_state(state)

    print(f"\n  Created: {created}  Already existed: {already}  Errors: {errors}")

    if ambiguous_found:
        print(f"\n  ⚠ Ambiguous — not added to calendar, review manually:")
        for abbrev, name, sub, dt in sorted(ambiguous_found, key=lambda x: x[3]):
            print(f"    [{abbrev} {dt.strftime('%Y-%m-%d')}] {name}")
            print(f"      submission_types={sub}")

    print(f"{'─'*55}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print events without creating them")
    args = parser.parse_args()
    run(dry_run=args.dry_run)
