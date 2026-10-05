---
name: repo-fit
description: Find GitHub repos worth adopting for the projects in a folder, and score each as High, Medium or Skip with a reason. Reads the user's projects, searches GitHub for recently active repos, checks real stars, licence and activity, then writes a dated report. Use when asked to "find repos for my projects", "what should I be using", "scout GitHub", or on a daily or weekly schedule.
---

# repo-fit

Scout GitHub for tools that fit what the user is actually building, and say no to most of them.
Most repos are Skip. A run with zero High is a correct answer.

The helper is `scripts/repo_fit.py` (Python 3, standard library only). Run it from the user's
working directory. Everything it prints is JSON. State lives in `./.repo-fit/`, reports in
`./repo-fit-reports/`. Use the path of this skill's folder for `scripts/repo_fit.py`.

## 1. Profile the projects

Ask which project folders to cover if the user has not said. Default to the current directory.

```
python3 scripts/repo_fit.py profile PATH [PATH...]
```

A project with no manifests (a repo of instructions, skills or docs) comes back with empty
`manifests` and `dependencies`. The `context` files are then the whole profile, so read them
properly. They are cut at 6000 characters, so open the full file if it matters.

Write a short `./.repo-fit/profile.md` once, and reuse it on later runs unless a manifest changed:

- One line per project: what it is, the stack, who it is for.
- A `## Frictions` section: real problems the user has named (slow tests, manual deploys, no auth).
  Ask the user for these once. They are what makes a recommendation specific rather than generic.
- A `## Already have` section: tools, skills, MCP servers and hooks already installed. Fill it
  from what you can read (installed skills folders, MCP config, package manifests), and let the
  user correct it.

**Check the profile against what is live before you search.** Project notes, READMEs and
instruction files go stale, and a stale friction sends the whole run after a problem the user no
longer has. For each friction and each "already have" entry, confirm it against the real thing:
the live config file, the installed tools, the manifest, the running service. In a test run, a
project's notes said its memory was flat markdown files, but its live config had a memory
provider enabled, so every memory repo turned out to be already covered. If you cannot check
something, mark it `unverified` in `profile.md` and in the report.

Never invent a project, a stack or a friction. If the profile is thin, say so in the report.

## 2. Search

Turn the profile into 5 to 8 GitHub search queries: stack keywords, the category of tool a
friction suggests, and one or two broader topics. Plain keywords, no marketing words.

**Keep each query to 2 or 3 words.** GitHub requires every word to match, so a long query
returns nothing (in a test run, four 4-word queries returned zero repos). If a query returns
nothing, shorten it before concluding there is nothing out there, and say so in the report.

```
python3 scripts/repo_fit.py search "mcp server supabase" "nextjs auth" --days 7 --min-stars 20
```

Raise `--days` for a weekly run. Without a token the search API allows about 10 queries a minute;
set `GITHUB_TOKEN` if you hit the limit.

## 3. Facts before opinions

```
python3 scripts/repo_fit.py facts owner/repo [owner/repo...]
```

Add `--personal` if the user only uses tools for personal projects (non-commercial licences are
then allowed). Each result carries `stars`, `licence`, `pushed_at`, `gates` and `previously`.

- Any entry in `gates` is a Skip, reason is the gate name. `licence-unclear` means open the
  LICENSE file before deciding. `noncommercial` is a Skip for anyone using it for paid work.
- `error` with `unverified: true` means the lookup failed. Rate it on what you can see, mark it
  `unverified`, never fill in a number.
- `previously` is the last verdict. Reuse it unless something concrete changed: a new release,
  a big jump in stars, a new project it now serves, or a friction the user added. Say what
  changed. "Looked again" is not a change. This is what stops a repo being Skip on Monday and
  High on Tuesday.

## 4. Judge fit

For repos that pass the gates, read the README (and recent issues if it matters), then decide.

- **High**: serves a named project and removes a named friction, passes every gate, nothing the
  user already has covers it, and there is a first step they could run today on a throwaway branch.
- **Medium**: plausible, needs evaluation or adaptation. Say what would promote it.
- **Skip**: everything else. Give a one-word reason: `no-fit`, `wrong-stack`, `already-covered`,
  `too-new`, `licence`, `unmaintained`.

The friction must point at something real, such as a file, a dependency or a line in the
profile. "Could be useful for AI" is Skip. Do not recommend a replacement for something that
works. If a new stack would be needed, the case has to be much stronger.

## 5. Report and remember

Write `./repo-fit-reports/YYYY-MM-DD.md`:

```
Summary: X reviewed · Y High · Z Medium · N skipped
Profile: <date profile.md was last changed>

## High
**owner/repo** ⭐ stars · licence · pushed date
- Project: ...
- Friction: ... (what in the profile or code shows it)
- Why this one: 1 or 2 sentences
- First step: one concrete action
- [ ] tried   [ ] parked   [ ] rejected

## Medium
**owner/repo** ⭐ stars: one line, and what would promote it

## Skipped
owner/repo (reason), owner/repo (reason)
```

Then record every verdict, so the next run remembers it:

```
python3 scripts/repo_fit.py record owner/repo Skip --reason wrong-stack
```

On the next run, read the previous report first. A box the user ticked wins: `rejected` stays
Skip unless something material changed, and `tried` counts as already installed.

If nothing is High or Medium, still write the report and say "No strong matches this run." Say it
only when both sections are empty. A Medium counts as a match, so a report with one never says it
(a test run did, with a Medium listed two sections below the line).

## Rules

- State only what you fetched. Stars, licence and dates come from `facts`, not from search
  snippets, READMEs or memory.
- Be short and opinionated. No padding, no essay.
- Never install anything. Recommend, and leave the install to the user.
