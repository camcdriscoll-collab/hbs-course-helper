#!/usr/bin/env python3
"""
canvas_readings.py — Download linked readings from Canvas assignment HTML.

Handles three link types found in Canvas assignment descriptions:
  • hbsp.harvard.edu/tu/...  → Playwright download (public links, no login needed)
  • External articles/blogs  → Playwright print-to-PDF (headless Chromium)
  • YouTube links            → .txt stub with title + URL
  • instructure.com links    → skip (already handled by canvas_refresh.py)

No login setup required — HBSP /tu/ links are public coursepack links.

Standalone debug (show what would be downloaded for a session):
  python3 canvas_readings.py --list 260908 LTV

Download a specific session manually:
  python3 canvas_readings.py 260908 LTV

Called from canvas_refresh.py:
  import canvas_readings
  canvas_readings.sync_reading_links(assignment, session_dir)
"""

import argparse
import asyncio
import json
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


def safe_name(title: str, max_len: int = 120) -> str:
    """Sanitize a link title for use as a filename stem."""
    s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", title)
    s = re.sub(r"-{2,}", "-", s).strip(". -")
    return s[:max_len]


def _file_exists(session_dir: Path, stem: str) -> bool:
    """True if any file in session_dir has this exact stem (case-insensitive)."""
    stem_l = stem.lower()
    return any(f.stem.lower() == stem_l for f in session_dir.iterdir() if f.is_file())


# ── Playwright downloaders ────────────────────────────────────────────────────

async def _fetch_hbsp(ctx, href: str, title: str, session_dir: Path) -> bool:
    """
    Visit an HBSP /tu/ link using stored auth.
    The link auto-downloads a PDF when logged in; falls back to page.pdf() if not.
    Returns True if a new file was saved.
    """
    page = await ctx.new_page()
    try:
        # Try: catch the download that fires on an authenticated /tu/ visit
        try:
            async with page.expect_download(timeout=25_000) as dl_info:
                await page.goto(href, wait_until="commit", timeout=30_000)
            dl = await dl_info.value
            suggested = dl.suggested_filename or ""
            # Use suggested name if it's a PDF, otherwise use title
            if suggested.lower().endswith(".pdf"):
                fname = suggested
            else:
                fname = f"{safe_name(title)}.pdf"
            out = session_dir / fname
            if out.exists():
                await dl.cancel()
                return False
            await dl.save_as(out)
            print(f"    ↓ [hbsp] {out.name}")
            return True
        except Exception:
            pass  # no download triggered — fall through to print-to-PDF

        # Fallback: print whatever page loaded
        stem = safe_name(title)
        if _file_exists(session_dir, stem):
            return False
        try:
            await page.wait_for_load_state("networkidle", timeout=15_000)
        except Exception:
            pass
        pdf_bytes = await page.pdf(format="A4", print_background=True)
        out = session_dir / f"{stem}.pdf"
        out.write_bytes(pdf_bytes)
        print(f"    ↓ [hbsp→pdf] {out.name}")
        return True

    except Exception as e:
        print(f"    ✗ {title}: {e}")
        return False
    finally:
        await page.close()


async def _fetch_article(ctx, href: str, title: str, session_dir: Path) -> bool:
    """
    Visit an external URL and print to PDF.
    Returns True if a new file was saved.
    """
    stem = safe_name(title)
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

async def _run(links: list[dict], session_dir: Path) -> int:
    """Async: download all reading links. Returns count of newly saved files."""
    from playwright.async_api import async_playwright

    count = 0

    # YouTube stubs — no browser needed
    for link in links:
        if classify(link["href"]) == "youtube":
            stem = f"{safe_name(link['title'])} (YouTube)"
            out = session_dir / f"{stem}.txt"
            if not out.exists():
                out.write_text(f"{link['title']}\n{link['href']}\n")
                print(f"    ↓ [youtube] {out.name}")
                count += 1

    hbsp_links = [l for l in links if classify(l["href"]) == "hbsp"]
    ext_links  = [l for l in links if classify(l["href"]) == "external"]

    if not hbsp_links and not ext_links:
        return count

    # HBSP /tu/ links are public coursepack links — no authentication needed.
    # One browser handles everything.
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context(accept_downloads=True)

        for link in hbsp_links:
            if _file_exists(session_dir, safe_name(link["title"])):
                continue
            ok = await _fetch_hbsp(ctx, link["href"], link["title"], session_dir)
            if ok:
                count += 1

        for link in ext_links:
            ok = await _fetch_article(ctx, link["href"], link["title"], session_dir)
            if ok:
                count += 1

        await ctx.close()
        await browser.close()

    return count


# ── Public API (called from canvas_refresh.py) ────────────────────────────────

def sync_reading_links(assignment: dict, session_dir: Path) -> int:
    """
    Parse assignment HTML and download all linked readings into session_dir.
    Called from canvas_refresh.py. Returns count of newly saved files.
    """
    html = assignment.get("description") or ""
    if not html:
        return 0
    links = extract_links(html)
    actionable = [l for l in links if classify(l["href"]) in ("hbsp", "youtube", "external")]
    if not actionable:
        return 0

    session_dir.mkdir(parents=True, exist_ok=True)
    try:
        return asyncio.run(_run(actionable, session_dir))
    except Exception as e:
        print(f"    ✗ Reading sync error: {e}")
        return 0


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--list", action="store_true",
                        help="List links that would be downloaded (no downloads)")
    parser.add_argument("date", nargs="?", help="Session date YYMMDD")
    parser.add_argument("course", nargs="*", help="Course abbreviation (e.g. LTV)")
    args = parser.parse_args()

    if not args.date or not args.course:
        parser.print_help()
        sys.exit(1)

    date_str = args.date
    abbrev   = " ".join(args.course).upper()

    _paths = path_config.resolve()
    _courses = _paths["courses"]
    if abbrev not in _courses:
        sys.exit(f"Unknown course '{abbrev}'. Known: {', '.join(_courses)}")

    course_id = _courses[abbrev]["canvas_id"]

    # Import canvas_refresh for canvas_get
    import canvas_refresh as _cr
    assignments = _cr.canvas_get(f"courses/{course_id}/assignments", {"per_page": 100})
    assignment = next(
        (a for a in assignments
         if a.get("due_at") and _cr.yymmdd(_cr.boston_date(a["due_at"])) == date_str),
        None
    )
    if assignment is None:
        sys.exit(f"No assignment found for {date_str} {abbrev}")

    html = assignment.get("description") or ""
    links = extract_links(html)

    if args.list:
        print(f"\nLinks in {date_str} {abbrev}: {assignment['name']}")
        for l in links:
            opt = " [optional]" if l["optional"] else ""
            kind = classify(l["href"])
            print(f"  [{kind}]{opt} {l['title']}")
            print(f"         {l['href']}")
        return

    course_folder = _courses[abbrev].get("folder_path") or _paths["coursework_root"] / abbrev
    session_dir   = course_folder / f"{date_str} {abbrev}"

    print(f"\nReading sync: {abbrev} {date_str}")
    n = sync_reading_links(assignment, session_dir)
    print(f"\n✅ Done — {n} new file(s) saved.")


if __name__ == "__main__":
    main()
