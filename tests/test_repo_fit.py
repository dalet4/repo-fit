import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import repo_fit  # noqa: E402

NOW = datetime(2026, 10, 4, tzinfo=timezone.utc)


def repo(**kw):
    base = {"archived": False, "pushed_at": "2026-10-01T00:00:00Z", "created_at": "2025-01-01T00:00:00Z",
            "stargazers_count": 900, "license": {"spdx_id": "MIT"}, "owner": {"type": "Organization"}}
    base.update(kw)
    return base


class Gates(unittest.TestCase):
    def test_clean_repo_passes(self):
        self.assertEqual(repo_fit.gates(repo(), NOW), [])

    def test_each_gate(self):
        cases = {
            "archived": repo(archived=True),
            "unmaintained": repo(pushed_at="2026-05-01T00:00:00Z"),
            "no-licence": repo(license=None),
            "licence-unclear": repo(license={"spdx_id": "NOASSERTION"}),
            "noncommercial": repo(license={"spdx_id": "PolyForm-Noncommercial-1.0.0"}),
            "too-new": repo(created_at="2026-10-01T00:00:00Z", stargazers_count=1, owner={"type": "User"}),
        }
        for flag, r in cases.items():
            self.assertIn(flag, repo_fit.gates(r, NOW), flag)

    def test_cc_by_nc_is_noncommercial_but_gpl_is_not(self):
        self.assertIn("noncommercial", repo_fit.gates(repo(license={"spdx_id": "CC-BY-NC-4.0"}), NOW))
        self.assertEqual(repo_fit.gates(repo(license={"spdx_id": "GPL-3.0"}), NOW), [])

    def test_personal_allows_noncommercial(self):
        r = repo(license={"spdx_id": "CC-BY-NC-4.0"})
        self.assertEqual(repo_fit.gates(r, NOW, personal=True), [])

    def test_young_repo_with_traction_or_org_is_fine(self):
        young = {"created_at": "2026-10-01T00:00:00Z"}
        self.assertEqual(repo_fit.gates(repo(stargazers_count=500, owner={"type": "User"}, **young), NOW), [])
        self.assertEqual(repo_fit.gates(repo(stargazers_count=1, **young), NOW), [])


class History(unittest.TestCase):
    def test_later_verdict_wins_and_lookup_is_case_insensitive(self):
        with tempfile.TemporaryDirectory() as d:
            repo_fit.record("Owner/Repo", "Skip", "no-fit", d, today="2026-10-03")
            repo_fit.record("Owner/Repo", "High", "now serves X", d, today="2026-10-04")
            got = repo_fit.load_history(d)["owner/repo"]
            self.assertEqual((got["verdict"], got["date"]), ("High", "2026-10-04"))

    def test_missing_history_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(repo_fit.load_history(d), {})


class Profile(unittest.TestCase):
    def test_reads_manifests_deps_and_context(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)
            (p / "package.json").write_text(json.dumps({"dependencies": {"next": "16"}, "devDependencies": {"vitest": "1"}}))
            (p / "requirements.txt").write_text("requests>=2\n# comment\nsupabase==2.0\n")
            (p / "README.md").write_text("# Demo\nA dashboard.")
            got = repo_fit.profile([d])[0]
            self.assertEqual(got["manifests"], ["package.json", "requirements.txt"])
            self.assertEqual(got["dependencies"], ["next", "requests", "supabase", "vitest"])
            self.assertIn("dashboard", got["context"]["README.md"])


