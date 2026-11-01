import json
import os
import subprocess

from tests.base import DoxCase, adr, fm
from tests.test_allocate import fake_gh


class LinksRemote(DoxCase):
    def setUp(self):
        super().setUp()
        h7, t7 = adr(7, "seven")
        self.handbook = self.repo("clones/handbook", {
            f"adr/{h7}.md": t7, "rules/platform.md": fm(id="platform", title="P", version="1"),
        }, namespace="handbook", root=".", origin=True)
        os.environ["DOX_CLONES"] = str(self.base / "clones" / "{repo}")

    def api(self, refs, body=""):
        a, ta = adr(1, "x", refs=refs, body=body)
        return self.repo("api", {f"docs/adr/{a}.md": ta, "docs/guide.md": "# g\n"})

    def test_remote_refs_and_urls_against_sibling_clone(self):
        repo = self.api(
            ["handbook:ADR-0007", "handbook:adr-0007-seven", "handbook:rules/platform.md#p4",
             "handbook:ADR-0008", "handbook:missing.md"],
            body="[ok](https://forge.example.org/example-org/handbook/blob/main/adr/adr-0007-seven.md)\n"
                 "[bad](https://forge.example.org/example-org/handbook/blob/main/adr/nope.md)\n"
                 "[pinned](https://forge.example.org/example-org/handbook/blob/0123abcd/gone.md)\n"
                 "[self](https://forge.example.org/example-org/api/blob/main/docs/guide.md)\n"
                 "[elsewhere](https://forge.example.org/someone/else/blob/main/x.md)\n"
                 "`https://forge.example.org/example-org/handbook/blob/main/rules/platform.md`\n"
                 "[branch](https://forge.example.org/example-org/handbook/blob/robin/x/gone.md)\n")
        _, data = self.lint_json(repo)
        self.assertIn("links-remote", data["inactive"])
        fs = self.findings(repo, "links-remote", "--remote")
        msgs = sorted(f["message"] for f in fs)
        self.assertEqual(len(msgs), 3, msgs)
        self.assertIn("refs: 'handbook:ADR-0008' not found on handbook's default branch", msgs)
        self.assertIn("refs: 'handbook:missing.md' not found on handbook's default branch", msgs)
        self.assertTrue(any("adr/nope.md" in m for m in msgs))

    def test_sees_only_the_default_branch(self):
        h8, t8 = adr(8, "unmerged")
        self.write(self.handbook, f"adr/{h8}.md", t8)  # local, not pushed
        repo = self.api(["handbook:ADR-0008"])
        self.assertEqual(len(self.findings(repo, "links-remote", "--remote")), 1)

    def test_without_clone_uses_github_api_and_unreachable_is_exit_3(self):
        del os.environ["DOX_CLONES"]
        repo = self.api(["handbook:ADR-0007"])
        with fake_gh(fail=True):
            code, out, err = self.dox("lint", "--remote", cwd=repo)
        self.assertEqual(code, 3, out + err)


class SchemaFetch(DoxCase):
    def publish(self, tag="v1.0.0"):
        src = self.base / "publish"
        self.write_schema(src)
        subprocess.run(["git", "init", "-q", "-b", "main", str(src)], check=True)
        self.commit(src)
        self.git(src, "tag", tag)
        bare = self.base / "forge" / "example-org" / "doc-schema.git"
        bare.parent.mkdir(parents=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(src), str(bare)], check=True)
        del os.environ["DOX_SCHEMA_PATH"]
        os.environ["DOX_SCHEMA_URL"] = f"file://{self.base}/forge/{{repo}}.git"

    def test_fetch_by_tag_into_cache(self):
        self.publish()
        repo = self.repo("api", {})
        code, data = self.lint_json(repo)
        self.assertEqual(code, 0)
        cached = self.home / ".cache/dox/example-org/doc-schema/v1.0.0/dox.toml"
        self.assertTrue(cached.is_file())
        self.assertFalse((cached.parent / ".git").exists())
        self.assertEqual(data["config"]["schema_dir"]["source"], "fetched")
        self.assertEqual(data["notes"], [])
        # cached: works with the forge gone
        os.environ["DOX_SCHEMA_URL"] = "file:///nonexistent/{repo}.git"
        self.assertEqual(self.dox("lint", cwd=repo)[0], 0)

    def test_unknown_ref_is_config_error(self):
        self.publish()
        repo = self.repo("api", {})
        self.write(repo, ".dox.toml", (repo / ".dox.toml").read_text().replace("v1.0.0", "v9.9.9"))
        code, _, err = self.dox("lint", cwd=repo)
        self.assertEqual(code, 2, err)

    def test_unreachable_forge_is_exit_3(self):
        self.publish()
        os.environ["DOX_SCHEMA_URL"] = "file:///nonexistent/{repo}.git"
        repo = self.repo("api", {})
        code, _, err = self.dox("lint", cwd=repo)
        self.assertEqual(code, 3, err)

    def test_bad_schema_repo_name(self):
        repo = self.repo("api", {})
        self.assertEqual(self.dox("lint", "--dox-schema", "not a repo", cwd=repo)[0], 2)
