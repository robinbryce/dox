import json

from tests.base import DoxCase, adr, fm


def clean_files():
    a1, t1 = adr(1, "first", body="# First\n\nSee [the second](adr-0002-second.md).\n")
    a2, t2 = adr(2, "second", refs=["ADR-0001", "EX-12"])
    return {
        f"docs/adr/{a1}.md": t1,
        f"docs/adr/{a2}.md": t2,
        "docs/rules.md": fm(id="rules", title="Rules", version="1.0.0") + "# Rules\n",
        "docs/guide.md": fm(id="guide", title="Guide", status="current", created="2026-01-01")
        + "Read [ADR 1](adr/adr-0001-first.md).\n",
        "README.md": "# api\n\nNo frontmatter, not a document.\n",
    }


class CleanRepo(DoxCase):
    def test_clean_repo_exits_zero(self):
        repo = self.repo("api", clean_files())
        code, out, err = self.dox("lint", cwd=repo)
        self.assertEqual(code, 0, out + err)
        self.assertIn("0 error(s)", out)

    def test_json_reports_config_sources_and_inactive_remote(self):
        repo = self.repo("api", clean_files())
        code, data = self.lint_json(repo)
        self.assertEqual(code, 0)
        self.assertEqual(data["config"]["schema_repo"]["source"], "DOX_SCHEMA_REPO")
        self.assertEqual(data["config"]["severity"]["schema"]["value"], "error")
        self.assertIn("links-remote", data["inactive"])
        self.assertTrue(any("DOX_SCHEMA_PATH" in n for n in data["notes"]))

    def test_types_are_assigned(self):
        from dox import config
        from dox.docs import Corpus
        repo = self.repo("api", clean_files())
        corpus = Corpus(config.load(repo))
        got = {d.path: d.type.name for d in corpus.docs}
        self.assertEqual(got, {
            "docs/adr/adr-0001-first.md": "adr",
            "docs/adr/adr-0002-second.md": "adr",
            "docs/guide.md": "note",
            "docs/rules.md": "rules",
        })


class Schema(DoxCase):
    def test_bad_status_and_missing_field(self):
        ident, text = adr(1, "x", status="done")
        repo = self.repo("api", {f"docs/adr/{ident}.md": text.replace("created: 2026-01-01\n", "")})
        fs = self.findings(repo, "schema")
        msgs = " | ".join(f["message"] for f in fs)
        self.assertIn("'done' is not one of", msgs)
        self.assertIn("'created' is a required property", msgs)
        fixes = " | ".join(f["fix"] for f in fs)
        self.assertIn("set `status` to one of: proposed, accepted", fixes)
        self.assertIn("add `created:`", fixes)
        status = next(f for f in fs if "done" in f["message"])
        self.assertEqual(status["line"], 4)

    def test_no_frontmatter(self):
        repo = self.repo("api", {"docs/adr/adr-0001-x.md": "# x\n"})
        fs = self.findings(repo, "schema")
        self.assertEqual(len(fs), 1)
        self.assertIn("no YAML frontmatter", fs[0]["message"])

    def test_unknown_field_rejected(self):
        ident, text = adr(1, "x", colour="blue")
        repo = self.repo("api", {f"docs/adr/{ident}.md": text})
        fs = self.findings(repo, "schema")
        self.assertTrue(any("colour" in f["message"] for f in fs), fs)

    def test_dates_stay_strings(self):
        ident, text = adr(1, "x")
        repo = self.repo("api", {f"docs/adr/{ident}.md": text})
        self.assertEqual(self.findings(repo, "schema"), [])