class Retry(unittest.TestCase):
    def run_gh(self, side_effects):
        from unittest import mock
        with mock.patch("urllib.request.urlopen", side_effect=side_effects) as m:
            try:
                return repo_fit.gh("/repos/a/b"), m.call_count
            except repo_fit.ApiError as e:
                return e, m.call_count

    def test_one_timeout_is_retried(self):
        import io
        ok = io.BytesIO(b'{"full_name": "a/b"}')
        got, calls = self.run_gh([TimeoutError(), ok])
        self.assertEqual((got, calls), ({"full_name": "a/b"}, 2))

    def test_two_timeouts_give_an_api_error_not_a_crash(self):
        got, calls = self.run_gh([TimeoutError(), TimeoutError()])
        self.assertIsInstance(got, repo_fit.ApiError)
        self.assertEqual(calls, 2)

    def test_http_errors_are_not_retried(self):
        import urllib.error
        err = urllib.error.HTTPError("u", 404, "nf", {}, None)
        got, calls = self.run_gh([err, err])
        self.assertIsInstance(got, repo_fit.ApiError)
        self.assertEqual(calls, 1)

    def http_403(self, headers, body=b""):
        import io
        import urllib.error
        err = urllib.error.HTTPError("u", 403, "forbidden", headers, io.BytesIO(body))
        return str(self.run_gh([err])[0])

    def test_403_with_no_quota_left_suggests_a_token(self):
        self.assertIn("set GITHUB_TOKEN", self.http_403({"X-RateLimit-Remaining": "0"}))

    def test_403_that_is_not_a_rate_limit_says_what_the_api_said(self):
        msg = self.http_403({"X-RateLimit-Remaining": "57"}, b'{"message": "path not available"}')
        self.assertIn("path not available", msg)
        self.assertNotIn("GITHUB_TOKEN", msg)

    def test_403_with_unreadable_body_does_not_crash(self):
        self.assertNotIn("GITHUB_TOKEN", self.http_403({}, b"not json"))


class StateFlag(unittest.TestCase):
    def run_main(self, argv, cwd=None):
        import contextlib
        import io
        import os
        old = os.getcwd()
        if cwd:
            os.chdir(cwd)
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                repo_fit.main(argv)
        finally:
            os.chdir(old)

    def test_state_works_before_and_after_the_subcommand(self):
        with tempfile.TemporaryDirectory() as before, tempfile.TemporaryDirectory() as after:
            self.run_main(["--state", before, "record", "a/b", "Skip"])
            self.run_main(["record", "c/d", "Skip", "--state", after])
            self.assertIn("a/b", {r["repo"] for r in repo_fit.load_history(before).values()})
            self.assertIn("c/d", {r["repo"] for r in repo_fit.load_history(after).values()})

    def test_state_before_is_not_overwritten_by_subcommand_default(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as cwd:
            self.run_main(["--state", d, "record", "a/b", "Skip"], cwd=cwd)
            self.assertFalse((Path(cwd) / ".repo-fit").exists())

    def test_default_state_is_dot_repo_fit_in_cwd(self):
        with tempfile.TemporaryDirectory() as cwd:
            self.run_main(["record", "a/b", "Skip"], cwd=cwd)
            self.assertTrue((Path(cwd) / ".repo-fit" / "history.jsonl").exists())


class ContextLimit(unittest.TestCase):
    def test_long_instruction_file_is_kept_past_the_old_1500_cut(self):
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "CLAUDE.md").write_text("x" * 5000 + "END")
            ctx = repo_fit.profile([d])[0]["context"]["CLAUDE.md"]
            self.assertEqual(len(ctx), 5003)


def api_repo(slug="o/r", **kw):
    """A GitHub /repos object with everything facts() reads."""
    base = repo(full_name=slug, html_url="https://github.com/" + slug, description="d", topics=["t"])
    base.update(kw)
    return base


