# HBS Course Helper

Automated Canvas file sync, AI-generated case prep notes, reading downloads, weekly planning overview, calendar integration, and NotebookLM podcast generation for HBS MBA coursework.

## What it does

| Script | Purpose |
|--------|---------|
| `canvas_refresh.py --daily` | Sync files + download readings + regenerate stale notes for sessions in the next 2 days. Runs every day at 5pm via launchd. |
| `canvas_refresh.py --weekly` | Full 6-week sync, reading downloads, notes for 2-week window, weekly overview doc, calendar sync, participation tracker refresh. Runs Sunday 8am via launchd. |
| `canvas_readings.py YYMMDD COURSE` | Download all linked readings for a session on demand (HBSP cases, articles, YouTube stubs). |
| `canvas_organize.py` | Route files to correct folders (PPTX → Slides/, etc.) and dedup to Trash. Runs automatically after every sync. |
| `weekly_overview.py` | Generate `Overview/YYMMDD Overview.docx` — Mon–Fri breakdown of sessions and submissions for the upcoming week. |
| `calendar_sync.py` | Sync Canvas assignment deadlines to Apple Calendar ("Canvas Assignments"). Idempotent. |
| `participation_tracker.py` | Build/refresh `Participation Tracker.xlsx` — one tab with all courses side by side, live spoke/entered rate per course. |
| `cheat_sheet.py YYMMDD COURSE` | Generate a case prep notes `.docx` on demand for a specific session. |
| `podcast_gen.py YYMMDD COURSE` | Generate a ~30-min NotebookLM audio overview on demand for a specific session. |
| `update_mcps.py` | Check PyPI for dependency updates and upgrade the venv. Run manually when needed. |

---

## Daily run (`--daily`, 5pm every day)

1. Discover sessions with due dates in the next 2 days
2. Sync Canvas-hosted files for those sessions (Canvas folders + files linked in assignments)
3. Download externally-linked readings (HBSP cases, articles → PDF, YouTube → stub)
4. Generate or refresh Notes `.docx` if stale (new files, edited Canvas description, updated prompt)
5. Organize folders + dedup to Trash
6. Sync Canvas deadlines to Calendar

## Weekly run (`--weekly`, Sunday 8am)

1. Sync all course files across a 6-week horizon
2. For every session within the next 2 weeks: sync Canvas files + download linked readings
3. Generate or refresh Notes for those sessions
4. Organize folders + dedup to Trash
5. Generate `Overview/YYMMDD Overview.docx` for the upcoming week
6. Sync Canvas deadlines to Calendar
7. Refresh `Participation Tracker.xlsx` (preserves any ratings already entered)
8. *(If `--with-podcast`)* Open the overview doc → show numbered session list → prompt to skip any → generate podcasts

---

## Reading downloads (`canvas_readings.py`)

For every session in the notes window, the sync automatically downloads all readings linked in the Canvas assignment description:

| Link type | Action |
|-----------|--------|
| `hbsp.harvard.edu/tu/...` | Download PDF (public coursepack links — no login needed) |
| External articles / blog posts | Headless Chromium print-to-PDF |
| YouTube videos | Save a `.txt` stub with the title and URL |
| LinkedIn / social / mailto | Skip silently |
| `instructure.com` Canvas files | Skip (already handled by the Canvas file sync) |

**Oversized files:** If a PDF exceeds the 50-page limit or the 800k-token context budget, it is downloaded to the session folder but excluded from the AI notes. A `{name} (skipped).txt` stub is written next to it so the exclusion is visible.

**File type safety:** If an HBSP download returns a non-PDF (e.g. an Excel exhibit named `.pdf`), the file is automatically renamed to the correct extension (`.docx`, `.xlsx`, etc.) before it reaches the notes generator.

Debug / dry run:
```bash
python3 scripts/canvas_readings.py --list 260908 LTV   # show links without downloading
python3 scripts/canvas_readings.py 260908 LTV           # download for one session
```

---

## Notes (cheat sheet)

Claude reads the assigned PDFs and Canvas discussion questions and generates a structured `.docx` with:
- Verbatim Canvas assignment at the top
- Case analysis keyed to the discussion questions
- Saved as `YYMMDD COURSE Notes.docx` in the session folder

Notes are regenerated automatically when:
- The session folder has no Notes file yet
- New reading files have been added since the last generation
- The Canvas assignment description changed (professor edited it)
- The master prompt or course refinement prompt was updated since last generation

---

## Participation Tracker

`participation_tracker.py` (called automatically by `--weekly`) creates/refreshes `~/Desktop/Coursework/Participation Tracker.xlsx`:

- One tab — all four courses (CATS, CFO, LME, LTV) side by side with a narrow separator between each
- Each course has its own color scheme (teal / blue / red / purple)
- **Row 1**: Full course name header
- **Row 2**: Live participation rate — `spoke / entered` (updates as you fill in ratings)
- **Row 3**: Column labels — Day | Case Title | Rating
- **Row 4+**: One row per Canvas session, sorted by date

