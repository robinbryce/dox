import datetime
import json
import subprocess
from unittest import mock

from dox import proc
from tests.base import DoxCase, adr, fm

REAL_RUN = proc.run


def fake_gh(pr_paths=(), fail=False, calls=None):
    def run(args, cwd=None, timeout=None, env=None):
        args = [str(a) for a in args]
        if args[0] == "gh":
            if calls is not None:
                calls.append(args)
            if fail:
                return subprocess.CompletedProcess(args, 1, "", "gh: not logged in")
            data = [{"files": [{"path": p} for p in pr_paths]}]
            return subprocess.CompletedProcess(args, 0, json.dumps(data), "")
        return REAL_RUN(args, cwd=cwd, timeout=timeout, env=env)
    return mock.patch("dox.proc.run", run)


def github_origin(case, repo):
    """Point origin's URL at GitHub for slug parsing, but fetch from the bare repo."""
    bare = case.git(repo, "remote", "get-url", "origin")
    case.git(repo, "remote", "set-url", "origin", "git@github.com:example-org/api.git")
    case.git(repo, "config", f"url.{bare}.insteadOf", "git@github.com:example-org/api.git")


class Next(DoxCase):
    def test_offline_counts_working_tree_including_retired_and_untracked(self):
        a, ta = adr(7, "x")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta, "docs/adr/retired/adr-0009-old.md": ta})
        self.write(repo, "docs/adr/adr-0011-draft.md", "x")  # untracked
        code, out, err = self.dox("next", "adr", "--offline", cwd=repo)
        self.assertEqual((code, out.strip()), (0, "adr-0012"), err)
        self.assertIn("offline", err)

    def test_counts_origin_main_beyond_stale_clone(self):
        repo = self.repo("api", {}, origin=True)
        other = self.base / "other"
        subprocess.run(["git", "clone", "-q", self.git(repo, "remote", "get-url", "origin"), str(other)], check=True)
        a, ta = adr(40, "landed")
        self.write(other, f"docs/adr/{a}.md", ta)
        self.commit(other)
        self.git(other, "push", "-q", "origin", "main")
        code, out, err = self.dox("next", "adr", "my-thing", cwd=repo)
        self.assertEqual(out.strip(), "adr-0041-my-thing", err)
        self.assertIn("not on GitHub", err)

    def test_counts_open_pull_requests(self):
        repo = self.repo("api", {}, origin=True)
        github_origin(self, repo)
        calls = []
        with fake_gh(["docs/adr/adr-0055-in-review/README.md", "docs/other.md"], calls=calls):
            code, out, err = self.dox("next", "adr", "--json", cwd=repo)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(out)["id"], "adr-0056")
        self.assertIn("example-org/api", calls[0])

    def test_unreachable_is_exit_3(self):
        repo = self.repo("api", {}, origin=True)
        github_origin(self, repo)
        with fake_gh(fail=True):
            code, _, err = self.dox("next", "adr", cwd=repo)
        self.assertEqual(code, 3, err)
        self.git(repo, "config", "--unset", "url." + str(self.base / "api.git") + ".insteadOf")
        self.git(repo, "remote", "set-url", "origin", str(self.base / "missing.git"))
        code, _, err = self.dox("next", "adr", cwd=repo)
        self.assertEqual(code, 3, err)

    def test_month_cohort_and_archive(self):
        repo = self.repo("work", {
            "plans/plan-2611-02-a.md": "x",
            "archive/2611/plan-2611-04-b/README.md": "x",
            "plans/plan-2610-09-c.md": "x",
            "archive/2510/plan-0003-legacy.md": "x",
        }, namespace="work", root=".", types=["plan"])
        code, out, _ = self.dox("next", "plan", "--offline", "--date", "2026-11-03", cwd=repo)
        self.assertEqual(out.strip(), "plan-2611-05")
        code, out, _ = self.dox("next", "plan", "--offline", "--date", "2026-12-01", cwd=repo)
        self.assertEqual(out.strip(), "plan-2612-01")

    def test_author_chosen_ids_and_foreign_types(self):
        repo = self.repo("api", {})
        self.assertEqual(self.dox("next", "rules", "--offline", cwd=repo)[0], 2)
        self.assertEqual(self.dox("next", "plan", "--offline", cwd=repo)[0], 2)
        self.assertEqual(self.dox("next", "memo", "--offline", cwd=repo)[0], 2)


class New(DoxCase):
    def test_file_from_template_validates(self):
        a, ta = adr(3, "x")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta})
        code, out, err = self.dox("new", "adr", "Grant Format", "--offline", "--date", "2026-11-01", cwd=repo)
        self.assertEqual(code, 0, err)
        path = repo / "docs/adr/adr-0004-grant-format.md"
        self.assertEqual(out.strip(), str(path))
        text = path.read_text()
        self.assertIn("id: adr-0004-grant-format", text)
        self.assertIn("created: 2026-11-01", text)
        self.assertEqual(self.findings(repo, "schema"), [])

    def test_dir_plan_and_refuse_overwrite(self):
        repo = self.repo("work", {}, namespace="work", root=".", types=["plan"])
        code, out, err = self.dox("new", "plan", "dox-core", "--dir", "--offline", "--date", "2026-11-01", cwd=repo)
        self.assertEqual(code, 0, err)
        d = repo / "plans/plan-2611-01-dox-core"
        self.assertTrue((d / "README.md").is_file())
        self.assertEqual((d / "handoff.md").read_text(), "# Handoff for plan-2611-01-dox-core\n")
        self.assertEqual(self.findings(repo), [])
        # an untracked plan already holds 01, so the next is 02
        code, out, _ = self.dox("new", "plan", "dox-core", "--dir", "--offline", "--date", "2026-11-01", cwd=repo)
        self.assertIn("plan-2611-02-dox-core", out)

    def test_author_chosen_id(self):
        self.schema_variant(lambda t: t.replace('status = "none"', 'status = "none"\ntemplate = "adr.md"'))
        repo = self.repo("api", {})
        code, out, err = self.dox("new", "rules", "rules", "--offline", cwd=repo)
        self.assertEqual(code, 0, err)
        self.assertTrue(out.strip().endswith("docs/rules.md"))

    def test_rules_layout_refuses_dir(self):
        repo = self.repo("api", {})
        self.assertEqual(self.dox("new", "rules", "x", "--dir", "--offline", cwd=repo)[0], 2)


class NewFallback(DoxCase):
    def test_fallback_type_starts_at_docs_root(self):
        self.schema_variant(lambda t: t.replace("fallback = true", 'fallback = true\ntemplate = "adr.md"'))
        repo = self.repo("api", {})
        code, out, err = self.dox("new", "note", "architecture", "--offline", cwd=repo)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.strip(), str(repo / "docs/architecture.md"))
