# Setup

Start-to-finish setup, written for someone who has never used a terminal. Budget
about 20 minutes, most of it waiting for downloads.

**Requires a Mac.** The calendar sync and the scheduled runs use macOS-only
tools (Apple Calendar and `launchd`).

---

## Before you start: collect three things

Open a note and paste these in as you go. You'll need all three in Step 3.

### 1. A Canvas API token

1. Log in to Canvas.
2. Click **Account** (bottom left) → **Settings**.
3. Scroll to **Approved Integrations** → click **+ New Access Token**.
4. Purpose: `course helper`. Leave the expiry blank.
5. Click **Generate Token**, then copy the long string.

> Copy it now. Canvas shows the token exactly once — if you close the box, you
> have to delete it and make a new one.

### 2. Your Canvas web address

Look at your browser's address bar while you're in Canvas and copy just the
domain — e.g. `https://canvas.harvard.edu`. No trailing slash.

### 3. An Anthropic API key

This is the part people get stuck on, so read it carefully.

**A claude.ai login is not an API key.** Claude for Education, a Claude Pro
subscription, and a claude.ai account are all the *chat* product. This tool
talks to the *developer* product, which is billed separately, by the token.

1. Go to **https://console.anthropic.com** and sign in.
2. If your school has set up an organization there, you may be able to join it
   and bill to that workspace — check with IT or your program office before
   you put a personal card in.
3. Go to **Settings → API keys → Create key**, and copy it. It starts `sk-ant-`.
4. Add a payment method under **Billing**, or the key returns errors on the
   first run. Set a monthly spend limit while you're there — $20 is plenty.

Expect roughly **$0.30–$0.80 per class session** of generated notes.

---

## Step 1 — Get the code

Open **Terminal** (⌘-Space, type "Terminal", press Return) and paste this in:

```bash
git clone https://github.com/camcdriscoll-collab/hbs-course-helper.git ~/hbs-course-helper && cd ~/hbs-course-helper
```

If it says `git: command not found`, macOS will offer to install developer
tools — accept, wait for it to finish, then run the line again.

## Step 2 — Run the installer

```bash
./setup.sh
```

This creates an isolated Python environment, installs everything, and writes
a blank `.env` file for your credentials. It's safe to run more than once.

## Step 3 — Fill in your credentials

```bash
open -e .env
```

Replace the placeholder on each line with the values you collected above, then
save (⌘-S) and close. `COURSEWORK_ROOT` is where your course folders live —
`~/Desktop/Coursework` is a fine answer if you don't have one yet.

> `.env` holds live credentials. It is already excluded from git, so it will
> never be uploaded — but don't paste its contents into email or Slack either.

## Step 4 — First run

```bash
./.venv/bin/python scripts/canvas_refresh.py --daily
```

You should see your courses discovered, files downloading, and notes generating
for anything due in the next two days. First run is the slow one.

## Step 5 — Let it run on its own (optional)

```bash
./setup.sh --schedule
```

Installs two background jobs: a daily sync at 5pm and a full weekly sync on
Sunday at 8am. To stop them later:

```bash
launchctl unload ~/Library/LaunchAgents/com.canvas-course-helper.*.plist
```

## Step 6 — Calendar and podcasts (optional)

- **Calendar**: create a calendar named exactly `Canvas Assignments` in Apple
  Calendar, then run `./.venv/bin/python scripts/calendar_sync.py`.
- **Podcasts**: run `./.venv/bin/notebooklm login` once and sign in to Google.

---

## When something breaks

| What you see | What to do |
|---|---|
| `Missing ANTHROPIC_API_KEY` | A line in `.env` is blank or still says `sk-ant-...` |
| `Canvas HTTP 401` | The Canvas token is wrong or was revoked — generate a new one |
| `WARNING: .env file not found` | You're not in the repo folder. `cd ~/hbs-course-helper` first |
| No courses found | Check `CANVAS_BASE_URL` — domain only, no `/api/v1`, no trailing slash |
| Readings don't download | `./.venv/bin/python -m playwright install chromium` |
| Scheduled runs do nothing | Check `canvas_refresh_daily.log` in the repo folder |

Still stuck? Open an issue on the repo with the error message — with your
tokens removed.
