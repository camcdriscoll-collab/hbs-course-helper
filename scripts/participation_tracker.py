#!/usr/bin/env python3
"""
participation_tracker.py — HBS participation tracker spreadsheet.

Creates/refreshes: ~/Desktop/Coursework/Participation Tracker.xlsx
  - Single worksheet ("Participation")
  - CATS | CFO | LME | LTV side by side — 3 columns each: Day | Case Title | Rating
  - Row 1: merged course header with live rate formula (spoke / total entered)
  - Row 2: column labels
  - Row 3+: one row per session, sorted by date

Rating values: ok, good, great, x (didn't speak), or blank (not yet)
Denominator = count of non-blank rating cells (entry-based, not date-based)
Numerator   = count of ok + good + great ratings

On refresh (called from canvas_refresh.py --weekly):
  - Case titles and dates are updated from Canvas
  - User-entered ratings are preserved (keyed by course + session date)

Run standalone:
  ~/repos/hbs-course-helper/.venv/bin/python3 participation_tracker.py
"""

import json
import os
import re
import sys
from datetime import datetime, timezone, timedelta, date as _date
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, Request, build_opener

# ── Path setup ────────────────────────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent))
import path_config

_paths       = path_config.resolve()
DEST_ROOT    = _paths["coursework_root"]
ENV_FILE     = _paths["env_file"] or Path("/dev/null")
_COURSES     = _paths["courses"]
COURSE_NAMES = path_config.COURSE_NAMES

COURSES      = {a: d["canvas_id"] for a, d in _COURSES.items() if d["folder_path"]}
CANVAS_BASE  = "https://hbs.instructure.com/api/v1"
BOSTON       = timezone(timedelta(hours=-4))

COURSE_ORDER = ["CATS", "CFO", "LME", "LTV"]
OUTPUT_FILE  = DEST_ROOT / "Participation Tracker.xlsx"

# Columns per course group: Day(offset 0), Case Title(offset 1), Rating(offset 2)
COLS_PER_COURSE = 3

# ── Canvas API ────────────────────────────────────────────────────────────────


