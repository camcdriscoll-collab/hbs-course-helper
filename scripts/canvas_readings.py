#!/usr/bin/env python3
"""
canvas_readings.py — Download linked readings from Canvas assignment HTML.

Handles three link types found in Canvas assignment descriptions:
  • hbsp.harvard.edu/tu/...  → Playwright download (public links, no login needed)
  • services.hbsp.harvard.edu/.../sclinks/ → Playwright download (coursepack links)
  • External articles/blogs  → Playwright print-to-PDF (headless Chromium)
  • YouTube links            → .txt stub with title + URL
  • instructure.com links    → skip (already handled by canvas_refresh.py)

Files are saved as: YYMMDD Descriptive Title.pdf
  e.g. 260902 Rocky Mountain Condiments.pdf
       260908 Contracts 101.pdf

Standalone debug (show what would be downloaded for a session):
  python3 canvas_readings.py --list 260908 LTV

Download a specific session manually:
  python3 canvas_readings.py 260908 LTV

Rename existing session files to the YYMMDD Title convention:
  python3 canvas_readings.py --rename-all

Called from canvas_refresh.py:
  import canvas_readings
  canvas_readings.sync_reading_links(assignment, session_dir)
"""

import argparse
import asyncio
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import path_config

# ── Constants ─────────────────────────────────────────────────────────────────

_CANVAS_RE = re.compile(r"instructure\.com", re.I)
_HBSP_RE   = re.compile(r"hbsp\.harvard\.edu", re.I)
_YT_RE     = re.compile(r"(youtube\.com|youtu\.be)", re.I)

# Non-reading links to silently skip (people profiles, social media, etc.)
_SKIP_RE = re.compile(
    r"(linkedin\.com|twitter\.com|x\.com|instagram\.com|facebook\.com"
    r"|mailto:|tel:|#)",
    re.I,
)

# ── HTML parsing ──────────────────────────────────────────────────────────────

class _AnchorParser(HTMLParser):
    """Walk Canvas assignment HTML, track <em> headings, collect anchor links."""

    def __init__(self):
        super().__init__()
        self.links: list[dict] = []
        self._in_a = False
        self._href = ""
        self._buf: list[str] = []
        self._in_em = False
        self._em_buf: list[str] = []
        self._optional = False

    def handle_starttag(self, tag: str, attrs):
        d = dict(attrs)
        if tag == "em":
            self._in_em = True
            self._em_buf = []
        elif tag == "a":
            self._in_a = True
            self._href = d.get("href", "").strip()
            self._buf = []

    def handle_endtag(self, tag: str):
        if tag == "em":
            text = "".join(self._em_buf).strip().lower()
            # "Optional Readings:" section → flip the flag
            if "optional" in text:
                self._optional = True
            self._in_em = False
        elif tag == "a" and self._in_a:
            title = " ".join("".join(self._buf).split())
            if self._href and title:
                self.links.append({
                    "href": self._href,
                    "title": title,
                    "optional": self._optional,
                })
            self._in_a = False
            self._href = ""
            self._buf = []

    def handle_data(self, data: str):
        if self._in_a:
            self._buf.append(data)
        if self._in_em:
            self._em_buf.append(data)


def extract_links(html: str) -> list[dict]:
    """
    Parse all reading links from Canvas assignment HTML.
    Returns list of {href, title, optional}.
    Excludes Canvas-hosted files (handled by canvas_refresh.py) and
    non-reading links (LinkedIn, social media, mailto, etc.).
    """
    p = _AnchorParser()
    p.feed(html)
    return [
        l for l in p.links
        if not _CANVAS_RE.search(l["href"])
        and not _SKIP_RE.search(l["href"])
    ]


def classify(href: str) -> str:
    """Returns 'hbsp' | 'youtube' | 'external'."""
    if _HBSP_RE.search(href):
        return "hbsp"
    if _YT_RE.search(href):
        return "youtube"
    return "external"


# ── Naming helpers ────────────────────────────────────────────────────────────

