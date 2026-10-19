#!/usr/bin/env python3
"""
finals_digest.py — Final exam logistics digest

Scans Canvas assignments, announcements, and pages for each active course
to find final exam / project information, then:
  1. Produces Finals Digest.docx at the Coursework root
  2. Creates Apple Calendar events for each deadline

Usage:
  python3 scripts/finals_digest.py
  python3 scripts/finals_digest.py --dry-run   # Word doc only, no calendar events
"""

import argparse
import html as _html
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))
import path_config
import canvas_refresh as cr
import ai_config

_paths    = path_config.resolve()
DEST_ROOT = _paths["coursework_root"]
_COURSES  = _paths["courses"]
ENV_FILE  = _paths["env_file"]
BOSTON    = ZoneInfo("America/New_York")

COURSE_NAMES  = cr.COURSE_NAMES
ACTIVE_COURSES = ["CATS", "IMAGE", "IMCM", "LME", "LTV"]
CALENDAR_NAME  = "Canvas Assignments"

# Matches finals in assignment/announcement names
FINAL_RE = re.compile(
    r'\b(final|exam|take.?home|self.?scheduled|capstone)\b', re.I
)


# ── HTML → plain text ─────────────────────────────────────────────────────────

def strip_html(s: str) -> str:
    s = re.sub(r'<br\s*/?>', '\n', s or '')
    s = re.sub(r'</?p[^>]*>', '\n', s)
    s = re.sub(r'<li[^>]*>', '\n• ', s)
    s = re.sub(r'<h[1-6][^>]*>', '\n### ', s)
    s = re.sub(r'</h[1-6]>', '\n', s)
    s = re.sub(r'<strong>(.*?)</strong>', r'**\1**', s)
    s = re.sub(r'<em>(.*?)</em>', r'*\1*', s)
    s = re.sub(r'<[^>]+>', '', s)
    s = _html.unescape(s)
    s = re.sub(r'\n{3,}', '\n\n', s)
    return s.strip()


# ── Canvas data collection ────────────────────────────────────────────────────

def _collect(abbrev: str, cid: str) -> list[dict]:
    """
    Return all Canvas items that look like finals for this course.
    Each item: {source, name, due_at_iso, text, submission_types, points}
    """
    items = []

    # Assignments — match FINAL_RE in name, or any assignment due Nov/Dec
    for a in cr.canvas_get(f"courses/{cid}/assignments", {"per_page": 100}):
        name = a.get("name", "")
        due  = a.get("due_at")
        late = False
        if due:
            try:
                dt = datetime.fromisoformat(due.replace("Z", "+00:00")).astimezone(BOSTON)
                late = dt.month >= 11
            except Exception:
                pass
        if FINAL_RE.search(name) or late:
            items.append({
                "source": "assignment",
                "name":   name,
                "due_at_iso": due,
                "text":   strip_html(a.get("description") or ""),
                "submission_types": a.get("submission_types") or [],
                "points": a.get("points_possible"),
            })

    # Announcements — match FINAL_RE in title
    for a in cr.canvas_get(f"courses/{cid}/discussion_topics",
                            {"per_page": 50, "only_announcements": True}):
        if FINAL_RE.search(a.get("title") or ""):
            items.append({
                "source": "announcement",
                "name":   a.get("title", ""),
                "due_at_iso": None,
                "text":   strip_html(a.get("message") or ""),
                "submission_types": [],
                "points": None,
            })

    # Pages — match FINAL_RE in title (best-effort; some courses 404)
    pages = cr.canvas_get(f"courses/{cid}/pages", {"per_page": 100})
    for p in (pages if isinstance(pages, list) else []):
        if FINAL_RE.search(p.get("title") or ""):
            detail = cr.canvas_get(f"courses/{cid}/pages/{p['url']}")
            items.append({
                "source": "page",
                "name":   p.get("title", ""),
                "due_at_iso": None,
                "text":   strip_html((detail.get("body") if isinstance(detail, dict) else "") or ""),
                "submission_types": [],
                "points": None,
            })

    return items


def _fmt_due(iso: str | None) -> str:
    if not iso:
        return "no Canvas due date"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BOSTON)
    return dt.strftime("%A, %B %-d, %Y at %-I:%M %p ET")


# Month name → number for text-based date extraction
_MONTHS = {m: i for i, m in enumerate(
    ["january","february","march","april","may","june",
     "july","august","september","october","november","december"], 1)}

_DATE_RE = re.compile(
    r'(?:closes?|due|exam|held|deadline)[^\n]*?'
    r'(january|february|march|april|may|june|july|august|september|october|november|december)'
    r'\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?',
    re.I
)