class IdUnique(DoxCase):
    def test_id_must_equal_stem(self):
        _, text = adr(1, "x")
        repo = self.repo("api", {"docs/adr/adr-0001-y.md": text})
        fs = self.findings(repo, "id-unique")
        self.assertEqual(len(fs), 1)
        self.assertIn("'adr-0001-x' but the file stem is 'adr-0001-y'", fs[0]["message"])

    def test_directory_document_identity(self):
        ident, text = adr(3, "dir")
        repo = self.repo("api", {f"docs/adr/{ident}/README.md": text, f"docs/adr/{ident}/appendix.md": "# A\n"})
        self.assertEqual(self.findings(repo, "id-unique"), [])
        self.assertEqual(self.findings(repo, "placement"), [])

    def test_shared_number_flagged_on_each(self):
        a, ta = adr(23, "grant-format")
        b, tb = adr(23, "grant-store")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta, f"docs/adr/{b}.md": tb})
        fs = self.findings(repo, "id-unique")
        self.assertEqual(len(fs), 2)
        self.assertIn("number ADR-0023 is shared", fs[0]["message"])

    def test_allowed_shared_number(self):
        a, ta = adr(23, "grant-format")
        b, tb = adr(23, "grant-store")
        c, tc = adr(24, "one")
        d, td = adr(24, "two")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta, f"docs/adr/{b}.md": tb,
                                 f"docs/adr/{c}.md": tc, f"docs/adr/{d}.md": td},
                         extra='[checks.id-unique]\nallow = ["ADR-0023"]\n')
        fs = self.findings(repo, "id-unique")
        self.assertEqual(sorted(f["path"] for f in fs), [f"docs/adr/{c}.md", f"docs/adr/{d}.md"])

    def test_allow_must_be_citations(self):
        a, ta = adr(1, "x")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta},
                         extra='[checks.id-unique]\nallow = "ADR-0023"\n')
        code, out, err = self.dox("lint", cwd=repo)
        self.assertEqual(code, 2, out + err)
        self.assertIn("allow", err)

    def test_same_id_twice(self):
        a, ta = adr(1, "x")
        a2, tb = adr(1, "x", status="superseded", superseded_by="ADR-0002")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta, f"docs/adr/retired/{a}.md": tb})
        fs = self.findings(repo, "id-unique")
        self.assertTrue(any("also used by" in f["message"] for f in fs), fs)


class LinksLocal(DoxCase):
    def test_broken_link_suggests_moved_target(self):
        a, ta = adr(1, "x", body="See [moved](old/guide.md) and [ok](#anchor) and `[x](nope.md)`.\n"
                                  "```\n[y](fenced.md)\n```\n")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta,
                                 "docs/area/guide.md": "# g\n"})
        fs = self.findings(repo, "links-local")
        self.assertEqual(len(fs), 1, fs)
        self.assertIn("old/guide.md", fs[0]["message"])
        self.assertIn("change the link to `../area/guide.md`", fs[0]["fix"])
        self.assertEqual(fs[0]["line"], 7)

    def test_link_leaving_repository(self):
        repo = self.repo("api", {"docs/x.md": "[c](../../canopy/README.md)\n"})
        fs = self.findings(repo, "links-local")
        self.assertIn("leaves the repository", fs[0]["message"])

    def test_forbidden_reference(self):
        repo = self.repo("api", {"docs/x.md": "see https://private.example.org/thing\n"},
                         extra='[checks.links-local]\nforbid = ["private.example.org"]\n')
        fs = self.findings(repo, "links-local")
        self.assertEqual([f["message"] for f in fs], ["forbidden reference private.example.org"])

    def test_frontmatter_refs_resolve(self):
        a, ta = adr(1, "x", refs=["ADR-0009", "adr-0001-x", "guide.md", "api:ADR-0001",
                                  "nowhere:ADR-0001", "EX-1", "https://example.org", "handbook:ADR-0001"])
        repo = self.repo("api", {f"docs/adr/{a}.md": ta, "docs/guide.md": "# g\n"})
        msgs = sorted(f["message"] for f in self.findings(repo, "links-local"))
        self.assertEqual(msgs, ["refs: 'ADR-0009' does not resolve", "refs: unknown home 'nowhere' in 'nowhere:ADR-0001'"])

    def test_replaces_needs_replaced_by(self):
        old, told = adr(1, "old", status="superseded", superseded_by="ADR-0002")
        new, tnew = adr(2, "new", supersedes=["ADR-0001"])
        lone, tlone = adr(3, "lone", supersedes=["ADR-0004"])
        four, tfour = adr(4, "four", status="superseded", superseded_by="ADR-0002")
        repo = self.repo("api", {f"docs/adr/retired/{old}.md": told, f"docs/adr/{new}.md": tnew,
                                 f"docs/adr/{lone}.md": tlone, f"docs/adr/retired/{four}.md": tfour})
        fs = self.findings(repo, "links-local")
        got = sorted((f["path"], f["message"]) for f in fs)
        self.assertEqual(len(got), 2, got)
        self.assertIn("docs/adr/adr-0002-new.md", got[0][0] + got[1][0])
        self.assertIn("docs/adr/retired/adr-0004-four.md", got[0][0] + got[1][0])