def _load_token() -> str:
    token = os.getenv("CANVAS_API_TOKEN", "")
    if token:
        return token
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("CANVAS_API_TOKEN=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    sys.exit(f"No CANVAS_API_TOKEN found. Check {ENV_FILE}")


class _StripAuth(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req:
            from urllib.parse import urlparse
            if urlparse(newurl).netloc != urlparse(req.full_url).netloc:
                new_req.remove_header("Authorization")
        return new_req


_opener = build_opener(_StripAuth())


def canvas_get(path: str, params: dict | None = None) -> list | dict:
    token = _load_token()
    url = f"{CANVAS_BASE}/{path.lstrip('/')}"
    if params:
        url += "?" + urlencode(params)
    results = []
    while url:
        req = Request(url, headers={"Authorization": f"Bearer {token}"})
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
        link = resp.headers.get("Link", "")
        url = None
        for part in link.split(","):
            if 'rel="next"' in part:
                m = re.search(r"<(.+?)>", part)
                if m:
                    url = m.group(1)
    return results


# ── Helpers ───────────────────────────────────────────────────────────────────


def boston_date(iso: str) -> datetime:
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone(BOSTON)


def yymmdd(dt: datetime) -> str:
    return dt.strftime("%y%m%d")


def col_letter(n: int) -> str:
    """1-indexed column number → Excel column letter (A, B, …, Z, AA, …)."""
    result = ""
    while n > 0:
        n, rem = divmod(n - 1, 26)
        result = chr(65 + rem) + result
    return result


def extract_case_title(name: str) -> str:
    """
    Strip 'COURSE | Class N: ' boilerplate, return the case/topic title.
    e.g. "CFO | Class 3: The DCF Method" → "The DCF Method"
    """
    m = re.search(r"class\s+\d+\s*[:\-]\s*(.*)", name, re.IGNORECASE)
    if m and m.group(1).strip():
        return m.group(1).strip()
    if "|" in name:
        return name.split("|")[-1].strip()
    return name.strip()


# ── Session data ──────────────────────────────────────────────────────────────


def get_all_sessions() -> dict[str, list[dict]]:
    """
    Fetch all Canvas assignments with due dates for each active course.
    Returns {abbrev: [assignment_dict, ...]}, sorted chronologically.
    """
    result: dict[str, list[dict]] = {}
    for abbrev in COURSE_ORDER:
        course_id = COURSES.get(abbrev)
        if not course_id:
            result[abbrev] = []
            continue
        assignments = canvas_get(f"courses/{course_id}/assignments", {"per_page": 100})
        sessions = [a for a in assignments if a.get("due_at")]
        sessions.sort(key=lambda a: a["due_at"])
        result[abbrev] = sessions
    return result


# ── Preserve existing ratings ─────────────────────────────────────────────────


def read_existing_ratings(path: Path) -> dict[str, dict[str, str]]:
    """
    Read user-entered ratings from an existing spreadsheet.
    Returns {abbrev: {yymmdd_key: rating_string}}.
    Keyed by date so ratings survive case-title edits on Canvas.
    """
    ratings: dict[str, dict[str, str]] = {a: {} for a in COURSE_ORDER}
    try:
        import openpyxl
    except ImportError:
        return ratings

    try:
        wb = openpyxl.load_workbook(path, data_only=True)
        ws = wb.active
        for i, abbrev in enumerate(COURSE_ORDER):
            day_col    = i * COLS_PER_COURSE + 1      # 1-indexed
            rating_col = i * COLS_PER_COURSE + 3

            for row in ws.iter_rows(min_row=3):
                if len(row) < rating_col:
                    continue
                day_val    = row[day_col - 1].value
                rating_val = row[rating_col - 1].value

                if day_val is None:
                    continue
                # openpyxl returns Excel dates as Python datetime/date objects
                if isinstance(day_val, datetime):
                    day_val = day_val.date()
                if not isinstance(day_val, _date):
                    continue

                key    = day_val.strftime("%y%m%d")
                rating = str(rating_val or "").strip().lower()
                if rating:
                    ratings[abbrev][key] = rating
    except Exception as e:
        print(f"  Warning: could not read existing ratings ({e}) — starting fresh")

    return ratings


# ── Spreadsheet builder ───────────────────────────────────────────────────────


def build_tracker():
    try:
        import openpyxl
        from openpyxl.styles import Font, Alignment, PatternFill, Border, Side
        from openpyxl.formatting.rule import CellIsRule
        from openpyxl.worksheet.datavalidation import DataValidation
    except ImportError:
        sys.exit(
            "openpyxl not installed.\n"
            "Run: ~/repos/hbs-course-helper/.venv/bin/python3 -m pip install openpyxl"
        )

    # ── Preserve existing user ratings ────────────────────────────────────────
    existing: dict[str, dict[str, str]] = {a: {} for a in COURSE_ORDER}
    if OUTPUT_FILE.exists():
        existing = read_existing_ratings(OUTPUT_FILE)
        total = sum(len(v) for v in existing.values())
        if total:
            print(f"  Preserved {total} existing rating(s).")

    # ── Fetch Canvas sessions ─────────────────────────────────────────────────
    print("  Fetching sessions from Canvas...")
    sessions = get_all_sessions()
    for abbrev in COURSE_ORDER:
        print(f"    {abbrev}: {len(sessions.get(abbrev, []))} session(s)")

    max_sessions = max((len(v) for v in sessions.values()), default=0)
    # Range rows for formulas — add buffer so formulas survive new sessions
    formula_end = max(max_sessions + 3, 60) + 2   # row number (data starts at row 3)

    # ── Build workbook ────────────────────────────────────────────────────────
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Participation"

    # ── Styles ────────────────────────────────────────────────────────────────
    NAVY    = "1F4E79"
    BLUE    = "2E75B6"
    WHITE   = "FFFFFF"
    EVEN_BG = "EBF3FB"    # very light blue for alternating rows

    header_fill  = PatternFill(fill_type="solid", fgColor=NAVY)
    subhead_fill = PatternFill(fill_type="solid", fgColor=BLUE)
    even_fill    = PatternFill(fill_type="solid", fgColor=EVEN_BG)

    header_font  = Font(bold=True, color=WHITE, size=12)
    subhead_font = Font(bold=True, color=WHITE, size=10)
    center       = Alignment(horizontal="center", vertical="center")
    wrap_top     = Alignment(wrap_text=True, vertical="top")

    sep_right = Border(right=Side(style="medium", color="7F7F7F"))

    # ── Column widths ─────────────────────────────────────────────────────────
    # Day=11, Case Title=42, Rating=11 — repeated for each course
    widths = [11, 42, 11]
    for i in range(len(COURSE_ORDER)):
        for j, w in enumerate(widths):
            ws.column_dimensions[col_letter(i * COLS_PER_COURSE + j + 1)].width = w

    # ── Row 1: Merged course headers with live participation rate ──────────────
    ws.row_dimensions[1].height = 26

    for i, abbrev in enumerate(COURSE_ORDER):
        sc = i * COLS_PER_COURSE + 1           # start column (1-indexed)
        ec = sc + COLS_PER_COURSE - 1          # end column
        rating_c = col_letter(ec)              # e.g. "C", "F", "I", "L"

        full_name = COURSE_NAMES.get(abbrev, abbrev)

        # Numerator: count of ok + good + great
        num_f = (
            f"SUMPRODUCT(({rating_c}3:{rating_c}{formula_end}=\"ok\")"
            f"+({rating_c}3:{rating_c}{formula_end}=\"good\")"
            f"+({rating_c}3:{rating_c}{formula_end}=\"great\"))"
        )
        # Denominator: count of non-blank rating cells (entry-based)
        den_f = f"SUMPRODUCT(({rating_c}3:{rating_c}{formula_end}<>\"\"))"

        formula = (
            f'="{full_name}  "&TEXT({num_f},"0")&"/"&TEXT({den_f},"0")'
        )

        ws.merge_cells(start_row=1, start_column=sc, end_row=1, end_column=ec)
        cell = ws.cell(row=1, column=sc)
        cell.value     = formula
        cell.font      = header_font
        cell.fill      = header_fill
        cell.alignment = center

    # ── Row 2: Column labels ──────────────────────────────────────────────────
    ws.row_dimensions[2].height = 16

    for i in range(len(COURSE_ORDER)):
        sc = i * COLS_PER_COURSE + 1
        for j, label in enumerate(["Day", "Case Title", "Rating"]):
            cell = ws.cell(row=2, column=sc + j, value=label)
            cell.font      = subhead_font
            cell.fill      = subhead_fill
            cell.alignment = center

    # ── Rows 3+: Session data ─────────────────────────────────────────────────
    for i, abbrev in enumerate(COURSE_ORDER):
        sc              = i * COLS_PER_COURSE + 1
        rating_col_idx  = sc + 2
        course_sessions = sessions.get(abbrev, [])
        existing_abbrev = existing.get(abbrev, {})

        for row_offset, a in enumerate(course_sessions):
            row = 3 + row_offset
            dt       = boston_date(a["due_at"])
            date_val = dt.date()
            date_key = yymmdd(dt)
            title    = extract_case_title(a.get("name", ""))
            rating   = existing_abbrev.get(date_key, "")

            is_even = (row_offset % 2 == 1)
            bg_fill = even_fill if is_even else None

            # Day
            day_cell = ws.cell(row=row, column=sc, value=date_val)
            day_cell.number_format = "MMM D"
            day_cell.alignment     = center
            if bg_fill:
                day_cell.fill = bg_fill

            # Case title
            title_cell = ws.cell(row=row, column=sc + 1, value=title)
            title_cell.alignment = wrap_top
            if bg_fill:
                title_cell.fill = bg_fill

            # Rating (user-filled)
            rating_cell = ws.cell(row=row, column=rating_col_idx, value=rating)
            rating_cell.alignment = center
            if bg_fill:
                rating_cell.fill = bg_fill

        # Right-edge separator for all course groups except the last
        if i < len(COURSE_ORDER) - 1:
            last_col = sc + COLS_PER_COURSE - 1
            for row in range(1, 3 + max_sessions + 1):
                cell = ws.cell(row=row, column=last_col)
                # Preserve existing border, just add right side
                existing_border = cell.border
                cell.border = Border(
                    left=existing_border.left,
                    top=existing_border.top,
                    bottom=existing_border.bottom,
                    right=Side(style="medium", color="7F7F7F"),
                )

        # Dropdown validation for Rating column
        rating_col_letter = col_letter(rating_col_idx)
        dv_end = 3 + max(len(course_sessions), 40)
        dv = DataValidation(
            type="list",
            formula1='"ok,good,great,x"',
            allow_blank=True,
            showDropDown=False,
            error="Enter ok, good, great, or x",
            errorTitle="Invalid entry",
            prompt="ok = brief comment\ngood = solid contribution\ngreat = strong insight\nx = didn't speak",
            promptTitle="Participation",
        )
        ws.add_data_validation(dv)
        dv.sqref = f"{rating_col_letter}3:{rating_col_letter}{dv_end}"

    # ── Conditional formatting for Rating cells ───────────────────────────────
    # great = green, good = light green, ok = yellow, x = light gray
    rating_styles = [
        ("great", "C6EFCE", "375623", True),
        ("good",  "E2EFDA", "375623", False),
        ("ok",    "FFEB9C", "9C5700", False),
        ("x",     "F2F2F2", "7F7F7F", False),
    ]

    for i in range(len(COURSE_ORDER)):
        sc         = i * COLS_PER_COURSE + 1
        rating_c   = col_letter(sc + 2)
        apply_rng  = f"{rating_c}3:{rating_c}{formula_end}"

        for value, bg, fg, bold in rating_styles:
            fill = PatternFill(fill_type="solid", fgColor=bg)
            font = Font(color=fg, bold=bold)
            rule = CellIsRule(operator="equal", formula=[f'"{value}"'],
                              fill=fill, font=font)
            ws.conditional_formatting.add(apply_rng, rule)

    # ── Freeze top 2 header rows ──────────────────────────────────────────────
    ws.freeze_panes = "A3"

    # ── Save ──────────────────────────────────────────────────────────────────
    try:
        wb.save(OUTPUT_FILE)
    except PermissionError:
        sys.exit(
            f"\nCould not save — is {OUTPUT_FILE.name} open in Excel? "
            "Close it and try again."
        )
    print(f"  Saved: {OUTPUT_FILE}")


# ── Public entry point (called from canvas_refresh.py) ───────────────────────


def refresh():
    """Refresh the participation tracker. Called from canvas_refresh.py --weekly."""
    build_tracker()


# ── Standalone ───────────────────────────────────────────────────────────────


def main():
    print("Building HBS participation tracker...")
    build_tracker()
    print("Done.")


if __name__ == "__main__":
    main()