def _extract_text_dates(text: str, abbrev: str) -> list[dict]:
    """
    Find the LAST (most restrictive) date mentioned near close/due/deadline keywords.
    Used for finals that have no Canvas due_at (e.g. LME window, IMCM announcement).
    """
    results = []
    for m in _DATE_RE.finditer(text):
        month_name = m.group(1).lower()
        day        = int(m.group(2))
        year       = int(m.group(3)) if m.group(3) else 2026
        month      = _MONTHS.get(month_name)
        if not month:
            continue
        try:
            from datetime import date
            d = date(year, month, day)
            results.append({"date_iso": d.isoformat(), "abbrev": abbrev})
        except ValueError:
            continue
    # Return the latest date found (close of window / exam day)
    return sorted(results, key=lambda x: x["date_iso"])[-1:] if results else []


# ── Claude digest ─────────────────────────────────────────────────────────────

_DIGEST_PROMPT = """\
You are helping an HBS student consolidate final exam logistics from Canvas.

Below is raw information pulled from Canvas for each course. For each course
that has a final exam, project, or equivalent, produce a clean section with:

  **[Full Course Name]**
  Format: <In-person exam / Take-home exam / Project / Presentation / ...>
  Date / Window: <exact date(s) and times in ET>
  Duration: <if applicable>
  Location: <if in-person>
  Submission: <how to submit>
  Key Requirements: <word limit, page limit, group vs individual, etc.>
  Notes: <anything else logistically important — grading weight, materials allowed, etc.>

If a course has no final exam information in the data, say so briefly.
Be concise and factual — this is a logistics reference, not a summary of content.
Do not invent or infer details not present in the source material.
Use "ET" for all times.
"""

def _call_claude(raw_text: str) -> str:
    import anthropic
    env = cr.ENV
    key = env.get("ANTHROPIC_API_KEY") or ""
    client = anthropic.Anthropic(api_key=key)
    model  = ai_config.MODEL

    resp = client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{
            "role": "user",
            "content": _DIGEST_PROMPT + "\n\n---\n\n" + raw_text,
        }]
    )
    return resp.content[0].text


# ── Word doc ──────────────────────────────────────────────────────────────────

def _write_docx(md_text: str, out_path: Path) -> None:
    try:
        from docx import Document
        from docx.shared import Pt, RGBColor
        from docx.enum.text import WD_ALIGN_PARAGRAPH
    except ImportError:
        sys.exit("python-docx not installed")

    doc  = Document()
    # Page margins
    for sec in doc.sections:
        sec.top_margin    = sec.bottom_margin = Pt(54)
        sec.left_margin   = sec.right_margin  = Pt(72)

    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    # Title
    t = doc.add_paragraph()
    t.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = t.add_run("Finals Digest — HBS 26F")
    run.bold      = True
    run.font.size = Pt(16)
    t.paragraph_format.space_after = Pt(4)

    sub = doc.add_paragraph()
    sub.alignment = WD_ALIGN_PARAGRAPH.CENTER
    from datetime import date
    sub.add_run(f"Generated {date.today().strftime('%B %-d, %Y')}").font.size = Pt(10)
    sub.paragraph_format.space_after = Pt(18)

    doc.add_paragraph()  # spacer

    for line in md_text.splitlines():
        stripped = line.strip()
        if not stripped:
            doc.add_paragraph().paragraph_format.space_after = Pt(2)
            continue

        if stripped.startswith("## "):
            p = doc.add_paragraph()
            run = p.add_run(stripped[3:])
            run.bold      = True
            run.font.size = Pt(14)
            run.font.color.rgb = RGBColor(0x02, 0x76, 0x34)  # HBS green
            p.paragraph_format.space_before = Pt(14)
            p.paragraph_format.space_after  = Pt(4)

        elif stripped.startswith("**") and stripped.endswith("**"):
            # Course header line from Claude: **Course Name**
            text = stripped[2:-2]
            p = doc.add_paragraph()
            run = p.add_run(text)
            run.bold      = True
            run.font.size = Pt(13)
            run.font.color.rgb = RGBColor(0x02, 0x76, 0x34)
            p.paragraph_format.space_before = Pt(14)
            p.paragraph_format.space_after  = Pt(3)

        elif re.match(r'^[A-Za-z /]+:', stripped):
            # Field: Value lines (Format:, Date:, etc.)
            key, _, val = stripped.partition(":")
            p = doc.add_paragraph()
            p.paragraph_format.left_indent  = Pt(18)
            p.paragraph_format.space_after  = Pt(2)
            r1 = p.add_run(key + ": ")
            r1.bold = True
            p.add_run(val.strip())

        elif stripped.startswith("• ") or stripped.startswith("- "):
            p = doc.add_paragraph(stripped[2:], style="List Bullet")
            p.paragraph_format.left_indent = Pt(36)
            p.paragraph_format.space_after = Pt(1)

        elif stripped.startswith("---"):
            doc.add_paragraph().paragraph_format.space_after = Pt(4)

        else:
            p = doc.add_paragraph(stripped)
            p.paragraph_format.space_after = Pt(2)

    doc.save(str(out_path))
    print(f"  Saved: {out_path}")


