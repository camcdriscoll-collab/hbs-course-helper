# Canvas Course Helper

Automated Canvas file sync, AI-generated case prep notes, reading downloads, weekly planning, calendar integration, and NotebookLM podcast generation for MBA coursework.

Works with any Canvas LMS instance (Harvard Business School, Stanford GSB, Wharton, etc.).

---

## What it does

| Script | Purpose |
|--------|---------|
| `canvas_refresh.py --daily` | Sync files + download readings + regenerate stale notes for sessions in the next 2 days. Runs automatically at 5pm via launchd. |
| `canvas_refresh.py --weekly` | Full 6-week sync, reading downloads, notes for 2-week window, weekly overview doc, calendar sync, participation tracker refresh. Runs automatically Sunday 8am via launchd. |
| `canvas_readings.py YYMMDD COURSE` | Download all linked readings for one session (HBSP cases, articles, YouTube stubs). |
| `canvas_organize.py` | Route files to correct folders (slides to `Slides/`, etc.) and move duplicates to Trash. Runs automatically after every sync. |
| `weekly_overview.py` | Generate `Overview/YYMMDD Overview.docx` — Mon–Fri breakdown of sessions and submissions for the upcoming week. |
| `calendar_sync.py` | Sync Canvas assignment deadlines to Apple Calendar ("Canvas Assignments"). Idempotent. |
| `participation_tracker.py` | Build/refresh `Participation Tracker.xlsx` — all courses side by side with a live spoke/entered rate per course. |
| `cheat_sheet.py YYMMDD COURSE` | Generate a case prep notes `.docx` on demand for a specific session. |
| `podcast_gen.py YYMMDD COURSE` | Generate a ~30-min NotebookLM audio overview on demand for a specific session. |
| `update_mcps.py` | Check PyPI for dependency updates and upgrade the venv. Run manually when needed. |

---

## Scheduled jobs

Two jobs run automatically via macOS launchd once set up:

### Daily at 5pm — `canvas_refresh.py --daily`

1. Discover sessions with due dates in the next 2 calendar days
2. Sync Canvas-hosted files for those sessions (files attached in Canvas folders and assignments)
3. Download externally-linked readings (HBSP cases, articles → PDF, YouTube → stub)
4. Generate or refresh Notes `.docx` if stale (new files, edited Canvas description, updated prompt)
5. Organize folders and move duplicates to Trash
6. Sync Canvas deadlines to Apple Calendar

### Sunday 8am — `canvas_refresh.py --weekly`

1. Sync all course files across a 6-week forward horizon
2. For every session in the next 2 weeks: sync Canvas files + download linked readings
3. Generate or refresh Notes for those sessions
4. Organize folders and move duplicates to Trash
5. Generate `Overview/YYMMDD Overview.docx` for the upcoming Mon–Fri
6. Sync Canvas deadlines to Apple Calendar
7. Refresh `Participation Tracker.xlsx` (preserves any ratings already entered)
8. *(If `--with-podcast`)* Show upcoming sessions → prompt to skip any → generate podcasts

---

## Reading downloads (`canvas_readings.py`)

For every session in the notes window, the sync automatically downloads all readings linked in the Canvas assignment description:

| Link type | Action |
|-----------|--------|
| `hbsp.harvard.edu/tu/...` | Download PDF (public coursepack links — no login needed) |
| `services.hbsp.harvard.edu/.../sclinks/` | Download PDF (coursepack sclinks) |
| External articles / blog posts | Headless Chromium print-to-PDF |
| YouTube videos | Save a `.txt` stub with the title and URL |
| LinkedIn / social / mailto | Skip silently |
| `instructure.com` Canvas files | Skip (already handled by the Canvas file sync) |

**Oversized files:** If a PDF exceeds the 50-page limit or the 800k-token context budget, it is downloaded to the session folder but excluded from the AI notes. A `{name} (skipped).txt` stub is written next to it so the exclusion is visible. Delete the stub and re-run to force inclusion.

**File type safety:** If an HBSP download returns a non-PDF (e.g. an Excel exhibit named `.pdf`), the file is automatically renamed to the correct extension (`.docx`, `.xlsx`, etc.) before it reaches the notes generator.

Debug / preview:
```bash
python3 scripts/canvas_readings.py --list 260908 LTV   # show links without downloading
python3 scripts/canvas_readings.py 260908 LTV          # download for one session
```

---

## Notes (AI case prep)

Claude reads the assigned PDFs and Canvas discussion questions and generates a structured `.docx` with:
- Verbatim Canvas assignment at the top
- Case analysis keyed to the discussion questions
- Saved as `YYMMDD COURSE Notes.docx` in the session folder

