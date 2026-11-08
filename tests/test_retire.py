import datetime
import json

import yaml

from tests.base import DoxCase, adr, fm

TODAY = datetime.date.today().isoformat()


def front(path):
    text = path.read_text()
    return yaml.safe_load(text.split("---\n")[1]), text


class Retire(DoxCase):
    def setUp(self):
        super().setUp()
        self.schema_variant(lambda t: t.replace(
            "[types.adr.links]", '[types.adr.retire]\nby = "superseded"\n[types.adr.links]'
        ).replace('superseded-by = "replaced-by"\nrefs = "cites"\n\n[types.rules]',
                  'superseded-by = "replaced-by"\nrefs = "cites"\nabsorbed-into = "absorbed-into"\n\n[types.rules]'))

    def corpus(self):
        old, told = adr(1, "old", body="See [new](adr-0002-new.md) and [guide](../guide.md#top).\n")
        new, tnew = adr(2, "new", refs=["EX-1"])
        return {
            f"docs/adr/{old}.md": told,
            f"docs/adr/{new}.md": tnew,
            "docs/guide.md": fm(id="guide", title="Guide", status="current", created="2026-01-01")
            + "Read [the old one](adr/adr-0001-old.md#context) and [new](adr/adr-0002-new.md).\n",
            "README.md": "Start at [old](docs/adr/adr-0001-old.md).\n",
        }

    def test_retire_by_successor(self):
        repo = self.repo("api", self.corpus())
        code, out, err = self.dox("retire", "ADR-0001", "--by", "ADR-0002", cwd=repo)
        self.assertEqual(code, 0, out + err)
        self.assertFalse((repo / "docs/adr/adr-0001-old.md").exists())
        moved = repo / "docs/adr/retired/adr-0001-old.md"
        data, text = front(moved)
        self.assertEqual(data["status"], "superseded")
        self.assertEqual(data["superseded-by"], "adr-0002-new")
        self.assertIn("[new](../adr-0002-new.md)", text)        # its own links follow it
        self.assertIn("[guide](../../guide.md#top)", text)
        succ, _ = front(repo / "docs/adr/adr-0002-new.md")
        self.assertEqual(succ["supersedes"], ["adr-0001-old"])
        self.assertEqual(succ["refs"], ["EX-1"])                # other fields untouched
        guide = (repo / "docs/guide.md").read_text()
        self.assertIn("[the old one](adr/retired/adr-0001-old.md#context)", guide)
        self.assertIn("[new](adr/adr-0002-new.md)", guide)
        self.assertIn("(docs/adr/retired/adr-0001-old.md)", (repo / "README.md").read_text())
        self.assertIn("docs/adr/adr-0001-old.md\tdocs/adr/retired/adr-0001-old.md", out)
        self.assertEqual(self.findings(repo), [])
        self.assertIn("adr-0001-old", self.git(repo, "diff", "--cached", "--name-status", "-M"))

    def test_backlinks_survive_the_move(self):
        repo = self.repo("api", self.corpus())
        before = json.loads(self.dox("graph", "--json", cwd=repo)[1])["backlinks"]["adr-0001-old"]
        self.dox("retire", "adr-0001-old", "--by", "adr-0002-new", cwd=repo)
        g = json.loads(self.dox("graph", "--json", cwd=repo)[1])
        self.assertEqual(sorted(set(before) | {"adr-0002-new"}), sorted(g["backlinks"]["adr-0001-old"]))
        self.assertEqual(self.findings(repo, "links-local"), [])

    def test_absorbed_keeps_status_without_a_retire_status(self):
        repo = self.repo("api", self.corpus())
        code, out, err = self.dox("retire", "ADR-0001", "--absorbed-into", "guide", cwd=repo)
        self.assertEqual(code, 0, out + err)
        data, _ = front(repo / "docs/adr/retired/adr-0001-old.md")
        self.assertEqual(data["status"], "accepted")
        self.assertEqual(data["absorbed-into"], ["guide"])
        self.assertNotIn("superseded-by", data)

    def test_dry_run_changes_nothing(self):
        repo = self.repo("api", self.corpus())
        before = {p: p.read_text() for p in repo.rglob("*.md")}
        code, out, err = self.dox("retire", "ADR-0001", "--by", "ADR-0002", "--dry-run", cwd=repo)
        self.assertEqual(code, 0, err)
        self.assertIn("would move", out)
        self.assertEqual({p: p.read_text() for p in repo.rglob("*.md")}, before)

    def test_refusals(self):
        repo = self.repo("api", self.corpus())
        self.assertEqual(self.dox("retire", "ADR-0009", "--by", "ADR-0002", cwd=repo)[0], 2)
        self.assertEqual(self.dox("retire", "ADR-0001", "--by", "ADR-0009", cwd=repo)[0], 2)
        self.assertEqual(self.dox("retire", "ADR-0001", cwd=repo)[0], 2)
        self.assertEqual(self.dox("retire", "guide", "--by", "ADR-0002", cwd=repo)[0], 2)  # no retired_dir
        self.dox("retire", "ADR-0001", "--by", "ADR-0002", cwd=repo)
        self.assertEqual(self.dox("retire", "ADR-0001", "--by", "ADR-0002", cwd=repo)[0], 2)  # already

    def test_directory_document_and_status_override(self):
        files = self.corpus()
        _, t3 = adr(3, "big", body="Part: [appendix](appendix.md); up: [guide](../../guide.md).\n")
        files["docs/adr/adr-0003-big/README.md"] = t3
        files["docs/adr/adr-0003-big/appendix.md"] = "Back to [guide](../../guide.md).\n"
        files["docs/guide.md"] += "Big: [b](adr/adr-0003-big/) and [a](adr/adr-0003-big/appendix.md).\n"
        repo = self.repo("api", files)
        code, out, err = self.dox("retire", "ADR-0003", "--by", "ADR-0002", "--status", "rejected", cwd=repo)
        self.assertEqual(code, 0, out + err)
        d = repo / "docs/adr/retired/adr-0003-big"
        data, text = front(d / "README.md")
        self.assertEqual(data["status"], "rejected")
        self.assertIn("[appendix](appendix.md)", text)
        self.assertIn("[guide](../../../guide.md)", text)
        self.assertIn("[guide](../../../guide.md)", (d / "appendix.md").read_text())
        g = (repo / "docs/guide.md").read_text()
        self.assertIn("[b](adr/retired/adr-0003-big/)", g)
        self.assertIn("[a](adr/retired/adr-0003-big/appendix.md)", g)
        self.assertEqual(self.findings(repo), [])