# ── Calendar events ───────────────────────────────────────────────────────────

def _make_event(title: str, date_iso: str, dry_run: bool) -> str:
    dt       = datetime.fromisoformat(date_iso)
    date_str = dt.strftime("%B %-d, %Y")
    safe     = title.replace('"', '\\"')
    script   = f"""
tell application "Calendar"
    tell calendar "{CALENDAR_NAME}"
        set evt_date to date "{date_str}"
        set matching to (every event whose summary = "{safe}" and start date = evt_date)
        if (count of matching) > 0 then
            return "exists"
        end if
        make new event with properties {{summary:"{safe}", start date:evt_date, end date:evt_date, allday event:true}}
        return "created"
    end tell
end tell
"""
    if dry_run:
        return "dry-run"
    result = subprocess.run(["osascript", "-e", script],
                            capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        return f"error: {result.stderr.strip() or result.stdout.strip()}"
    return result.stdout.strip()


def _sync_calendar(deadlines: list[dict], dry_run: bool) -> None:
    print(f"\n{'─'*55}")
    print(f"  FINALS CALENDAR{'  [dry run]' if dry_run else ''}")
    print(f"{'─'*55}")
    created = skipped = 0
    for d in deadlines:
        title    = d["title"]
        date_iso = d["date_iso"]
        outcome  = _make_event(title, date_iso, dry_run)
        if outcome in ("created", "dry-run"):
            print(f"  + {datetime.fromisoformat(date_iso).strftime('%b %-d')}  {title}")
            created += 1
        elif outcome == "exists":
            skipped += 1
        else:
            print(f"  ✗ {title}: {outcome}")
    print(f"\n  Created: {created}  Already existed: {skipped}")
    print(f"{'─'*55}\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true",
                        help="Generate Word doc but skip calendar events")
    args = parser.parse_args()

    print("\n── Finals Digest ─────────────────────────────────────────")

    # ── Step 1: collect raw Canvas data ───────────────────────────────────────
    sections: list[str] = []
    deadlines: list[dict] = []

    for abbrev in ACTIVE_COURSES:
        if abbrev not in _COURSES:
            continue
        cid  = _COURSES[abbrev]["canvas_id"]
        name = COURSE_NAMES.get(abbrev, abbrev)
        print(f"  {abbrev}...", end=" ", flush=True)

        items = _collect(abbrev, cid)
        if not items:
            print("nothing found")
            sections.append(f"COURSE: {name} ({abbrev})\nNo final exam information found on Canvas.\n")
            continue

        print(f"{len(items)} item(s)")

        # Build a raw text block for Claude
        block = [f"COURSE: {name} ({abbrev})"]
        for it in items:
            block.append(f"\n[{it['source'].upper()}] {it['name']}")
            if it["due_at_iso"]:
                block.append(f"Due: {_fmt_due(it['due_at_iso'])}")
            if it["points"]:
                block.append(f"Points: {it['points']}")
            if it["submission_types"]:
                block.append(f"Submission types: {', '.join(it['submission_types'])}")
            if it["text"]:
                block.append(it["text"][:2000])  # cap per item

        sections.append("\n".join(block))

        # Collect calendar deadlines
        has_dated_final = False
        for it in items:
            if it["due_at_iso"] and FINAL_RE.search(it["name"]):
                dt = datetime.fromisoformat(
                    it["due_at_iso"].replace("Z", "+00:00")).astimezone(BOSTON)
                label = f"{it['name']} ({abbrev})"
                deadlines.append({
                    "title":    label,
                    "date_iso": dt.date().isoformat(),
                })
                has_dated_final = True

        # For finals with no Canvas due_at (announcements, self-scheduled exams),
        # extract the deadline date from the description text
        if not has_dated_final:
            for it in items:
                if not FINAL_RE.search(it["name"]) and not FINAL_RE.search(it["text"]):
                    continue
                for d in _extract_text_dates(it["text"], abbrev):
                    label = f"{it['name']} ({abbrev})"
                    deadlines.append({"title": label, "date_iso": d["date_iso"]})
                    break  # one event per course is enough

    # ── Step 2: Claude summarisation ──────────────────────────────────────────
    raw = "\n\n" + ("─" * 60 + "\n\n").join(sections)
    print("\n  Calling Claude to extract logistics...")
    digest_md = _call_claude(raw)

    # ── Step 3: Word doc ──────────────────────────────────────────────────────
    out_path = DEST_ROOT / "Finals Digest.docx"
    _write_docx(digest_md, out_path)

    # ── Step 4: Calendar events ───────────────────────────────────────────────
    if deadlines:
        _sync_calendar(deadlines, dry_run=args.dry_run)
    else:
        print("  No dated deadlines found for calendar.")

    print("── Done ──────────────────────────────────────────────────\n")


if __name__ == "__main__":
    main()