Notes are regenerated automatically when:
- The session folder has no Notes file yet
- New reading files have been added since the last generation
- The Canvas assignment description changed (professor edited it)
- The master prompt or course refinement prompt was updated since last generation

**Prompt customization:** Edit `prompts/cheat_sheet_prompt.md` (master prompt applied to all courses) and/or create `prompts/cheat_sheet_prompt_COURSE_refinement.md` for course-specific instructions. Editing a prompt marks all upcoming Notes as stale so they regenerate on the next run.

---

## Folder organization (`canvas_organize.py`)

Files are routed to canonical locations after every sync. Each file lives in exactly one place.

| File type | Destination |
|-----------|-------------|
| PPTX / PPT | `COURSE/General/Slides/` (always, even if Canvas attached them to a session) |
| PDF / DOCX in a session folder | Stays in `COURSE/YYMMDD COURSE/` |
| PDF / DOCX at course root | `COURSE/General/` if it's a course-level doc (syllabus, schedule, guide…); otherwise `COURSE/General/Supplemental/` |

Duplicates (same byte size, different location) are moved to the macOS Trash. If sizes differ, a warning is printed and both copies are kept.

---

## Participation Tracker

`participation_tracker.py` creates/refreshes `Participation Tracker.xlsx` at the Coursework root:

- All courses displayed side by side (one column group per course)
- Each course has its own color scheme
- **Row 1**: Full course name header
- **Row 2**: Live participation rate — `spoke / entered` (formula updates as you fill in ratings)
- **Row 3**: Column labels — Day | Case Title | Rating
- **Row 4+**: One row per Canvas session, sorted by date