class Search(unittest.TestCase):
    def run_search(self, responses, queries=("a b", "c d"), **kw):
        """Run search() with gh() patched. responses: one dict (or ApiError) per gh call, in order."""
        from unittest import mock
        calls = []

        def fake_gh(path, **params):
            calls.append((path, params))
            r = responses[len(calls) - 1]
            if isinstance(r, Exception):
                raise r
            return r

        args = {"days": 7, "min_stars": 5, "limit": 15}
        args.update(kw)
        with mock.patch.object(repo_fit, "gh", side_effect=fake_gh):
            return repo_fit.search(list(queries), **args), calls

    def item(self, slug, stars=100):
        return {"full_name": slug, "stargazers_count": stars, "description": "x", "pushed_at": "2026-10-01T00:00:00Z"}

    def test_builds_the_query_with_window_star_floor_sort_and_limit(self):
        from datetime import date, timedelta
        _, calls = self.run_search([{"items": []}], queries=["mcp server"], days=10, min_stars=20, limit=3)
        path, params = calls[0]
        self.assertEqual(path, "/search/repositories")
        self.assertEqual((params["sort"], params["order"], params["per_page"]), ("updated", "desc", 3))
        m = __import__("re").fullmatch(r"mcp server pushed:>(\d{4}-\d{2}-\d{2}) stars:>=20", params["q"])
        self.assertIsNotNone(m, params["q"])
        age = (datetime.now(timezone.utc).date() - date.fromisoformat(m.group(1))).days
        self.assertIn(age, (10, 11))  # 11 only if the clock crossed midnight during the test

    def test_candidates_carry_the_fields_the_skill_needs_and_the_query_that_found_them(self):
        out, _ = self.run_search([{"items": [self.item("a/one", 42)]}], queries=["q1"])
        self.assertEqual(out["errors"], [])
        self.assertEqual(out["candidates"], [{"repo": "a/one", "stars": 42, "description": "x",
                                              "pushed_at": "2026-10-01T00:00:00Z", "query": "q1"}])

    def test_a_repo_found_by_two_queries_appears_once_under_the_first(self):
        out, _ = self.run_search([{"items": [self.item("a/one"), self.item("a/two")]},
                                  {"items": [self.item("a/two"), self.item("a/three")]}])
        self.assertEqual([c["repo"] for c in out["candidates"]], ["a/one", "a/two", "a/three"])
        self.assertEqual(out["candidates"][1]["query"], "a b")

    def test_a_failing_query_is_reported_and_the_rest_still_run(self):
        out, calls = self.run_search([repo_fit.ApiError("GitHub API 500 for /search/repositories"),
                                      {"items": [self.item("a/ok")]}])
        self.assertEqual(len(calls), 2)
        self.assertEqual([c["repo"] for c in out["candidates"]], ["a/ok"])
        self.assertEqual(len(out["errors"]), 1)
        self.assertIn("500", out["errors"][0])

    def test_no_results_is_an_empty_list_not_an_error(self):
        out, _ = self.run_search([{"items": []}, {"items": []}])
        self.assertEqual(out, {"candidates": [], "errors": []})


class Facts(unittest.TestCase):
    def run_facts(self, slug="o/r", state=None, personal=False, api=None):
        from unittest import mock
        api = api_repo() if api is None else api
        with tempfile.TemporaryDirectory() as d:
            target = state or d
            quiet = [{"created_at": "2015-01-01T00:00:00Z"}, repo_fit.ApiError("GitHub API 404 for /contents")]
            with mock.patch.object(repo_fit, "gh", side_effect=[api, *quiet]):  # a list item that is an exception is raised
                return repo_fit.facts(slug, target, personal)

    def test_reports_the_fields_and_an_empty_gate_list_for_a_clean_repo(self):
        got = self.run_facts(api=api_repo("o/r", stargazers_count=1234))
        self.assertEqual((got["repo"], got["stars"], got["licence"], got["gates"], got["previously"]),
                         ("o/r", 1234, "MIT", [], None))
        self.assertEqual(got["url"], "https://github.com/o/r")
        self.assertEqual(got["topics"], ["t"])

    def test_a_repo_with_no_licence_reports_none_and_the_gate(self):
        got = self.run_facts(api=api_repo(license=None))
        self.assertIsNone(got["licence"])
        self.assertIn("no-licence", got["gates"])

    def test_gates_are_applied_to_what_the_api_returned(self):
        got = self.run_facts(api=api_repo(archived=True, pushed_at="2020-01-01T00:00:00Z"))
        self.assertEqual(sorted(got["gates"]), ["archived", "unmaintained"])

    def test_personal_flag_reaches_the_gates(self):
        nc = api_repo(license={"spdx_id": "CC-BY-NC-4.0"})
        self.assertIn("noncommercial", self.run_facts(api=nc)["gates"])
        self.assertNotIn("noncommercial", self.run_facts(api=nc, personal=True)["gates"])

    def test_an_api_failure_is_marked_unverified_and_keeps_the_earlier_verdict(self):
        with tempfile.TemporaryDirectory() as d:
            repo_fit.record("o/r", "Skip", "wrong-stack", d, today="2026-09-01")
            got = self.run_facts(state=d, api=repo_fit.ApiError("GitHub API 404 for /repos/o/r"))
        self.assertTrue(got["unverified"])
        self.assertIn("404", got["error"])
        self.assertEqual(got["previously"]["verdict"], "Skip")
        self.assertNotIn("stars", got)

    def test_the_earlier_verdict_is_found_whatever_the_case_of_the_slug(self):
        with tempfile.TemporaryDirectory() as d:
            repo_fit.record("Owner/Repo", "Medium", "watch", d, today="2026-09-01")
            got = self.run_facts(slug="owner/repo", state=d, api=api_repo("Owner/Repo"))
        self.assertEqual(got["previously"]["verdict"], "Medium")
        self.assertEqual(got["previously"]["date"], "2026-09-01")


