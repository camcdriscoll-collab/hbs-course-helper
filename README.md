# HBS Course Helper

Automated Canvas file sync, AI-generated case prep notes, weekly planning overview, calendar integration, and NotebookLM podcast generation for HBS MBA coursework.

## What it does

| Script | Purpose |
|--------|---------|
| `canvas_refresh.py --daily` | Sync files + regenerate stale notes for sessions in the next 2 days. Runs every day at 5pm via launchd. |
| `canvas_refresh.py --weekly` | Full 6-week sync, notes for 2-week window, weekly overview doc, calendar sync, optional podcast confirmation. Runs Sunday 8am via launchd. |
| `canvas_organize.py` | Route files to correct folders (PPTX → Slides/, etc.) and dedup to Trash. Runs automatically after every sync. |
| `weekly_overview.py` | Generate `Overview/YYMMDD Overview.docx` — Mon–Fri breakdown of sessions and submissions for the upcoming week. |
| `calendar_sync.py` | Sync Canvas assignment deadlines to Apple Calendar ("Canvas Assignments" calendar → syncs to Google Calendar). Idempotent. |
| `canvas_sync.py` | Standalone full sync for all courses (manual fallback; same logic is built into `canvas_refresh.py`). |
| `cheat_sheet.py YYMMDD COURSE` | Generate a case prep notes `.docx` on demand for a specific session. |
| `podcast_gen.py YYMMDD COURSE` | Generate a ~30-min NotebookLM audio overview on demand for a specific session. |
| `update_mcps.py` | Check PyPI for dependency updates and upgrade automatically. |

---

## Daily run (`--daily`, 5pm every day)

1. Discover sessions with due dates in the next 2 days
2. Sync Canvas files for those sessions
3. Generate or refresh Notes `.docx` if stale (new files, edited Canvas description, updated prompt)
4. Organize folders + dedup to Trash
5. Sync Canvas deadlines to Calendar

## Weekly run (`--weekly`, Sunday 8am)

1. Sync all course files across a 6-week horizon
2. Generate or refresh Notes for sessions within the next 2 weeks
3. Organize folders + dedup to Trash
4. Generate `Overview/YYMMDD Overview.docx` for the upcoming week
5. Sync Canvas deadlines to Calendar
6. *(If `--with-podcast`)* Open the overview doc → show numbered session list → prompt to skip any → generate podcasts

---

## Notes (cheat sheet)

Claude reads the assigned PDFs and Canvas discussion questions and generates a structured `.docx` with:
- Verbatim Canvas assignment at the top
- Case analysis keyed to the discussion questions
- Saved as `YYMMDD COURSE Notes.docx` in the session folder

Notes are regenerated automatically when:
- The session folder has no Notes file yet
- New reading files have been added
- The Canvas assignment description changed (professor edited it)
- The master prompt or course refinement prompt was updated since last generation

## Weekly Overview

`weekly_overview.py` (called automatically by `--weekly`) generates `Overview/YYMMDD Overview.docx`:
- Organized Monday through Friday
- Each day lists readings per course with page counts
- Submissions (assignments due) are called out in **bold** at the top of each day
- Saved in `~/Desktop/Coursework/Overview/`

## Calendar sync

`calendar_sync.py` creates all-day events in Apple Calendar for every Canvas deliverable (quizzes, uploads, papers). Events appear in the **"Canvas Assignments"** calendar, which syncs automatically to Google Calendar.

- State is tracked in `~/.canvas_calendar_state.json` — reruns won't create duplicates
- Event title format: `5:00pm — LTV Writing Assignment #1 (LTV)`

**First-time setup:** Create a calendar named `Canvas Assignments` in Google Calendar and set it orange. It will appear in Apple Calendar after a sync.

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
    260831 Overview.docx       ← weekly planning doc
    260907 Overview.docx
  LTV/
    General/
      Slides/                  ← all PPTX files (always here, never in session folders)
      Supplemental/            ← non-session-specific PDFs
    260902 LTV/
      Case PDF.pdf
      260902 LTV Notes.docx
      260902 LTV Podcast.m4a
  CATS/
    ...
  claude/
    scripts/                   ← working copies of all scripts (what launchd runs)
    prompts/                   ← master prompt + per-course refinements for notes and podcasts
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

### 3. Copy scripts to working directory
The launchd jobs run from `~/Desktop/Coursework/claude/scripts/`. Copy scripts there:
```bash
cp scripts/* ~/Desktop/Coursework/claude/scripts/
cp -r prompts/ ~/Desktop/Coursework/claude/prompts/
```

### 4. Set up Calendar (first time only)
1. In Google Calendar, create a new calendar called **Canvas Assignments** and set it orange
2. Wait for it to appear in Apple Calendar (or force-refresh with ⌘R)
3. Run `calendar_sync.py` to populate it with all upcoming deadlines

### 5. Authenticate NotebookLM (for podcasts)
```bash
.venv/bin/notebooklm login
```
Opens a browser. Cookies are cached at `~/.notebooklm/profiles/default/` and reused automatically.

### 6. Schedule automated runs (macOS launchd)
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

# Generate notes for a specific session
python3 scripts/cheat_sheet.py 260902 LTV

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
