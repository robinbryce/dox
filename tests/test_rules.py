import os
import textwrap

from tests.base import DoxCase, fm

RULES_TOML = textwrap.dedent('''
    [rules]
    type = "rules"
    heading = '^### (?P<id>[A-Z]+[0-9]+) — (?P<text>.+)$'
    link = true
    fields = [
      { name = "Why", regex = '\\*\\*Why:\\*\\*' },
      { name = "Enforced-by", regex = '\\*\\*Enforced-by:\\*\\* *(?:(?:hook|lint|test):\\S+|review)(?:, *(?:(?:hook|lint|test):\\S+|review))* *$' },
    ]
    ''')


def rule(rid, text, why=True, link=True, enforced="review"):
    out = [f"### {rid} — {text}", "Do the thing."]
    if why:
        out.append("**Why:** because.")
    if link:
        out.append("[source](https://forge.example.org/x)")
    if enforced is not None:
        out.append(f"**Enforced-by:** {enforced}")
    return "\n".join(out) + "\n\n"


def rules_doc(*rules, ident="rules", title="API rules"):
    return fm(id=ident, title=title, version="1.0.0", created="2026-01-01") + "\n# Rules\n\n" + "".join(rules)


class Shape(DoxCase):
    def setUp(self):
        super().setUp()
        self.schema_variant(lambda t: t.replace("[severity]", '[severity]\nrules-shape = "error"\ndigest-fresh = "error"')
                            + RULES_TOML)

    def test_well_formed_rules_pass(self):
        repo = self.repo("api", {"docs/rules.md": rules_doc(
            rule("A1", "Keep it simple", enforced="test:tests/x_test.go, review"),
            rule("A2", "Be kind", enforced="lint:no-work-ids"))})
        self.assertEqual(self.findings(repo, "rules-shape"), [])

    def test_each_missing_part_is_a_finding(self):
        repo = self.repo("api", {"docs/rules.md": rules_doc(
            rule("A1", "No why", why=False),
            rule("A2", "No link", link=False),
            rule("A3", "No enforcement", enforced=None),
            rule("A4", "Bad enforcement", enforced="vibes"),
            rule("A1", "Duplicate id"),
            "### not a rule heading\n\ntext\n")})
        msgs = sorted(f["message"] for f in self.findings(repo, "rules-shape"))
        self.assertEqual(len(msgs), 6, msgs)
        self.assertIn("rule A1 has no Why", msgs)
        self.assertIn("rule A2 has no link", msgs)
        self.assertIn("rule A3 has no Enforced-by", msgs)
        self.assertIn("rule A4 has no Enforced-by", msgs)
        self.assertIn("rule id A1 is used twice", msgs)
        self.assertIn("heading does not match the rule heading pattern: ### not a rule heading", msgs)

    def test_findings_point_at_the_rule_line(self):
        repo = self.repo("api", {"docs/rules.md": rules_doc(rule("A1", "x"), rule("A2", "y", why=False))})
        (f,) = self.findings(repo, "rules-shape")
        text = (repo / "docs/rules.md").read_text().splitlines()
        self.assertTrue(text[f["line"] - 1].startswith("### A2"))