BUSL_TEXT = "License text copyright (c) 2020 MariaDB\n\"Business Source License\" is a trademark of MariaDB\n"
N8N_TEXT = ("# License\n\nPortions of this software are licensed as follows:\n\n"
            "- Content under the Sustainable Use License\n")


class LicenceFit(unittest.TestCase):
    def facts_for(self, spdx, use=None, personal=False, text=None, text_error=None):
        """Run facts() on a repo with this SPDX id; `text` is what the LICENSE endpoint serves.

        Returns (result, paths of every GitHub call made).
        """
        from base64 import b64encode
        from unittest import mock
        calls = []

        def fake(path, **kw):
            calls.append(path)
            if path.endswith("/license"):
                if text_error or text is None:
                    raise text_error or repo_fit.ApiError("GitHub API 404 for " + path)
                return {"content": b64encode(text.encode()).decode()}
            if path.startswith("/users/"):
                return {"created_at": "2015-01-01T00:00:00Z"}
            if path.endswith("package.json"):
                raise repo_fit.ApiError("GitHub API 404 for " + path)
            return api_repo(license={"spdx_id": spdx} if spdx else None)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(repo_fit, "gh", side_effect=fake):
            return repo_fit.facts("o/r", d, personal, use), calls

    def test_each_class_in_each_use(self):
        gate, review, ok = "gate", "review", "ok"
        expected = {  # spdx: (personal, internal, client, saas)
            "MIT": (ok, ok, ok, ok),
            "LGPL-3.0": (ok, ok, review, review),
            "GPL-3.0": (ok, ok, review, review),
            "AGPL-3.0": (ok, review, review, review),
            "BUSL-1.1": (ok, review, gate, gate),
            "CC-BY-NC-4.0": (ok, gate, gate, gate),
        }
        for spdx, levels in expected.items():
            for use, level in zip(repo_fit.USES, levels):
                got, _ = self.facts_for(spdx, use=use)
                seen = gate if got["gates"] and "licence-unclear" not in got["gates"] else (review if got["licence_review"] else ok)
                self.assertEqual(seen, level, f"{spdx} for {use}")

    def test_a_clean_licence_costs_no_extra_api_call(self):
        got, calls = self.facts_for("MIT")
        self.assertNotIn("/repos/o/r/license", calls)
        self.assertEqual((got["licence_class"], got["use_mode"]), ("permissive", "client"))

    def test_noassertion_with_business_source_text_is_source_available_and_gated_for_clients(self):
        got, calls = self.facts_for("NOASSERTION", text=BUSL_TEXT)
        self.assertIn("/repos/o/r/license", calls)
        self.assertEqual((got["licence_class"], got["gates"]), ("source-available", ["source-available"]))

    def test_a_mixed_licence_file_is_open_core_even_when_it_also_names_a_source_available_licence(self):
        got, _ = self.facts_for("NOASSERTION", text=N8N_TEXT)
        self.assertEqual((got["licence_class"], got["gates"]), ("open-core-mixed", []))
        self.assertEqual(len(got["licence_review"]), 1)

    def test_unrecognised_text_is_unknown_and_quotes_the_first_line(self):
        got, _ = self.facts_for("Other", text="\n\nAcme Public Licence v9\nDo what you like.")
        self.assertEqual((got["licence_class"], got["gates"]), ("unknown", ["licence-unclear"]))
        self.assertIn("Acme Public Licence v9", got["licence_note"])

    def test_no_licence_endpoint_answer_is_unknown_not_a_crash(self):
        got, _ = self.facts_for("NOASSERTION", text_error=repo_fit.ApiError("GitHub API 404 for /repos/o/r/license"))
        self.assertEqual((got["licence_class"], got["gates"]), ("unknown", ["licence-unclear"]))

    def test_personal_flag_is_the_same_as_use_personal(self):
        a, _ = self.facts_for("CC-BY-NC-4.0", personal=True)
        b, _ = self.facts_for("CC-BY-NC-4.0", use="personal")
        self.assertEqual((a["gates"], a["use_mode"]), (b["gates"], b["use_mode"]))
        self.assertEqual(a["gates"], [])

    def test_boost_is_not_business_source_and_lgpl_is_not_gpl(self):
        self.assertEqual(repo_fit.classify_spdx("BSL-1.0"), "permissive")
        self.assertEqual(repo_fit.classify_spdx("LGPL-2.1"), "weak-copyleft")
        self.assertEqual(repo_fit.classify_spdx("GPL-2.0-or-later"), "strong-copyleft")


