# HBS Coursework Automation

Automated Canvas file sync, AI-generated case prep notes, and NotebookLM podcast generation for HBS MBA coursework.

## What it does

| Script | Purpose |
|--------|---------|
| `canvas_refresh.py --daily` | Sync files + regenerate stale notes for sessions in the next 2 days |
| `canvas_refresh.py --weekly` | Full sync, notes for 2-week window, dedup, update check |
| `cheat_sheet.py YYMMDD COURSE` | Generate a case prep notes .docx on demand |
| `podcast_gen.py YYMMDD COURSE` | Generate a ~30-min NotebookLM audio overview on demand |
| `canvas_organize.py` | Route files to correct folders, dedup to Trash |
| `update_mcps.py` | Check for package updates and upstream MCP repo changes |

### Notes (cheat sheet)
For each session, Claude reads the assigned PDFs and Canvas discussion questions and generates a structured `.docx` with:
- Verbatim Canvas assignment at the top
- Case analysis keyed to the discussion questions
- Saved as `YYMMDD COURSE Notes.docx` in the session folder

### Podcast
NotebookLM generates a conversational audio overview (~30 min):
- **Case only**: 20 min deep-dive + 10 min discussion questions
- **Case + supplemental readings**: adds 5 min frameworks from the supplemental docs
- Saved as `YYMMDD COURSE Podcast.m4a` in the session folder

### Folder structure
```
Coursework/
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
```

## Setup

### 1. Prerequisites
- Python 3.10+
- [uv](https://docs.astral.sh/uv/) (`curl -LsSf https://astral.sh/uv/install.sh | sh`)
- A Canvas LMS account with API token
- An Anthropic API key
- A Google account (for NotebookLM)

### 2. Install dependencies
```bash
git clone https://github.com/camcdriscoll-collab/hbs-coursework-automation.git
cd hbs-coursework-automation
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

### 3. Configure credentials
Create `~/.canvas_env` (or set as environment variables):
```
CANVAS_API_TOKEN=your_canvas_token_here
ANTHROPIC_API_KEY=sk-ant-...
```

### 4. Configure courses
Run any script once — it will prompt you to set up `canvas_config.json` with your course IDs and folder paths. Course IDs are the numbers in your Canvas course URLs.

### 5. Authenticate NotebookLM (for podcasts)
```bash
.venv/bin/notebooklm login
```
Completes in a browser window. Cookies are cached at `~/.notebooklm/profiles/default/` and reused automatically.

### 6. Schedule automated runs (macOS)
The `--daily` and `--weekly` modes are designed for launchd:
- **Daily at 5pm**: `canvas_refresh.py --daily`
- **Sunday 8am**: `canvas_refresh.py --weekly`

Add `--with-podcast` to weekly if you want podcasts generated automatically (adds ~10 min per session).

## Usage

```bash
# Sync and generate notes for upcoming sessions
.venv/bin/python3 scripts/canvas_refresh.py --daily

# Full weekly sync + notes + dedup + update check
.venv/bin/python3 scripts/canvas_refresh.py --weekly

# Generate notes for a specific session
.venv/bin/python3 scripts/cheat_sheet.py 260902 LTV

# Generate a podcast for a specific session
.venv/bin/python3 scripts/podcast_gen.py 260902 LTV

# Check for package and upstream MCP updates
.venv/bin/python3 scripts/update_mcps.py
```

## Flags

| Flag | Effect |
|------|--------|
| `--skip-prompt-regen` | Don't regenerate notes just because the prompt changed |
| `--with-podcast` | Also generate NotebookLM podcasts in the notes window |

## How updates work

The weekly run automatically:
1. Checks PyPI for newer versions of all dependencies and upgrades them
2. Checks whether the upstream [notebooklm-mcp](https://github.com/alfredang/notebooklm-mcp) and [canvas-mcp](https://github.com/vishalsachdev/canvas-mcp) repos have new commits
3. Reports upstream changes but does **not** auto-merge them (the MCP forks have custom additions that could conflict)

To review and apply upstream MCP changes manually:
```bash
cd ~/repos/notebooklm-mcp
git fetch https://github.com/alfredang/notebooklm-mcp.git main
git merge FETCH_HEAD
```

## Related repos
- [camcdriscoll-collab/canvas-mcp-hbs2026](https://github.com/camcdriscoll-collab/canvas-mcp-hbs2026) — Canvas MCP server (fork of vishalsachdev/canvas-mcp)
- [camcdriscoll-collab/notebooklm-mcp](https://github.com/camcdriscoll-collab/notebooklm-mcp) — NotebookLM MCP server (fork of alfredang/notebooklm-mcp)