Rating values: `ok`, `good`, `great`, `x` (didn't speak), or blank (not yet entered). Dropdown validation in every Rating cell. Conditional color-coding: great = green, good = light green, ok = yellow, x = gray.

On refresh, existing ratings are preserved (keyed by course + session date), so Canvas title or date updates don't clobber your entries.

---

## Weekly Overview

`weekly_overview.py` (called automatically by `--weekly`) generates `Overview/YYMMDD Overview.docx`:
- Organized Monday through Friday
- Each day lists readings per course with page counts
- Submissions (assignments due) are called out in **bold** at the top of each day
- Saved in `~/Desktop/Coursework/Overview/`

---

## Calendar sync

`calendar_sync.py` creates events in Apple Calendar for every Canvas deliverable (quizzes, uploads, papers). Events appear in the **"Canvas Assignments"** calendar.

- Works with both iCloud and Google Calendar — create the calendar in whichever you prefer and it will appear in Apple Calendar automatically
- State is tracked in `~/.canvas_calendar_state.json` — reruns won't create duplicates
- Event title format: `5:00pm — LTV Writing Assignment #1 (LTV)`

**First-time setup:** Create a calendar named exactly `Canvas Assignments` in Apple Calendar (or iCloud/Google Calendar and let it sync). Then run `calendar_sync.py` to populate it.

---

## Podcast

NotebookLM generates a conversational audio overview from the case PDFs:
- **Case only**: 20 min deep-dive + 10 min discussion questions
- **Case + supplemental readings**: adds a 5 min frameworks section
- Saved as `YYMMDD COURSE Podcast.m4a` in the session folder

Prompt templates live in `prompts/` and are fully editable:
- `podcast_prompt.md` — base template (case only)
- `podcast_prompt_supplemental.md` — template with supplemental readings
- `podcast_prompt_COURSE_refinement.md` — per-course customizations (one per course)

---

## Folder structure

```
~/Desktop/Coursework/
  Overview/
    260831 Overview.docx           ← weekly planning doc
    260907 Overview.docx
  LTV/
    General/
      Slides/                      ← all PPTX files (always here, never in session folders)
      Supplemental/                ← non-session-specific PDFs
    260902 LTV/
      817002-PDF-ENG.pdf           ← HBSP case (auto-downloaded)
      The idea maze.pdf            ← article (auto-downloaded, printed to PDF)
      Beachhead Market (YouTube).txt  ← YouTube stub
      260902 LTV Notes.docx
      260902 LTV Podcast.m4a
    260908 LTV/
      820008-PDF-ENG.pdf
      Ginkgo Bio.pdf
      Ginkgo Bio (skipped).txt     ← token budget exceeded; file present but not in notes
      ...
  CFO/
    ...
  CATS/
    ...
  LME/
    ...
  claude/
    scripts/                       ← working copies of all scripts (what launchd runs)
    prompts/                       ← master prompt + per-course refinements for notes and podcasts
    canvas_config.json             ← cached course folder paths (auto-updated)
```

Each file lives in exactly one place. `canvas_organize.py` enforces this after every sync.

---

## Setup

### 1. Clone and install
```bash
git clone https://github.com/camcdriscoll-collab/hbs-course-helper.git
cd hbs-course-helper
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Add credentials
Create `.env` in the repo root (gitignored):
```
CANVAS_API_TOKEN=your_canvas_token_here
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Configure courses
Edit `CANVAS_IDS` in `scripts/path_config.py` to match your enrolled courses and their Canvas course IDs.

### 4. Copy scripts to working directory
The launchd jobs run from `~/Desktop/Coursework/claude/scripts/`. Copy scripts there:
```bash
cp scripts/* ~/Desktop/Coursework/claude/scripts/
cp -r prompts/ ~/Desktop/Coursework/claude/prompts/
```

### 5. Set up Calendar (first time only)
1. Create a calendar named exactly **Canvas Assignments** in Apple Calendar (iCloud or Google Calendar both work)
2. Run `python3 scripts/calendar_sync.py` to populate it with all upcoming deadlines

### 6. Authenticate NotebookLM (for podcasts)
```bash
.venv/bin/notebooklm login
```
Opens a browser. Cookies are cached at `~/.notebooklm/profiles/default/` and reused automatically.

### 7. Schedule automated runs (macOS launchd)
Plists live in `~/Library/LaunchAgents/`:
- **Daily at 5pm**: `canvas_refresh.py --daily`
- **Sunday 8am**: `canvas_refresh.py --weekly`

Load them with:
```bash
launchctl load ~/Library/LaunchAgents/com.charlottedriscoll.canvas-refresh-daily.plist
launchctl load ~/Library/LaunchAgents/com.charlottedriscoll.canvas-refresh-weekly.plist
```

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

# Generate notes for a specific session
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
```

## Flags

| Flag | Effect |
|------|--------|
| `--skip-prompt-regen` | Don't regenerate notes just because the prompt file changed |
| `--with-podcast` | After weekly sync, open the overview doc and interactively confirm which sessions to generate podcasts for |
