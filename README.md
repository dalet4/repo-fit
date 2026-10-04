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

Other agents: point your agent's instructions at `SKILL.md`. The helper is a plain command line
script, so any agent that can run Python can use it. (Only tested with Claude Code so far.)

You need Python 3 and internet access. Set `GITHUB_TOKEN` to avoid the unauthenticated rate
limit (about 10 searches a minute).

## What it does

1. **Profile**: reads manifests, dependencies and README/CLAUDE.md/AGENTS.md for each project,
   and keeps a short `.repo-fit/profile.md` including the frictions you name.
2. **Search**: turns that into GitHub queries for repos pushed in the last N days.
3. **Facts**: for each candidate, fetches stars, licence, last push, age and archived status, and
   applies hard gates (archived, unmaintained, no or non-commercial licence, brand-new
   single-author repo). Search snippets are never trusted for numbers.
4. **Judge**: High needs a named project, a named friction and a first step you could run today.
5. **Report**: `repo-fit-reports/YYYY-MM-DD.md`, with tick boxes you fill in. Ticked boxes are
   read on the next run.

Run it on a schedule with whatever you already use (a Claude Code routine, cron, a CI job).

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
