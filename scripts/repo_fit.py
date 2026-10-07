#!/usr/bin/env python3
"""repo-fit helper: the deterministic half of the repo-fit skill. Standard library only.

  repo_fit.py profile PATH...            what the projects are made of
  repo_fit.py search QUERY... [--days N] recently pushed repos matching GitHub search queries
  repo_fit.py facts OWNER/REPO...        stars, licence, activity, gate flags, previous verdict
  repo_fit.py record OWNER/REPO VERDICT  remember a verdict so the next run does not flip it

All output is JSON on stdout. Set GITHUB_TOKEN for a higher API rate limit.
"""
import argparse
import base64
import json
import os
import re
import sys
import time
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


# Once GitHub says a quota is spent, calls to that part of the API fail at once until it returns, so a
# run does not make (and wait on) dozens of requests that cannot succeed. Search and core have separate
# quotas. In-process only, so each helper run finds out with one request.
# ponytail: persist to --state if that one request per run ever matters.
_LIMITED = {}  # resource ("core" or "search") -> epoch seconds when the quota returns


def _resets(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%H:%M UTC")


def gh(path, **params):
    res = "search" if path.startswith("/search") else "core"
    if time.time() < _LIMITED.get(res, 0):
        raise ApiError(f"GitHub API 403 for {path} (rate limited, not retried: quota returns {_resets(_LIMITED[res])})")
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
                    reset = e.headers.get("X-RateLimit-Reset") or ""
                    until = int(reset) if reset.isdigit() else 0
                    if until:
                        _LIMITED[res] = until
                    hint = " (rate limited" + ("" if os.environ.get("GITHUB_TOKEN") else ": set GITHUB_TOKEN")
                    hint += (f"; quota returns {_resets(until)}" if until else "") + ")"
                else:  # a proxy or policy block, not a limit: a token will not help, so say what it said
                    try:
                        hint = f" ({json.load(e)['message']})"
                    except (ValueError, KeyError, AttributeError):
                        pass
                    wait = e.headers.get("Retry-After") or ""  # secondary limit: GitHub says how long to back off
                    if wait.isdigit():
                        _LIMITED[res] = time.time() + int(wait)
            raise ApiError(f"GitHub API {e.code} for {path}{hint}")
        except (urllib.error.URLError, TimeoutError) as e:
            if attempt == 2:
                raise ApiError(f"network error for {path}: {getattr(e, 'reason', e)}")


def days_since(stamp, now):
    return (now - datetime.fromisoformat(stamp.replace("Z", "+00:00"))).days


# What a licence class means per way of using the repo: ok, review (judge must address it) or gate
# (hard Skip). Not legal advice. One row per class, so a wrong cell is a one-line fix.
USES = ("personal", "internal", "client", "saas")
DEFAULT_USE = "client"  # the strictest common case; the report must say it was assumed
FIT = {
    "permissive":       dict(zip(USES, ("ok", "ok", "ok", "ok"))),
    "weak-copyleft":    dict(zip(USES, ("ok", "ok", "review", "review"))),
    "strong-copyleft":  dict(zip(USES, ("ok", "ok", "review", "review"))),
    "network-copyleft": dict(zip(USES, ("ok", "review", "review", "review"))),
    "source-available": dict(zip(USES, ("ok", "review", "gate", "gate"))),
    "noncommercial":    dict(zip(USES, ("ok", "gate", "gate", "gate"))),
    "open-core-mixed":  dict(zip(USES, ("review",) * 4)),
    "unknown":          dict(zip(USES, ("gate",) * 4)),
}
CLASS_NOTE = {
    "permissive": "permissive: few conditions beyond keeping the notice",
    "weak-copyleft": "weak copyleft: changes to the library itself usually have to be shared",
    "strong-copyleft": "strong copyleft: code you ship that includes it usually has to be shared under the same licence",
    "network-copyleft": "network copyleft (AGPL): hosting it for others usually triggers the share obligation too",
    "source-available": "source-available: not open source; commercial or competing use is usually restricted",
    "noncommercial": "non-commercial: not for paid work",
    "open-core-mixed": "mixed: different parts of the repo carry different licences; read which part you would use",
    "unknown": "licence not recognised: read the LICENSE file",
}
# Checked in this order, first match wins: a mixed LICENSE can also name a source-available one below.
TEXT_CLASSES = (
    ("open-core-mixed", ("portions of this software are licensed as follows",)),
    ("noncommercial", ("noncommercial", "non-commercial")),
    ("source-available", ("business source license", "server side public license", "elastic license",
                          "functional source license", "commons clause", "sustainable use license")),
)
PERMISSIVE = {"mit", "mit-0", "apache-2.0", "bsd-2-clause", "bsd-3-clause", "isc", "unlicense", "0bsd",
              "cc0-1.0", "zlib", "bsl-1.0", "wtfpl", "python-2.0", "psf-2.0", "blueoak-1.0.0", "cc-by-4.0",
              "upl-1.0", "mulanpsl-2.0", "bsd-3-clause-clear", "artistic-2.0", "postgresql"}


def classify_spdx(spdx):
    s = (spdx or "").lower()
    if re.search(r"(^|-)nc(-|$)|noncommercial", s):
        return "noncommercial"
    if s.startswith("agpl"):
        return "network-copyleft"
    if s.startswith(("lgpl", "mpl", "epl", "cddl", "cecill")):
        return "weak-copyleft"
    if s.startswith("gpl"):
        return "strong-copyleft"
    if s.startswith(("busl", "sspl", "elastic", "fsl")):
        return "source-available"
    return "permissive" if s in PERMISSIVE else "unknown"


def classify_text(text):
    """Class named by a LICENSE file's own words, or None when no known phrase appears."""
    low = (text or "").lower()
    for cls, phrases in TEXT_CLASSES:
        if any(p in low for p in phrases):
            return cls
    return None


def license_text(slug):
    """LICENSE file text as GitHub serves it (one call), or None if unavailable."""
    try:
        return base64.b64decode(gh(f"/repos/{slug}/license")["content"]).decode("utf-8", "replace")[:20000]
    except (ApiError, KeyError, ValueError):
        return None


def gates(repo, now=None, personal=False, use=None, lclass=None):
    """Hard-skip flags for a GitHub API repo object. Empty list means it passes.

    `lclass` is the licence class when the caller already resolved it from the LICENSE text.
    """
    now = now or datetime.now(timezone.utc)
    use = "personal" if personal else (use or DEFAULT_USE)
    flags = []
    if repo.get("archived"):
        flags.append("archived")
    if days_since(repo["pushed_at"], now) > 90:
        flags.append("unmaintained")
    lic = (repo.get("license") or {}).get("spdx_id")
    if not lic:
        flags.append("no-licence")
    else:
        cls = lclass or classify_spdx(lic)
        if FIT[cls][use] == "gate":
            flags.append("licence-unclear" if cls == "unknown" else cls)
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


INSTALL_HOOKS = ("preinstall", "install", "postinstall")


def lookup(path):
    """(data, error). A 404 is an ordinary 'not there', so it carries no error."""
    try:
        return gh(path), None
    except ApiError as e:
        return None, (None if "API 404" in str(e) else str(e))


def extras(r, now=None):
    """Plain observed facts, never a verdict: owner account age and root package.json install hooks.

    None means not checked (no root package.json, bad JSON, or the lookup failed).
    """
    now = now or datetime.now(timezone.utc)
    out, errors = {"owner_age_days": None, "install_scripts": None}, []
    login = (r.get("owner") or {}).get("login")
    if login:
        user, err = lookup(f"/users/{login}")
        errors += [err] if err else []
        if user and user.get("created_at"):
            out["owner_age_days"] = days_since(user["created_at"], now)
    pkg, err = lookup(f"/repos/{r['full_name']}/contents/package.json")
    errors += [err] if err else []
    try:
        scripts = json.loads(base64.b64decode(pkg["content"])).get("scripts", {})
        if isinstance(scripts, dict):
            out["install_scripts"] = [h for h in INSTALL_HOOKS if h in scripts]
    except (TypeError, KeyError, ValueError, AttributeError):
        pass
    if errors:
        out["extras_error"] = "; ".join(errors)
    return out


def facts(slug, state, personal=False, use=None):
    prev = load_history(state).get(slug.lower())
    try:
        r = gh(f"/repos/{slug}")
    except ApiError as e:
        return {"repo": slug, "error": str(e), "unverified": True, "previously": prev}
    use = "personal" if personal else (use or DEFAULT_USE)
    spdx = (r.get("license") or {}).get("spdx_id")
    text = license_text(r["full_name"]) if spdx in ("NOASSERTION", "Other") else None  # only the unclear ones cost a call
    cls = classify_text(text) or classify_spdx(spdx)
    note = CLASS_NOTE[cls]
    if cls == "unknown" and text:
        note += ". LICENSE starts: " + next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:120]
    review = [f"{CLASS_NOTE[cls]} (use: {use})"] if FIT[cls][use] == "review" else []
    flags = gates(r, personal=personal, use=use, lclass=cls)
    return {
        "repo": r["full_name"], "url": r["html_url"], "description": r.get("description"),
        "stars": r["stargazers_count"], "licence": spdx,
        "licence_class": cls, "licence_note": note, "licence_review": review, "use_mode": use,
        "pushed_at": r["pushed_at"], "created_at": r["created_at"], "topics": r.get("topics", []),
        "gates": flags, "previously": prev,
        **({} if flags else extras(r)),  # a repo already Skipped needs no further calls
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
    f.add_argument("--use", choices=USES, help=f"how the repo will be used (default {DEFAULT_USE})")
    f.add_argument("--personal", action="store_true", help="same as --use personal")
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
        res = [facts(x, a.state, a.personal, a.use) for x in a.repos]
    else:
        res = record(a.repo, a.verdict, a.reason, a.state)
    json.dump(res, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