class Placement(DoxCase):
    def test_retired_status_must_be_in_retired_dir(self):
        a, ta = adr(1, "x", status="rejected")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta})
        fs = self.findings(repo, "placement")
        self.assertEqual(len(fs), 1)
        self.assertIn("belongs in adr/retired", fs[0]["message"])
        self.assertIn(f"git mv docs/adr/{a}.md docs/adr/retired/", fs[0]["fix"])

    def test_legacy_dir_and_outside_root(self):
        a, ta = adr(1, "x")
        b, tb = adr(2, "y")
        repo = self.repo("api", {f"docs/decisions/{a}.md": ta, f"{b}.md": tb})
        fs = sorted(f["message"] for f in self.findings(repo, "placement"))
        self.assertEqual(len(fs), 2, fs)

    def test_type_not_declared(self):
        a, ta = adr(1, "x")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta}, types=["note"])
        fs = self.findings(repo, "placement")
        self.assertIn(".dox.toml types omit adr", fs[0]["message"])

    def test_part_with_frontmatter_and_layout(self):
        a, ta = adr(1, "x")
        repo = self.repo("api", {f"docs/adr/{a}/README.md": ta,
                                 f"docs/adr/{a}/part.md": fm(id="part", title="p") + "x\n",
                                 "docs/rules/platform/README.md": fm(id="platform", title="R", version="1") + "x\n"})
        msgs = [f["message"] for f in self.findings(repo, "placement")]
        self.assertIn("part of adr-0001-x carries frontmatter", msgs)
        self.assertIn("rules documents are single files", msgs)