def safe_name(title: str, max_len: int = 120) -> str:
    """Sanitize a title for use as a filename stem."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title)
    s = re.sub(r"-{2,}", "-", s).strip(". -")
    return s[:max_len]


def _clean_title(title: str, max_len: int = 60) -> str:
    """
    Shorten a Canvas link title to a human-readable filename component.
    - Strips trailing case numbers/codes in parens: (319-029), (215001), [224023]
    - Keeps single-letter case designations: (A), (B)
    - Strips HBP/HBR article attribution suffixes
    - Truncates long titles at the EARLIEST natural break (colon, dash, comma)
      so bibliographic citations like "Author, Book Title (Publisher: ...)"
      truncate at the author comma, and case titles like "Company: Subtitle"
      truncate at the colon.
    """
    name = title
    # Strip trailing bracketed codes: [224023]
    name = re.sub(r'\s*\[\w+\]\s*$', '', name).strip()
    # Repeatedly strip trailing parentheticals that contain a digit or are 3+ chars
    # (preserves single-letter case designations like (A), (B))
    while True:
        m = re.sub(r'\s*\((?:[^)]*\d[^)]*|[^)]{3,})\)\s*$', '', name)
        if m == name:
            break
        name = m.strip()
    # Strip HBP/HBR article attribution
    name = re.sub(r',?\s*HBP\b.*$', '', name, flags=re.I).strip()
    name = re.sub(r',?\s*HBR\b.*$', '', name, flags=re.I).strip()
    # Strip trailing punctuation
    name = name.rstrip(',:.-').strip()
    # Truncate at the earliest natural break if still too long
    if len(name) > max_len:
        best = max_len + 1
        for sep in (':', ' — ', ' – ', ' - ', ','):
            pos = name.find(sep)
            if 0 < pos < best:
                best = pos
        if best <= max_len:
            name = name[:best].strip()
        else:
            name = name[:max_len].rstrip()
        # Clean up any dangling open parenthesis from the truncation point
        name = re.sub(r'\s*\([^)]*$', '', name).strip()
    return name


def _session_filename(date_str: str, title: str) -> str:
    """Canonical filename stem for a reading: 'YYMMDD Clean Title'."""
    return f"{date_str} {safe_name(_clean_title(title))}"


def _file_exists(session_dir: Path, stem: str) -> bool:
    """True if any file in session_dir has this exact stem (case-insensitive)."""
    stem_l = stem.lower()
    return any(f.stem.lower() == stem_l for f in session_dir.iterdir() if f.is_file())


# ── Playwright downloaders ────────────────────────────────────────────────────

_MAGIC: list[tuple[bytes, str]] = [
    (b"%PDF",           ".pdf"),
    (b"PK\x03\x04",    ".docx"),  # ZIP-based: DOCX/XLSX/PPTX
    (b"\xd0\xcf\x11\xe0", ".doc"),  # OLE: old DOC/XLS/PPT
]

def _fix_extension(path: Path) -> Path:
    """
    Check the file's magic bytes. If the extension doesn't match the actual
    format, rename to the correct extension and return the new path.
    """
    try:
        magic = path.read_bytes()[:4]
    except Exception:
        return path
    for sig, ext in _MAGIC:
        if magic.startswith(sig):
            if path.suffix.lower() != ext:
                new = path.with_suffix(ext)
                path.rename(new)
                return new
            return path
    return path


async def _fetch_hbsp(ctx, href: str, title: str, session_dir: Path,
                      date_str: str) -> bool:
    """
    Visit an HBSP link and catch the auto-triggered PDF download.
    Works for both /tu/ public links and /api/courses/.../sclinks/ coursepack links.
    Files are saved as: YYMMDD Clean Title.pdf
    Returns True if a new file was saved.
    """
    fname = f"{_session_filename(date_str, title)}.pdf"
    out   = session_dir / fname

    page = await ctx.new_page()
    try:
        # Catch the download event. goto() often raises "Download is starting"
        # when the URL immediately triggers a download — that's expected and OK.
        # The inner try/except is scoped to goto() only so dl_info.value is
        # always awaited even when goto throws.
        try:
            async with page.expect_download(timeout=25_000) as dl_info:
                try:
                    await page.goto(href, wait_until="commit", timeout=30_000)
                except Exception:
                    pass  # "Download is starting" — expected, download still captured
            dl = await dl_info.value
            if out.exists():
                await dl.cancel()
                return False
            await dl.save_as(out)
            # Verify actual file type and rename if mismatched
            out = _fix_extension(out)
            print(f"    ↓ [hbsp] {out.name}")
            return True
        except Exception:
            pass  # download not triggered — fall through to print-to-PDF fallback

        # Fallback: print whatever page loaded to PDF
        if _file_exists(session_dir, _session_filename(date_str, title)):
            return False
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        pdf_bytes = await page.pdf(format="A4", print_background=True)
        # Reject obviously empty/error renders (login walls, blank pages)
        if len(pdf_bytes) < 5_000:
            print(f"    ✗ [hbsp] {title}: got {len(pdf_bytes)}B — likely auth required, skipping")
            return False
        out.write_bytes(pdf_bytes)
        print(f"    ↓ [hbsp→pdf] {out.name}")
        return True

    except Exception as e:
        print(f"    ✗ {title}: {e}")
        return False
    finally:
        await page.close()


async def _fetch_article(ctx, href: str, title: str, session_dir: Path,
                         date_str: str) -> bool:
    """
    Visit an external URL and print to PDF as: YYMMDD Clean Title.pdf
    Returns True if a new file was saved.
    """
    stem = _session_filename(date_str, title)
    if _file_exists(session_dir, stem):
        return False

    page = await ctx.new_page()
    try:
        try:
            await page.goto(href, wait_until="networkidle", timeout=30_000)
        except Exception:
            # Navigation timeout or error — proceed with whatever loaded
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5_000)
            except Exception:
                pass

        pdf_bytes = await page.pdf(
            format="A4",
            print_background=False,
            margin={"top": "1cm", "bottom": "1cm", "left": "1cm", "right": "1cm"},
        )
        out = session_dir / f"{stem}.pdf"
        out.write_bytes(pdf_bytes)
        print(f"    ↓ [article] {out.name}")
        return True

    except Exception as e:
        print(f"    ✗ {title}: {e}")
        return False
    finally:
        await page.close()


# ── Main async runner ─────────────────────────────────────────────────────────

async def _run(links: list[dict], session_dir: Path, date_str: str) -> int:
    """Async: download all reading links. Returns count of newly saved files."""
    from playwright.async_api import async_playwright

    count = 0

    # YouTube stubs — no browser needed
    for link in links:
        if classify(link["href"]) == "youtube":
            stem = f"{date_str} {safe_name(link['title'])} (YouTube)"
            out = session_dir / f"{stem}.txt"
            if not out.exists():
                out.write_text(f"{link['title']}\n{link['href']}\n")
                print(f"    ↓ [youtube] {out.name}")
                count += 1

    hbsp_links = [l for l in links if classify(l["href"]) == "hbsp"]
    ext_links  = [l for l in links if classify(l["href"]) == "external"]

    if not hbsp_links and not ext_links:
        return count

    # One browser handles all downloads.
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(accept_downloads=True)

        for link in hbsp_links:
            if _file_exists(session_dir, _session_filename(date_str, link["title"])):
                continue
            ok = await _fetch_hbsp(ctx, link["href"], link["title"], session_dir, date_str)
            if ok:
                count += 1

        for link in ext_links:
            ok = await _fetch_article(ctx, link["href"], link["title"], session_dir, date_str)
            if ok:
                count += 1

        await ctx.close()
        await browser.close()

    return count


# ── Public API (called from canvas_refresh.py) ────────────────────────────────

def sync_reading_links(assignment: dict, session_dir: Path) -> int:
    """
    Parse assignment HTML and download all linked readings into session_dir.
    Files are named: YYMMDD Clean Title.pdf
    Called from canvas_refresh.py. Returns count of newly saved files.
    """
    html = assignment.get("description") or ""
    if not html:
        return 0
    links = extract_links(html)
    actionable = [l for l in links if classify(l["href"]) in ("hbsp", "youtube", "external")]
    if not actionable:
        return 0

    # Extract date from session folder name: "260902 LME" → "260902"
    date_str = session_dir.name[:6]

    session_dir.mkdir(parents=True, exist_ok=True)
    try:
        return asyncio.run(_run(actionable, session_dir, date_str))
    except Exception as e:
        print(f"    ✗ Reading sync error: {e}")
        return 0


# ── Rename existing files ─────────────────────────────────────────────────────

# Pattern for HBSP-generated case number filenames: 820008-PDF-ENG, N0602D-PDF-ENG
_HBSP_STEM_RE = re.compile(r'^[A-Z0-9]+-PDF-ENG$', re.I)


def _old_hbsp_stems(href: str, title: str) -> list[str]:
    """
    Return possible old-style filename stems for an HBSP link before the rename.
    Checks: sclink item ID in URL, case number extracted from title, title itself.
    """
    stems = []
    # LME sclinks: extract item ID from URL → "319029-PDF-ENG"
    m = re.search(r'/items/([A-Z0-9]+-PDF-ENG)/sclinks/', href, re.I)
    if m:
        stems.append(m.group(1))
    # Case number in title (CFO): "(215001)" → "215001-PDF-ENG"
    for pat in [r'\((\d{5,6})\)', r'\((\d{3})-(\d{3})\)']:
        for hit in re.finditer(pat, title):
            code = "".join(hit.groups())
            stems.append(f"{code}-PDF-ENG")
    # Title-as-filename (supplements without case numbers)
    stems.append(safe_name(title))
    return stems


def rename_session_files(assignment: dict, date_str: str, session_dir: Path) -> int:
    """
    Rename existing reading files in session_dir to the 'YYMMDD Title' convention.

    Matching strategy:
    - HBSP files: matched by case number in URL/title or by title stem
    - Articles/YouTube: matched by safe_name(link_title)
    - Unmatched HBSP case-number files: paired 1:1 with remaining HBSP links by order
    - Canvas-hosted files (no link): date prefix added only

    Returns count of files renamed.
    """
    if not session_dir.exists():
        return 0

    html    = assignment.get("description") or ""
    links   = extract_links(html)
    renamed = 0
    handled: set[Path] = set()  # files already processed

    # ── Pass 1: match each link to its current file ────────────────────────────
    unmatched_hbsp_links: list[dict] = []

    for link in links:
        href  = link["href"]
        title = link["title"]
        kind  = classify(href)

        if kind not in ("hbsp", "external", "youtube"):
            continue

        new_stem = _session_filename(date_str, title)

        # Build candidate old stems
        if kind == "hbsp":
            old_stems = _old_hbsp_stems(href, title)
        elif kind == "youtube":
            old_stems = [f"{safe_name(title)} (YouTube)"]
            new_stem  = f"{date_str} {safe_name(title)} (YouTube)"
        else:
            old_stems = [safe_name(title)]

        matched = False
        for old_stem in old_stems:
            for f in session_dir.iterdir():
                if not f.is_file() or f in handled:
                    continue
                if f.stem.lower() == old_stem.lower():
                    if f.stem == new_stem:
                        handled.add(f)
                        matched = True
                        break
                    new_path = session_dir / f"{new_stem}{f.suffix}"
                    if not new_path.exists():
                        f.rename(new_path)
                        print(f"    ↩ {f.name}  →  {new_path.name}")
                        renamed += 1
                    handled.add(new_path if new_path.exists() else f)
                    matched = True
                    break
            if matched:
                break
        else:
            if kind == "hbsp":
                unmatched_hbsp_links.append(link)

    # ── Pass 2: pair remaining HBSP case-number files with unmatched HBSP links ─
    # (LTV /tu/ links — case number not in title or URL, only in suggested filename)
    unmatched_hbsp_files = sorted(
        f for f in session_dir.iterdir()
        if f.is_file()
        and f not in handled
        and _HBSP_STEM_RE.match(f.stem)
    )
    if len(unmatched_hbsp_files) == len(unmatched_hbsp_links):
        for f, link in zip(unmatched_hbsp_files, unmatched_hbsp_links):
            new_stem = _session_filename(date_str, link["title"])
            new_path = session_dir / f"{new_stem}{f.suffix}"
            if not new_path.exists():
                f.rename(new_path)
                print(f"    ↩ {f.name}  →  {new_path.name}")
                renamed += 1
            handled.add(new_path if new_path.exists() else f)
    elif unmatched_hbsp_files:
        print(f"    ⚠ {len(unmatched_hbsp_files)} HBSP file(s) couldn't be matched to a link title — skipping rename")
        handled.update(unmatched_hbsp_files)

    # ── Pass 3: add date prefix to remaining files (Canvas-hosted) ────────────
    skip_suffixes = {'.docx', '.m4a', '.json', '.md'}
    skip_patterns = (' Notes', ' Podcast', ' Overview')
    for f in session_dir.iterdir():
        if not f.is_file() or f in handled:
            continue
        if f.name.startswith('.') or f.suffix.lower() in skip_suffixes:
            continue
        if any(p in f.name for p in skip_patterns):
            continue
        if f.stem.startswith(date_str):
            continue
        new_path = session_dir / f"{date_str} {f.name}"
        if not new_path.exists():
            f.rename(new_path)
            print(f"    ↩ {f.name}  →  {new_path.name}")
            renamed += 1

    return renamed


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true",
                        help="List links that would be downloaded (no downloads)")
    parser.add_argument("--rename-all", action="store_true",
                        help="Rename all existing session files to YYMMDD Title convention")
    parser.add_argument("date", nargs="?", help="Session date YYMMDD")
    parser.add_argument("course", nargs="*", help="Course abbreviation (e.g. LTV)")
    args = parser.parse_args()

    _paths   = path_config.resolve()
    _courses = _paths["courses"]

    # ── --rename-all: scan every session folder across all courses ─────────────
    if args.rename_all:
        import canvas_refresh as _cr
        total = 0
        for abbrev, info in _courses.items():
            course_id     = info["canvas_id"]
            course_folder = info.get("folder_path") or _paths["coursework_root"] / abbrev
            if not course_folder:
                continue
            assignments = _cr.canvas_get(f"courses/{course_id}/assignments",
                                         {"per_page": 100})
            for a in assignments:
                if not a.get("due_at"):
                    continue
                date_str    = _cr.yymmdd(_cr.boston_date(a["due_at"]))
                session_dir = course_folder / f"{date_str} {abbrev}"
                if not session_dir.exists():
                    continue
                n = rename_session_files(a, date_str, session_dir)
                if n:
                    print(f"  [{date_str}] {abbrev}: {n} file(s) renamed")
                    total += n
        print(f"\n✅ Done — {total} file(s) renamed across all sessions.")
        return

    # ── Normal mode: download (or list) for a specific session ────────────────
    if not args.date or not args.course:
        parser.print_help()
        sys.exit(1)

    date_str = args.date
    abbrev   = " ".join(args.course).upper()

    if abbrev not in _courses:
        sys.exit(f"Unknown course '{abbrev}'. Known: {', '.join(_courses)}")

    course_id = _courses[abbrev]["canvas_id"]

    import canvas_refresh as _cr
    assignments = _cr.canvas_get(f"courses/{course_id}/assignments", {"per_page": 100})
    assignment = next(
        (a for a in assignments
         if a.get("due_at") and _cr.yymmdd(_cr.boston_date(a["due_at"])) == date_str),
        None
    )
    if assignment is None:
        sys.exit(f"No assignment found for {date_str} {abbrev}")

    html  = assignment.get("description") or ""
    links = extract_links(html)

    if args.list:
        print(f"\nLinks in {date_str} {abbrev}: {assignment['name']}")
        for l in links:
            opt  = " [optional]" if l["optional"] else ""
            kind = classify(l["href"])
            new  = _session_filename(date_str, l["title"])
            print(f"  [{kind}]{opt} {l['title']}")
            print(f"         → {new}.pdf")
            print(f"         {l['href']}")
        return

    course_folder = _courses[abbrev].get("folder_path") or _paths["coursework_root"] / abbrev
    session_dir   = course_folder / f"{date_str} {abbrev}"

    print(f"\nReading sync: {abbrev} {date_str}")
    n = sync_reading_links(assignment, session_dir)
    print(f"\n✅ Done — {n} new file(s) saved.")


if __name__ == "__main__":
    main()
