import json

from tests.base import DoxCase, adr, fm


def corpus():
    old, told = adr(1, "old", status="superseded", superseded_by="ADR-0002")
    mid, tmid = adr(2, "mid", status="superseded", supersedes=["ADR-0001"], superseded_by="ADR-0003")
    new, tnew = adr(3, "new", supersedes=["ADR-0002"], refs=["handbook:ADR-0007", "EX-1"])
    lone, tlone = adr(4, "lone", body="Background in [old](retired/adr-0001-old.md).\n")
    return {
        f"docs/adr/retired/{old}.md": told,
        f"docs/adr/retired/{mid}.md": tmid,
        f"docs/adr/{new}.md": tnew,
        f"docs/adr/{lone}.md": tlone,
        "docs/guide.md": fm(id="guide", title="Guide | how", status="current", created="2026-01-01")
        + "See [ADR 3](adr/adr-0003-new.md).\n",
    }


class Graph(DoxCase):
    def test_graph_json(self):
        repo = self.repo("api", corpus())
        code, out, err = self.dox("graph", "--json", cwd=repo)
        self.assertEqual(code, 0, err)
        g = json.loads(out)
        ids = [n["id"] for n in g["nodes"]]
        self.assertIn("handbook:ADR-0007", ids)
        self.assertEqual(g["chains"], [["adr-0001-old", "adr-0002-mid", "adr-0003-new"]])
        self.assertEqual(g["backlinks"]["adr-0003-new"], ["adr-0002-mid", "guide"])
        self.assertEqual(g["orphans"], ["adr-0004-lone", "guide"])
        into = [(e["from"], e["to"]) for e in g["into_retired"]]
        self.assertEqual(into, [("adr-0004-lone", "adr-0001-old")])

    def test_mermaid_and_dot_are_deterministic(self):
        repo = self.repo("api", corpus())
        m1 = self.dox("graph", "--mermaid", cwd=repo)[1]
        m2 = self.dox("graph", "--mermaid", cwd=repo)[1]
        self.assertEqual(m1, m2)
        self.assertTrue(m1.startswith("graph LR\n"))
        self.assertIn("-->|replaces|", m1)
        d = self.dox("graph", "--dot", cwd=repo)[1]
        self.assertTrue(d.startswith("digraph dox {"))
        self.assertIn('[label="replaced-by"]', d)


class Index(DoxCase):
    def enable(self, digest=True):
        def edit(t):
            t = t.replace('index-fresh = "off"', 'index-fresh = "error"')
            if digest:
                t += '\n[index]\ndigest = "digest.md"\n'
            return t
        self.schema_variant(edit)

    def test_index_writes_blocks_and_keeps_author_text(self):
        self.enable()
        files = corpus()
        files["docs/adr/README.md"] = "# Decisions\n\nHand-written intro.\n"
        repo = self.repo("api", files)
        fs = self.findings(repo, "index-fresh")
        self.assertEqual(sorted(f["path"] for f in fs), ["docs/adr/README.md", "docs/digest.md"])
        code, out, _ = self.dox("index", "--check", cwd=repo)
        self.assertEqual(code, 1)
        code, out, _ = self.dox("index", cwd=repo)
        self.assertEqual(out.split(), ["docs/adr/README.md", "docs/digest.md"])
        readme = (repo / "docs/adr/README.md").read_text()
        self.assertTrue(readme.startswith("# Decisions\n\nHand-written intro.\n\n<!-- dox:index:begin -->"))
        self.assertIn("| [adr-0003-new](adr-0003-new.md) | ADR 3 | accepted |", readme)
        self.assertNotIn("adr-0001-old", readme)  # retired dir is not a live dir
        digest = (repo / "docs/digest.md").read_text()
        self.assertIn("- [adr-0003-new](adr/adr-0003-new.md): ADR 3 (accepted)", digest)
        self.assertIn("Guide \\| how", digest)
        self.assertNotIn("adr-0002-mid", digest)
        self.assertEqual(self.findings(repo, "index-fresh"), [])
        self.assertEqual(self.dox("index", cwd=repo)[1], "")  # idempotent
        # a new ADR makes it stale
        a, ta = adr(5, "later")
        self.write(repo, f"docs/adr/{a}.md", ta)
        fs = self.findings(repo, "index-fresh")
        self.assertEqual([f["message"] for f in fs][0], "generated index is stale")

    def test_index_links_do_not_hide_orphans(self):
        self.enable(digest=False)
        repo = self.repo("api", corpus())
        self.dox("index", cwd=repo)
        g = json.loads(self.dox("graph", cwd=repo)[1])
        self.assertIn("adr-0004-lone", g["orphans"])
        self.assertEqual(self.findings(repo, "links-local"), [])


class IndexOnlyWhereDocsLive(DoxCase):
    def test_no_index_demanded_for_absent_directory(self):
        self.schema_variant(lambda t: t.replace('index-fresh = "off"', 'index-fresh = "error"'))
        repo = self.repo("api", {"docs/guide.md": "# g\n"})
        self.assertEqual(self.findings(repo, "index-fresh"), [])
