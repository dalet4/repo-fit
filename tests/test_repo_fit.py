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


if __name__ == "__main__":
    unittest.main()
