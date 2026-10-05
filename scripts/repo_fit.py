#!/usr/bin/env python3
"""repo-fit helper: the deterministic half of the repo-fit skill. Standard library only.

  repo_fit.py profile PATH...            what the projects are made of
  repo_fit.py search QUERY... [--days N] recently pushed repos matching GitHub search queries
  repo_fit.py facts OWNER/REPO...        stars, licence, activity, gate flags, previous verdict
  repo_fit.py record OWNER/REPO VERDICT  remember a verdict so the next run does not flip it

All output is JSON on stdout. Set GITHUB_TOKEN for a higher API rate limit.
"""
import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://api.github.com"
CONTEXT_FILES = ("CLAUDE.md", "AGENTS.md", "README.md")
CONTEXT_CHARS = 6000  # instruction-only repos have no manifests, so these files are the whole profile
MANIFESTS = ("package.json", "requirements.txt", "pyproject.toml", "go.mod", "Cargo.toml", "Gemfile", "composer.json")


class ApiError(Exception):
    pass


def gh(path, **params):
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "repo-fit"}
    if os.environ.get("GITHUB_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
    for attempt in (1, 2):  # one retry: timeouts on a single lookup are common and transient
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            hint = ""
            if e.code in (403, 429):
                if e.headers.get("X-RateLimit-Remaining") == "0":
                    hint = " (rate limited: set GITHUB_TOKEN)"
                else:  # a proxy or policy block, not a limit: a token will not help, so say what it said
                    try:
                        hint = f" ({json.load(e)['message']})"
                    except (ValueError, KeyError, AttributeError):
                        pass
            raise ApiError(f"GitHub API {e.code} for {path}{hint}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 2:
                raise ApiError(f"network error for {path}: {getattr(e, 'reason', e)}")


def days_since(stamp, now):
    return (now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).days


def gates(repo, now=None, personal=False):
    """Hard-skip flags for a GitHub API repo object. Empty list means it passes."""
    now = now or datetime.now(timezone.utc)
    flags = []
    if repo.get("archived"):
        flags.append("archived")
    if days_since(repo["pushed_at"], now) > 90:
        flags.append("unmaintained")
    lic = (repo.get("license") or {}).get("spdx_id")
    if not lic:
        flags.append("no-licence")
    elif lic in ("NOASSERTION", "Other"):
        flags.append("licence-unclear")  # open the LICENSE file before trusting it
    elif re.search(r"(^|-)NC(-|$)|noncommercial", lic, re.I) and not personal:
        flags.append("noncommercial")
    young = days_since(repo["created_at"], now) < 14
    if young and repo.get("stargazers_count", 0) < 50 and (repo.get("owner") or {}).get("type") == "User":
        flags.append("too-new")
    return flags


def load_history(state):
    last = {}
    f = Path(state) / "history.jsonl"
    if f.exists():
        for line in f.read_text().splitlines():
            if line.strip():
                row = json.loads(line)
                last[row["repo"].lower()] = row  # later rows win
    return last


def record(repo, verdict, reason, state, today=None):
    Path(state).mkdir(parents=True, exist_ok=True)
    row = {"repo": repo, "verdict": verdict, "reason": reason, "date": today or datetime.now().date().isoformat()}
    with open(Path(state) / "history.jsonl", "a") as f:
        f.write(json.dumps(row) + "\n")
    return row


def facts(slug, state, personal=False):
    prev = load_history(state).get(slug.lower())
    try:
        r = gh(f"/repos/{slug}")
    except ApiError as e:
        return {"repo": slug, "error": str(e), "unverified": True, "previously": prev}
    return {
        "repo": r["full_name"], "url": r["html_url"], "description": r.get("description"),
        "stars": r["stargazers_count"], "licence": (r.get("license") or {}).get("spdx_id"),
        "pushed_at": r["pushed_at"], "created_at": r["created_at"], "topics": r.get("topics", []),
        "gates": gates(r, personal=personal), "previously": prev,
    }


def search(queries, days, min_stars, limit):
    since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
    seen, out, errors = set(), [], []
    for q in queries:
        try:
            data = gh("/search/repositories", q=f"{q} pushed:>{since} stars:>={min_stars}",
                      sort="updated", order="desc", per_page=limit)
        except ApiError as e:
            errors.append(str(e))
            continue
        for r in data["items"]:
            if r["full_name"] not in seen:
                seen.add(r["full_name"])
                out.append({"repo": r["full_name"], "stars": r["stargazers_count"],
                            "description": r.get("description"), "pushed_at": r["pushed_at"], "query": q})
    return {"candidates": out, "errors": errors}


def head(path, chars):
    try:
        return path.read_text(errors="replace")[:chars]
    except OSError:
        return None


def deps(path):
    if path.name == "package.json":
        try:
            d = json.loads(path.read_text())
            return sorted({*d.get("dependencies", {}), *d.get("devDependencies", {})})
        except (OSError, ValueError):
            return []
    if path.name == "requirements.txt":
        names = (re.match(r"[A-Za-z0-9_.-]+", ln) for ln in path.read_text(errors="replace").splitlines())
        return sorted({m.group(0) for m in names if m})
    return []


def profile(paths):
    out = []
    for p in map(Path, paths):
        found = [m for m in MANIFESTS if (p / m).exists()]
        out.append({
            "path": str(p), "name": p.resolve().name, "manifests": found,
            "dependencies": sorted({d for m in found for d in deps(p / m)})[:60],
            "context": {c: head(p / c, CONTEXT_CHARS) for c in CONTEXT_FILES if (p / c).exists()},
        })
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--state", default=".repo-fit", help="where verdict history lives (default ./.repo-fit)")
    # Same flag on every subcommand so it works on either side of it. SUPPRESS keeps a value
    # given before the subcommand from being overwritten by the subcommand's default.
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--state", default=argparse.SUPPRESS, help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("profile", parents=[shared]).add_argument("paths", nargs="+")
    s = sub.add_parser("search", parents=[shared])
    s.add_argument("queries", nargs="+")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--min-stars", type=int, default=0)
    s.add_argument("--limit", type=int, default=15)
    f = sub.add_parser("facts", parents=[shared])
    f.add_argument("repos", nargs="+")
    f.add_argument("--personal", action="store_true", help="non-commercial licences are fine")
    r = sub.add_parser("record", parents=[shared])
    r.add_argument("repo")
    r.add_argument("verdict", choices=["High", "Medium", "Skip"])
    r.add_argument("--reason", default="")
    a = ap.parse_args(argv)

    if a.cmd == "profile":
        res = profile(a.paths)
    elif a.cmd == "search":
        res = search(a.queries, a.days, a.min_stars, a.limit)
    elif a.cmd == "facts":
        res = [facts(x, a.state, a.personal) for x in a.repos]
    else:
        res = record(a.repo, a.verdict, a.reason, a.state)
    json.dump(res, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