class Extras(unittest.TestCase):
    OWNER = {"type": "Organization", "login": "o"}

    def run_extras(self, user=None, pkg=None):
        """extras() on a repo whose user and package.json lookups answer with these (or raise if an exception)."""
        from base64 import b64encode
        from unittest import mock
        answers = {"/users/o": user, "/repos/o/r/contents/package.json":
                   {"content": b64encode(pkg.encode()).decode()} if isinstance(pkg, str) else pkg}

        def fake(path, **kw):
            got = answers[path]
            if isinstance(got, Exception):
                raise got
            return got

        with mock.patch.object(repo_fit, "gh", side_effect=fake):
            return repo_fit.extras(api_repo(owner=self.OWNER), NOW)

    NOT_FOUND = repo_fit.ApiError("GitHub API 404 for /x")

    def test_owner_age_is_days_since_the_account_was_created(self):
        got = self.run_extras({"created_at": "2026-09-24T00:00:00Z"}, self.NOT_FOUND)
        self.assertEqual(got["owner_age_days"], 10)

    def test_install_hooks_are_listed_in_lifecycle_order_and_other_scripts_ignored(self):
        pkg = '{"scripts": {"test": "x", "postinstall": "a", "preinstall": "b", "build": "c"}}'
        got = self.run_extras({"created_at": "2020-01-01T00:00:00Z"}, pkg)
        self.assertEqual(got["install_scripts"], ["preinstall", "postinstall"])

    def test_a_package_json_with_no_hooks_is_empty_not_unchecked(self):
        self.assertEqual(self.run_extras({"created_at": "2020-01-01T00:00:00Z"}, '{"scripts": {"test": "x"}}')["install_scripts"], [])
        self.assertEqual(self.run_extras({"created_at": "2020-01-01T00:00:00Z"}, '{"name": "x"}')["install_scripts"], [])

    def test_no_package_json_is_unchecked_and_not_an_error(self):
        got = self.run_extras({"created_at": "2020-01-01T00:00:00Z"}, self.NOT_FOUND)
        self.assertIsNone(got["install_scripts"])
        self.assertNotIn("extras_error", got)

    def test_unreadable_package_json_is_unchecked(self):
        for bad in ("not json", '["a"]', '{"scripts": "x"}'):
            self.assertIsNone(self.run_extras({"created_at": "2020-01-01T00:00:00Z"}, bad)["install_scripts"], bad)

    def test_a_failed_lookup_is_unchecked_and_says_why(self):
        limited = repo_fit.ApiError("GitHub API 403 for /users/o (rate limited: set GITHUB_TOKEN)")
        got = self.run_extras(limited, self.NOT_FOUND)
        self.assertIsNone(got["owner_age_days"])
        self.assertIn("rate limited", got["extras_error"])

    def test_a_gated_repo_makes_no_extra_calls(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as d, mock.patch.object(repo_fit, "gh", side_effect=[api_repo(archived=True)]) as m:
            got = repo_fit.facts("o/r", d)
        self.assertEqual((m.call_count, "owner_age_days" in got), (1, False))

    def test_a_clean_repo_reports_both_facts_through_facts(self):
        from unittest import mock
        pkg = {"content": __import__("base64").b64encode(b'{"scripts": {"install": "x"}}').decode()}
        answers = [api_repo(owner=self.OWNER), {"created_at": "2026-10-03T00:00:00Z"}, pkg]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(repo_fit, "gh", side_effect=answers):
            got = repo_fit.facts("o/r", d)
        self.assertEqual((got["install_scripts"], got["gates"]), (["install"], []))
        self.assertIsInstance(got["owner_age_days"], int)


if __name__ == "__main__":
    unittest.main()