Rating values: `ok`, `good`, `great`, `x` (didn't speak), or blank (not yet entered). Dropdown validation in every Rating cell. Conditional color-coding: great = green, good = light green, ok = yellow, x = gray.

On refresh, existing ratings are preserved (keyed by course + session date), so Canvas title or date updates don't clobber your entries.

---

## Weekly Overview

`weekly_overview.py` generates `Overview/YYMMDD Overview.docx`:
- Organized Monday through Friday
- Each day lists readings per course (with page counts where available)
- Deliverables (quizzes, uploads, papers) are called out in **bold** at the top of each day
- Saved in `Coursework/Overview/`

---

## Calendar sync

`calendar_sync.py` creates events in Apple Calendar for every Canvas deliverable (quizzes, uploads, papers). Events appear in the **"Canvas Assignments"** calendar.

- Works with iCloud and Google Calendar — create the calendar in whichever you prefer and it syncs to Apple Calendar automatically
- State is tracked in `~/.canvas_calendar_state.json` — reruns won't create duplicates
- Event title format: `5:00pm — LTV Writing Assignment #1 (LTV)`

**First-time setup:** Create a calendar named exactly `Canvas Assignments` in Apple Calendar (or in iCloud/Google Calendar and let it sync). Then run `calendar_sync.py` once to populate it.

---

## Podcast generation

`podcast_gen.py` creates a conversational audio overview using NotebookLM:
- **Case only**: 20-min deep-dive + 10-min discussion question walkthrough
- **Case + supplemental readings**: adds a 5-min frameworks section
- Saved as `YYMMDD COURSE Podcast.m4a` in the session folder

Prompt templates in `prompts/` are fully editable:
- `podcast_prompt.md` — base template (case only)
- `podcast_prompt_supplemental.md` — template with supplemental readings
- `podcast_prompt_COURSE_refinement.md` — per-course additions (one per course)

---

## Folder structure

```
Coursework/
  Overview/
    260831 Overview.docx         ← weekly planning doc
    260907 Overview.docx
  LTV/
    General/
      Slides/                    ← all PPTX files (always here, never in session folders)
      Supplemental/              ← non-session-specific PDFs
    260902 LTV/
      817002-PDF-ENG.pdf         ← HBSP case (auto-downloaded)
      The idea maze.pdf          ← article (auto-downloaded, printed to PDF)
      Beachhead Market (YouTube).txt
      260902 LTV Notes.docx
      260902 LTV Podcast.m4a
    260908 LTV/
      820008-PDF-ENG.pdf
      Ginkgo Bio.pdf
      Ginkgo Bio (skipped).txt   ← token budget exceeded; file present but excluded from notes
  CFO/
    ...
  claude/
    scripts/                     ← working copies of all scripts (what launchd runs)
    prompts/                     ← master prompt + per-course refinements
    canvas_config.json           ← auto-updated: course list, folder paths, cache timestamps
  Participation Tracker.xlsx
```

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/camcdriscoll-collab/hbs-course-helper.git
cd hbs-course-helper
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

> Python 3.12+ required. Use `python3.12` explicitly if your system default is older.

### 2. Add credentials

Create `.env` in the repo root (gitignored):

```
CANVAS_API_TOKEN=your_canvas_token_here
CANVAS_BASE_URL=https://yourschool.instructure.com
ANTHROPIC_API_KEY=sk-ant-...
```

**Getting a Canvas token:** Canvas → Account → Settings → New Access Token. Give it any name and no expiry.

**Canvas URL:** Use your school's Canvas domain — e.g. `https://hbs.instructure.com`, `https://canvas.stanford.edu`, `https://canvas.instructure.com`.

> Courses are auto-discovered from Canvas on the first run. No manual course ID configuration is needed.

### 3. Copy scripts to working directory

The launchd jobs run from `Coursework/claude/scripts/` (inside your Coursework folder). Copy the scripts there:

```bash
cp scripts/* ~/Desktop/Coursework/claude/scripts/
cp -r prompts/ ~/Desktop/Coursework/claude/prompts/
```

Adjust the path if your Coursework folder is somewhere other than `~/Desktop/Coursework/`.

### 4. Set up Apple Calendar (first time only)

1. Create a calendar named exactly **Canvas Assignments** in Apple Calendar (iCloud and Google Calendar both work)
2. Run `python3 scripts/calendar_sync.py` to populate it with all upcoming deadlines

### 5. Authenticate NotebookLM (for podcasts)

```bash
.venv/bin/notebooklm login
```

Opens a browser. Sign in to your Google account. Cookies are cached at `~/.notebooklm/profiles/default/` and reused automatically. Only needed once.

### 6. Schedule automated runs (macOS launchd)

Edit the two plist files in `~/Library/LaunchAgents/` to replace the Python path and script path with your own:

- Python: output of `.venv/bin/python3 --version` (use that full path)
- Script: full path to `canvas_refresh.py` inside your Coursework folder

Then load them:

```bash
launchctl load ~/Library/LaunchAgents/com.canvas-course-helper.daily.plist
launchctl load ~/Library/LaunchAgents/com.canvas-course-helper.weekly.plist
```

**Daily plist** runs `canvas_refresh.py --daily` every day at **5pm**.
**Weekly plist** runs `canvas_refresh.py --weekly` every **Sunday at 8am**.

Logs are written next to the scripts: `canvas_refresh_daily.log` and `canvas_refresh_weekly.log`.

---

## Usage

```bash
# Daily sync (next 2 days)
python3 scripts/canvas_refresh.py --daily

# Weekly sync + overview + calendar
python3 scripts/canvas_refresh.py --weekly

# Weekly with interactive podcast confirmation
python3 scripts/canvas_refresh.py --weekly --with-podcast

# Download readings for a specific session
python3 scripts/canvas_readings.py 260902 LTV

# List links for a session without downloading
python3 scripts/canvas_readings.py --list 260902 LTV

# Generate notes for a specific session on demand
python3 scripts/cheat_sheet.py 260902 LTV

# Refresh the participation tracker manually
python3 scripts/participation_tracker.py

# Generate a podcast for a specific session
python3 scripts/podcast_gen.py 260902 LTV

# Generate the weekly overview doc manually
python3 scripts/weekly_overview.py

# Sync calendar deadlines manually
python3 scripts/calendar_sync.py
python3 scripts/calendar_sync.py --dry-run   # preview without creating events

# Organize and dedup folders
python3 scripts/canvas_organize.py

# Check for dependency updates
python3 scripts/update_mcps.py
```

---

## Flags

| Flag | Script | Effect |
|------|--------|--------|
| `--daily` | `canvas_refresh.py` | Sync next 2 days and refresh stale notes |
| `--weekly` | `canvas_refresh.py` | Full 6-week sync + overview + calendar + tracker |
| `--with-podcast` | `canvas_refresh.py --weekly` | Also generate podcasts (interactive confirmation) |
| `--skip-prompt-regen` | `canvas_refresh.py` | Don't mark notes stale just because the prompt file changed |
| `--list DATE COURSE` | `canvas_readings.py` | Preview reading links without downloading |
| `--dry-run` | `calendar_sync.py` | Print events that would be created, don't create them |

---

## Notes on cost

Notes generation calls the Claude API (Sonnet). A typical session with 3–4 PDFs costs $0.30–$0.80 depending on reading length. The daily run only regenerates notes that are actually stale, so costs are low after the initial setup run.