class Digest(DoxCase):
    def setUp(self):
        super().setUp()
        self.schema_variant(lambda t: t.replace("[severity]", '[severity]\nrules-shape = "error"\ndigest-fresh = "error"')
                            + RULES_TOML)
        self.handbook = self.repo("clones/handbook", {
            "rules/platform.md": rules_doc(rule("P1", "Pipe, not store"), rule("P2", "Verify offline"),
                                           ident="platform", title="Platform rules"),
        }, namespace="handbook", root=".", origin=True)
        self.git(self.handbook, "tag", "rules-v1")
        self.git(self.handbook, "push", "-q", "origin", "rules-v1")
        os.environ["DOX_CLONES"] = str(self.base / "clones" / "{repo}")

    def api(self, include=True, out=".claude/rules/rules-of-the-road.md"):
        extra = f'\n[digest]\nout = "{out}"\n'
        if include:
            extra += 'include = ["handbook:rules/platform.md@rules-v1"]\n'
        return self.repo("api", {"docs/rules.md": rules_doc(rule("A1", "Keep it simple"), rule("A2", "Be kind"))},
                         extra=extra)

    def test_digest_writes_platform_then_repo_rules(self):
        repo = self.api()
        fs = self.findings(repo, "digest-fresh")
        self.assertEqual([f["message"] for f in fs], ["rules digest missing"])
        code, out, err = self.dox("digest", cwd=repo)
        self.assertEqual(code, 0, err)
        self.assertEqual(out.split(), [".claude/rules/rules-of-the-road.md"])
        text = (repo / ".claude/rules/rules-of-the-road.md").read_text()
        lines = [ln for ln in text.splitlines() if ln.startswith("- ")]
        self.assertEqual([ln.split("**")[1] for ln in lines], ["P1", "P2", "A1", "A2"])
        self.assertIn("- **P1** Pipe, not store", text)
        self.assertIn("https://forge.example.org/example-org/handbook/blob/rules-v1/rules/platform.md", text)
        self.assertIn("(../../docs/rules.md)", text)
        self.assertIn("handbook rules/platform.md @ rules-v1, version 1.0.0", text)
        self.assertEqual(self.findings(repo, "digest-fresh"), [])
        self.assertEqual(self.dox("digest", cwd=repo)[1], "")  # idempotent, byte for byte

    def test_amending_a_rule_makes_the_digest_stale(self):
        repo = self.api()
        self.dox("digest", cwd=repo)
        p = repo / "docs/rules.md"
        p.write_text(p.read_text().replace("Be kind", "Be very kind"))
        (f,) = self.findings(repo, "digest-fresh")
        self.assertEqual(f["message"], "rules digest is stale")
        self.assertIn("dox digest", f["fix"])

    def test_bumping_the_pin_without_rerendering_is_stale(self):
        repo = self.api()
        self.dox("digest", cwd=repo)
        p = self.handbook / "rules/platform.md"
        p.write_text(p.read_text().replace("Verify offline", "Verify offline, forever"))
        self.commit(self.handbook)
        self.git(self.handbook, "tag", "rules-v2")
        self.git(self.handbook, "push", "-q", "origin", "main", "rules-v2")
        self.assertEqual(self.findings(repo, "digest-fresh"), [])  # still pinned to rules-v1
        t = (repo / ".dox.toml").read_text().replace("@rules-v1", "@rules-v2")
        (repo / ".dox.toml").write_text(t)
        self.assertEqual(len(self.findings(repo, "digest-fresh")), 1)
        self.dox("digest", cwd=repo)
        self.assertIn("Verify offline, forever", (repo / ".claude/rules/rules-of-the-road.md").read_text())

    def test_repo_rules_only(self):
        repo = self.api(include=False)
        self.dox("digest", cwd=repo)
        text = (repo / ".claude/rules/rules-of-the-road.md").read_text()
        self.assertIn("- **A1** Keep it simple", text)
        self.assertNotIn("P1", text)

    def test_author_text_outside_the_markers_is_kept(self):
        repo = self.api()
        self.write(repo, ".claude/rules/rules-of-the-road.md", "# Rules of the road\n\nRead these first.\n")
        self.dox("digest", cwd=repo)
        text = (repo / ".claude/rules/rules-of-the-road.md").read_text()
        self.assertTrue(text.startswith("# Rules of the road\n\nRead these first.\n\n<!-- dox:digest:begin -->"))

    def test_unreachable_include_is_exit_3(self):
        repo = self.api()
        t = (repo / ".dox.toml").read_text().replace("@rules-v1", "@no-such-tag")
        (repo / ".dox.toml").write_text(t)
        code, out, err = self.dox("digest", cwd=repo)
        self.assertEqual(code, 3, out + err)

    def test_no_digest_table_no_check(self):
        repo = self.repo("api", {"docs/rules.md": rules_doc(rule("A1", "x"))})
        self.assertEqual(self.findings(repo, "digest-fresh"), [])
        code, out, err = self.dox("digest", cwd=repo)
        self.assertEqual(code, 2, out + err)