class WorkIds(DoxCase):
    def test_ids_in_canon_prose_not_frontmatter(self):
        a, ta = adr(1, "x", refs=["EX-9"], body="Done under EX-42 per plan-2611-01-x.\n")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta, "README.md": "EX-7\n",
                                 "docs/vectors/notes.md": "EX-8\n"},
                         extra='[checks.no-work-ids]\nexempt = ["docs/vectors"]\n')
        got = sorted((f["path"], f["message"]) for f in self.findings(repo, "no-work-ids"))
        self.assertEqual(got, [
            ("README.md", "tracker id EX-7 in canon prose"),
            (f"docs/adr/{a}.md", "plan id plan-2611 in canon prose"),
            (f"docs/adr/{a}.md", "tracker id EX-42 in canon prose"),
        ])

    def test_plans_in_canon(self):
        repo = self.repo("api", {
            "docs/plans/plan-2611-01-x.md": fm(id="plan-2611-01-x", title="p", status="active", created="2026-11-01"),
            "docs/plans/notes.md": "# notes\n",
        })
        got = sorted(f["path"] for f in self.findings(repo, "no-plans-in-canon"))
        self.assertEqual(got, ["docs/plans/notes.md", "docs/plans/plan-2611-01-x.md"])

    def test_work_repo_holds_plans(self):
        repo = self.repo("work", {
            "plans/plan-2611-01-x.md": fm(id="plan-2611-01-x", title="p", status="active", created="2026-11-01")
            + "EX-1 is fine here\n",
            "archive/2610/plan-2610-02-y.md": fm(id="plan-2610-02-y", title="p", status="complete", created="2026-10-01"),
            "archive/2610/plan-0003-legacy.md": fm(id="plan-0003-legacy", title="p", status="complete", created="2025-10-01"),
        }, namespace="work", root=".", types=["plan"])
        code, data = self.lint_json(repo)
        self.assertEqual(data["findings"], [])
        self.assertEqual(code, 0)

    def test_outcome_none_is_not_a_ref(self):
        repo = self.repo("work", {
            "archive/2610/plan-2610-02-y.md": fm(id="plan-2610-02-y", title="p", status="complete",
                                                 created="2026-10-01", outcome='"none: superseded by events"'),
            "archive/2610/plan-2610-03-z.md": fm(id="plan-2610-03-z", title="p", status="complete",
                                                 created="2026-10-01", outcome=["ADR-0009"]),
        }, namespace="work", root=".", types=["plan"])
        msgs = [f["message"] for f in self.findings(repo, "links-local")]
        self.assertEqual(msgs, ["outcome: 'ADR-0009' does not resolve"])

    def test_closed_plan_must_be_archived(self):
        repo = self.repo("work", {
            "plans/plan-2611-01-x.md": fm(id="plan-2611-01-x", title="p", status="complete", created="2026-11-01"),
        }, namespace="work", root=".", types=["plan"])
        fs = self.findings(repo, "placement")
        self.assertIn("belongs in archive/{yymm}", fs[0]["message"])


class SeverityAndSkip(DoxCase):
    def test_warn_does_not_fail(self):
        a, ta = adr(1, "x", status="nope")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta}, extra='[severity]\nschema = "warn"\n')
        code, data = self.lint_json(repo)
        self.assertEqual(code, 0)
        self.assertEqual(data["summary"]["warnings"], 1)
        self.assertEqual(data["config"]["severity"]["schema"]["source"], ".dox.toml")

    def test_skip_is_reported(self):
        a, ta = adr(1, "x", status="nope")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta})
        code, data = self.lint_json(repo, "--skip", "schema")
        self.assertEqual(code, 0)
        self.assertEqual(data["skipped"], ["schema"])
        self.assertIn("skipped checks: schema", data["notes"])
        code, out, _ = self.dox("lint", "--skip", "schema", cwd=repo)
        self.assertIn("note: skipped checks: schema", out)

    def test_unknown_skip_is_usage_error(self):
        repo = self.repo("api", {})
        code, _, err = self.dox("lint", "--skip", "nope", cwd=repo)
        self.assertEqual(code, 2)

    def test_check_not_listed_by_schema_repo_is_off(self):
        self.schema_variant(lambda t: t.replace('no-work-ids = "error"\n', ""))
        repo = self.repo("api", {"docs/x.md": "EX-1\n"})
        code, data = self.lint_json(repo)
        self.assertEqual(code, 0)
        self.assertIn("no-work-ids", data["inactive"])


class Baseline(DoxCase):
    def test_init_then_shrink_only_and_new_findings_fail(self):
        a, ta = adr(1, "x", status="nope")
        b, tb = adr(2, "y", status="nope")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta, f"docs/adr/{b}.md": tb},
                         extra='baseline = ".dox-baseline.json"\n')
        code, _, err = self.dox("lint", "--init-baseline", cwd=repo)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(json.loads((repo / ".dox-baseline.json").read_text())["findings"]), 2)
        code, _, _ = self.dox("lint", "--init-baseline", cwd=repo)
        self.assertEqual(code, 2)  # never overwrite: shrink only
        code, data = self.lint_json(repo)
        self.assertEqual((code, data["summary"]["baselined"]), (0, 2))
        # fix one, regress elsewhere
        self.write(repo, f"docs/adr/{a}.md", ta.replace("status: nope", "status: accepted"))
        c, tc = adr(3, "z", status="nope")
        self.write(repo, f"docs/adr/{c}.md", tc)
        code, data = self.lint_json(repo)
        self.assertEqual(code, 1)
        self.assertEqual(data["summary"]["errors"], 1)
        code, _, _ = self.dox("lint", "--update-baseline", cwd=repo)
        entries = json.loads((repo / ".dox-baseline.json").read_text())["findings"]
        self.assertEqual([e["path"] for e in entries], [f"docs/adr/{b}.md"])  # shrank, did not add

    def test_update_keeps_entries_of_checks_not_run(self):
        repo = self.repo("api", {"docs/x.md": "EX-1\n"}, extra='baseline = ".dox-baseline.json"\n')
        self.dox("lint", "--init-baseline", cwd=repo)
        self.dox("lint", "--update-baseline", "--skip", "no-work-ids", cwd=repo)
        entries = json.loads((repo / ".dox-baseline.json").read_text())["findings"]
        self.assertEqual(len(entries), 1)


