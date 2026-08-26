# HBS Course Helper

Automated Canvas file sync, AI-generated case prep notes, and NotebookLM podcast generation for HBS MBA coursework.

## What it does

| Script | Purpose |
|--------|---------|
| `canvas_refresh.py --daily` | Sync files + regenerate stale notes for sessions in the next 2 days |
| `canvas_refresh.py --weekly` | Full sync, notes for 2-week window, dedup, dependency updates |
| `cheat_sheet.py YYMMDD COURSE` | Generate a case prep notes .docx on demand |
| `podcast_gen.py YYMMDD COURSE` | Generate a ~30-min NotebookLM audio overview on demand |
| `canvas_organize.py` | Route files to correct folders, dedup to Trash |
| `update_mcps.py` | Check PyPI for dependency updates and upgrade automatically |

### Notes (cheat sheet)
For each session, Claude reads the assigned PDFs and Canvas discussion questions and generates a structured `.docx` with:
- Verbatim Canvas assignment at the top
- Case analysis keyed to the discussion questions
- Saved as `YYMMDD COURSE Notes.docx` in the session folder

### Podcast
NotebookLM generates a conversational audio overview:
- **Case only**: 20 min deep-dive + 10 min discussion questions
- **Case + supplemental readings**: adds 5 min frameworks section
- Saved as `YYMMDD COURSE Podcast.m4a` in the session folder

### Folder structure
```
~/Desktop/Coursework/
  LTV/
    General/
      Slides/          ← all PPTX files
      Supplemental/    ← non-session-specific PDFs
    260902 LTV/
      Case PDF.pdf
      260902 LTV Notes.docx
      260902 LTV Podcast.m4a
  CATS/
    ...
  claude/
    scripts/           ← working copies of these scripts
    prompts/           ← master prompt + per-course refinements
```

## Setup

### 1. Clone and install
```bash
git clone https://github.com/camcdriscoll-collab/hbs-course-helper.git
cd hbs-course-helper
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 2. Add credentials
Create `.env` in the repo root (gitignored):
```
CANVAS_API_TOKEN=your_canvas_token_here
ANTHROPIC_API_KEY=sk-ant-...
```

### 3. Run once to configure courses
```bash
.venv/bin/python3 scripts/canvas_refresh.py --daily
```
This auto-discovers your course folders and writes `claude/canvas_config.json`.

### 4. Authenticate NotebookLM (for podcasts)
```bash
.venv/bin/notebooklm login
```
Opens a browser. Cookies are cached at `~/.notebooklm/profiles/default/` and reused automatically.

### 5. Schedule automated runs (macOS launchd)
- **Daily at 5pm**: `canvas_refresh.py --daily`
- **Sunday 8am**: `canvas_refresh.py --weekly`

## Usage

```bash
# Sync and generate notes for upcoming sessions
.venv/bin/python3 scripts/canvas_refresh.py --daily

# Full weekly sync + notes + dedup + dependency updates
.venv/bin/python3 scripts/canvas_refresh.py --weekly

# Generate notes for a specific session
.venv/bin/python3 scripts/cheat_sheet.py 260902 LTV

# Generate a podcast for a specific session
.venv/bin/python3 scripts/podcast_gen.py 260902 LTV
```

## Flags

| Flag | Effect |
|------|--------|
| `--skip-prompt-regen` | Don't regenerate notes just because the prompt changed |
| `--with-podcast` | Also generate NotebookLM podcasts in the notes window |
