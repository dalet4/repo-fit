# repo-fit

A skill for Claude Code (and other agents that read `SKILL.md`) that finds GitHub repos worth
adopting for the projects you are actually building, and says no to most of them.

It reads your project folders, searches GitHub for recently active repos, checks real stars,
licence and activity, then writes a dated report: **High**, **Medium** or **Skip**, with a reason.
It remembers its earlier verdicts, so a repo is not Skip on Monday and High on Tuesday for no reason.

## Install

Claude Code:

```
git clone https://github.com/dalet4/repo-fit ~/.claude/skills/repo-fit
```

Then ask: "run repo-fit on ~/code/my-app and ~/code/client-site".

Other editors and agents (Cursor, Codex, Gemini CLI, Zed, VS Code, Windsurf, Cline, Junie, Goose
and others that read the [Agent Skills](https://agentskills.io/specification) format), from your
project folder:

```
npx skills add dalet4/repo-fit -a cursor
```

Swap `cursor` for your agent's name, or leave `-a` off to choose from a list. This copies the skill
into that agent's skills folder (`.agents/skills/` for the agents that share it). The installer
sends anonymous usage data; set `DISABLE_TELEMETRY=1` to switch that off. Review the skill before
use, since an installed skill runs with your agent's permissions.

Tested: the install lands the files and `scripts/repo_fit.py` runs from the installed location.
Codex CLI (0.156.1) also lists the skill when it is installed in the project folder, and runs the
`profile` helper from there; it does not list it in a folder without the install. Not yet tested:
the search, facts and report steps in Codex, and loading the skill in any other editor. Only Claude
Code has run it end to end so far.
The helper is a plain command line script, so any agent that can run Python can use it.

You need Python 3 and internet access. Set `GITHUB_TOKEN` to avoid the unauthenticated rate
limit (about 10 searches a minute).

## What it does

1. **Profile**: reads manifests, dependencies and README/CLAUDE.md/AGENTS.md for each project,
   and keeps a short `.repo-fit/profile.md` including the frictions you name.
2. **Search**: turns that into GitHub queries for repos pushed in the last N days.
3. **Facts**: for each candidate, fetches stars, licence, last push, age and archived status, and
   applies hard gates (archived, unmaintained, no, non-commercial or source-available licence for client work, brand-new
   single-author repo). Search snippets are never trusted for numbers.
4. **Judge**: High needs a named project, a named friction and a first step you could run today.
5. **Report**: `repo-fit-reports/YYYY-MM-DD.md`, with tick boxes you fill in. Ticked boxes are
   read on the next run.

Run it on a schedule with whatever you already use (a Claude Code routine, cron, a CI job).

## Example report

An excerpt from a real run on 2026-10-05, with the project details generalised. The run reviewed
17 repos for a Next.js and Supabase dashboard and found nothing worth a High.

```
Summary: 17 reviewed · 0 High · 1 Medium · 16 skipped
Profile: 2026-10-05 (new, `.repo-fit/profile.md`)

## Notes on the run
- Search worked: 8 queries (2 to 3 words), `--days 7 --min-stars 20`, 66 candidates, no errors.
  Most were noise; 12 got a `facts` lookup.
- One friction in the profile came from the project's notes and could not be checked live (no
  database access in a read-only run), so it is marked **unverified**.

## High
None.

## Medium
**Farenhytee/database-sentinel** ⭐ 48 · MIT · pushed 2026-10-05: read-only Supabase RLS and
exposed-key audit (MCP server, CLI and Claude skill) that returns fix SQL. It fits a dashboard
holding customer data. It overlaps with the Supabase advisors and a security skill that is already
installed, and it is only 7 months old. It would be promoted if a read-only trial on a throwaway
branch finds gaps those two miss.

## Skipped
ohad6k/VibeRaven (already-covered), happyDomain/happydeliver (licence: unclear), supabase/cli
(licence: none reported by the API), DBDiff/DBDiff (no-fit), ozgurcd/gograph (wrong-stack),
czlonkowski/n8n-mcp (already-covered), and 10 more

Not reviewed, not recorded: the other ~54 search hits were off-topic and never got a `facts` lookup.
```

A High entry also gets a project, a friction, a first step and three tick boxes
(`tried`, `parked`, `rejected`). Ticked boxes are read on the next run.

## The helper

```
python3 scripts/repo_fit.py profile ./app
python3 scripts/repo_fit.py search "mcp server" --days 7
python3 scripts/repo_fit.py facts owner/repo --personal
python3 scripts/repo_fit.py record owner/repo Skip --reason wrong-stack
```

Tests: `python3 -m unittest discover tests`

## Limits

- The gates are rules of thumb: 90 days without a push, under 14 days old with under 50 stars from
  a personal account, and so on. Edit `gates()` if yours differ.
- `licence-unclear` means GitHub could not classify the licence. Open the file yourself.
- A recommendation is a starting point. It never installs anything.

MIT licence.