class Changed(DoxCase):
    def test_only_changed_files_reported(self):
        a, ta = adr(1, "x", status="nope")
        repo = self.repo("api", {f"docs/adr/{a}.md": ta}, origin=True)
        b, tb = adr(2, "y", status="nope")
        self.write(repo, f"docs/adr/{b}.md", tb)
        _, data = self.lint_json(repo, "--changed")
        self.assertEqual({f["path"] for f in data["findings"]}, {f"docs/adr/{b}.md"})


class ConfigErrors(DoxCase):
    def test_no_schema_repo_is_exit_2(self):
        import os
        del os.environ["DOX_SCHEMA_REPO"]
        repo = self.repo("api", {})
        code, _, err = self.dox("lint", cwd=repo)
        self.assertEqual(code, 2)
        self.assertIn("--dox-schema", err)
        code, _, err = self.dox("lint", "--dox-schema", "example-org/doc-schema", cwd=repo)
        self.assertEqual(code, 0, err)

    def test_missing_dox_toml(self):
        repo = self.repo("api", {})
        (repo / ".dox.toml").unlink()
        self.assertEqual(self.dox("lint", cwd=repo)[0], 2)

    def test_unsupported_contract_major(self):
        self.schema_variant(lambda t: t.replace('contract = "1.0"', 'contract = "2.0"'))
        repo = self.repo("api", {})
        code, _, err = self.dox("lint", cwd=repo)
        self.assertEqual(code, 2)
        self.assertIn("contract 2.0 is not supported", err)

    def test_newer_minor_unknown_keys_are_config_findings(self):
        self.schema_variant(lambda t: t.replace('contract = "1.0"', 'contract = "1.3"\nshiny = 1'))
        repo = self.repo("api", {}, extra="colour = 'blue'\n")
        fs = self.findings(repo, "config")
        self.assertEqual(sorted(f["message"] for f in fs),
                         [".dox.toml: unknown key `colour` ignored", "dox.toml: unknown key `shiny` ignored"])

    def test_unknown_type_and_namespace(self):
        repo = self.repo("api", {}, types=["adr", "memo"])
        self.assertEqual(self.dox("lint", cwd=repo)[0], 2)
        repo2 = self.repo("other", {}, namespace="elsewhere")
        self.assertEqual(self.dox("lint", cwd=repo2)[0], 2)

    def test_status_enum_disagreement_is_config_finding(self):
        self.schema_variant(lambda t: t.replace('rejected = "retired", ', ""))
        repo = self.repo("api", {})
        fs = self.findings(repo, "config")
        self.assertTrue(any("disagree" in f["message"] for f in fs), fs)

    def test_bad_role(self):
        self.schema_variant(lambda t: t.replace('accepted = "in-force"', 'accepted = "live"'))
        repo = self.repo("api", {})
        self.assertEqual(self.dox("lint", cwd=repo)[0], 2)

    def test_not_a_git_repo(self):
        d = self.base / "plain"
        d.mkdir()
        self.assertEqual(self.dox("lint", cwd=d)[0], 2)
