"""Shared fixture: an isolated HOME and cache, the fictional schema repo
`example-org/doc-schema` written to a temp dir, and helpers that build real
git repositories with a bare `origin`. Nothing touches the caller's
environment, network or real repositories."""

from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import subprocess
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

from dox import cli

SCHEMA_REPO = "example-org/doc-schema"

COMMON = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://example.org/doc-schema/common.schema.json",
    "$defs": {
        "date": {"type": "string", "pattern": "^[0-9]{4}-[0-9]{2}-[0-9]{2}$"},
        "ref": {"type": "string", "minLength": 1},
        "refs": {"type": "array", "items": {"$ref": "#/$defs/ref"}, "uniqueItems": True},
        "base": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "title": {"type": "string", "minLength": 1},
                "created": {"$ref": "#/$defs/date"},
                "updated": {"$ref": "#/$defs/date"},
                "supersedes": {"$ref": "#/$defs/refs"},
                "superseded-by": {"$ref": "#/$defs/ref"},
                "refs": {"$ref": "#/$defs/refs"},
            },
        },
    },
}


def doc_schema(name, statuses, required=("id", "title", "status", "created"), extra=None):
    props = {"status": {"enum": list(statuses)}} if statuses else {}
    props.update(extra or {})
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://example.org/doc-schema/{name}.schema.json",
        "type": "object",
        "properties": props,
        "required": list(required),
        "allOf": [{"$ref": "common.schema.json#/$defs/base"}],
        "unevaluatedProperties": False,
    }


SCHEMAS = {
    "common.schema.json": COMMON,
    "adr.schema.json": doc_schema("adr", ["proposed", "accepted", "rejected", "superseded"]),
    "note.schema.json": doc_schema("note", ["draft", "current", "superseded"]),
    "rules.schema.json": doc_schema("rules", None, required=("id", "title", "version"),
                                    extra={"version": {"type": "string"}}),
    "plan.schema.json": doc_schema("plan", ["draft", "active", "complete", "abandoned"],
                                   extra={"issues": {"type": "array"}}),
}

DOX_TOML = textwrap.dedent('''\
    contract = "1.0"
    name = "Example Org"
    exclude = [".agents/**"]

    [namespaces]
    handbook = "example-org/handbook"
    api      = "example-org/api"
    work     = "example-org/work"

    [types.adr]
    kind = "canon"
    schema = "adr.schema.json"
    identity = "id"
    id_format = "adr-{n:04}-{slug}"
    cite = "ADR-{n:04}"
    allocate = "namespace"
    dirs = ["adr"]
    retired_dir = "adr/retired"
    scan_dirs = ["decisions"]
    layout = "either"
    index = "README.md"
    template = "adr.md"
    [types.adr.status]
    field = "status"
    values = { proposed = "discussion", accepted = "in-force", rejected = "retired", superseded = "retired" }
    [types.adr.links]
    supersedes = "replaces"
    superseded-by = "replaced-by"
    refs = "cites"

    [types.rules]
    kind = "canon"
    schema = "rules.schema.json"
    identity = "id"
    dirs = ["rules.md", "rules"]
    layout = "file"
    status = "none"

    [types.note]
    kind = "canon"
    schema = "note.schema.json"
    identity = "id"
    fallback = true
    [types.note.status]
    field = "status"
    values = { draft = "discussion", current = "in-force", superseded = "retired" }
    [types.note.links]
    supersedes = "replaces"
    superseded-by = "replaced-by"
    refs = "cites"

    [types.plan]
    kind = "work"
    schema = "plan.schema.json"
    identity = "id"
    id_format = "plan-{yymm}-{n:02}-{slug}"
    id_legacy = ['^plan-[0-9]{4}-[a-z0-9]+(?:-[a-z0-9]+)*$']
    allocate = "month"
    dirs = ["plans"]
    archive_dir = "archive/{yymm}"
    template = "plan.md"
    template_dir = "plan-dir"
    [types.plan.status]
    field = "status"
    values = { draft = "discussion", active = "in-force", complete = "closed", abandoned = "closed" }
    [types.plan.links]
    refs = "cites"

    [refs]
    separator = ":"
    external = [{ pattern = '^EX-[0-9]+$', url = "https://tracker.example.org/{0}" }]

    [links]
    cross_repo = "https://forge.example.org/{repo}/blob/{ref}/{path}"

    [severity]
    config = "error"
    schema = "error"
    id-unique = "error"
    links-local = "error"
    placement = "error"
    index-fresh = "off"
    no-work-ids = "error"
    no-plans-in-canon = "error"
    links-remote = "error"

    [checks.no-work-ids]
    patterns = [
      { regex = '\\bEX-[0-9]+\\b', what = "tracker id" },
      { regex = '\\bplan-[0-9]{4}\\b', what = "plan id" },
    ]

    [checks.no-plans-in-canon]
    dirs = ["**/plans/**"]
    ''')

TEMPLATES = {
    "adr.md": "---\nid: {{ID}}\ntitle: {{TITLE}}\nstatus: proposed\ncreated: {{DATE}}\n---\n\n# {{TITLE}}\n",
    "plan.md": "---\nid: {{ID}}\ntitle: {{TITLE}}\nstatus: draft\ncreated: {{DATE}}\n---\n\n# {{TITLE}}\n",
    "plan-dir/README.md": "---\nid: {{ID}}\ntitle: {{TITLE}}\nstatus: draft\ncreated: {{DATE}}\n---\n\n# {{TITLE}}\n",
    "plan-dir/handoff.md": "# Handoff for {{ID}}\n",
}


def fm(**fields) -> str:
    """Frontmatter block from keyword fields (underscores become hyphens)."""
    lines = ["---"]
    for k, v in fields.items():
        k = k.replace("_", "-")
        lines.append(f"{k}: {json.dumps(v) if isinstance(v, list) else v}")
    lines.append("---\n")
    return "\n".join(lines)


def adr(n, slug, status="accepted", body="", **more):
    ident = f"adr-{n:04d}-{slug}"
    return ident, fm(id=ident, title=f"ADR {n}", status=status, created="2026-01-01", **more) + body


class DoxCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="dox-test-")
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(self._tmp.name).resolve()
        self.home = self.base / "home"
        self.home.mkdir()
        self.schema = self.write_schema(self.base / "doc-schema")
        env = {
            "HOME": str(self.home), "PATH": os.environ.get("PATH", ""),
            "XDG_CONFIG_HOME": str(self.home / ".config"), "XDG_CACHE_HOME": str(self.home / ".cache"),
            "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
            "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8",
            "GIT_CEILING_DIRECTORIES": str(self.base.parent),
            "DOX_SCHEMA_REPO": SCHEMA_REPO, "DOX_SCHEMA_PATH": str(self.schema),
        }
        self.enterContext(mock.patch.dict(os.environ, env, clear=True))

    # ---- schema repo

    def write_schema(self, path: Path, dox_toml: str = DOX_TOML, schemas=None) -> Path:
        path.mkdir(parents=True, exist_ok=True)
        (path / "dox.toml").write_text(dox_toml)
        (path / "schemas").mkdir(exist_ok=True)
        for name, s in (schemas or SCHEMAS).items():
            (path / "schemas" / name).write_text(json.dumps(s, indent=2))
        for name, text in TEMPLATES.items():
            (path / "templates" / name).parent.mkdir(parents=True, exist_ok=True)
            (path / "templates" / name).write_text(text)
        return path

    def schema_variant(self, edit) -> Path:
        """A copy of the fixture schema repo with dox.toml text edited."""
        p = self.base / f"doc-schema-{len(list(self.base.glob('doc-schema-*')))}"
        self.write_schema(p, edit(DOX_TOML), copy.deepcopy(SCHEMAS))
        os.environ["DOX_SCHEMA_PATH"] = str(p)
        return p

    # ---- git

    def git(self, repo: Path, *args: str) -> str:
        r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
        if r.returncode:
            raise AssertionError(f"git {' '.join(args)}: {r.stderr}")
        return r.stdout.strip()

    def repo(self, name: str, files: dict[str, str], *, namespace="api", root="docs",
             types=("adr", "rules", "note"), extra="", origin=False, commit=True) -> Path:
        path = self.base / name
        path.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
        toml = (f'schema_ref = "v1.0.0"\nnamespace = "{namespace}"\nroot = "{root}"\n'
                f"types = {json.dumps(list(types))}\n" + extra)
        self.write(path, ".dox.toml", toml)
        (path / root).mkdir(parents=True, exist_ok=True)
        for rel, text in files.items():
            self.write(path, rel, text)
        if commit:
            self.commit(path, "init")
        if origin:
            bare = self.base / f"{name}.git"
            subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
            self.git(path, "remote", "add", "origin", str(bare))
            self.git(path, "push", "-q", "-u", "origin", "main")
            self.git(path, "remote", "set-head", "origin", "main")
        return path

    def write(self, repo: Path, rel: str, text: str) -> Path:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        return p

    def commit(self, repo: Path, msg: str = "c"):
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-q", "--allow-empty", "-m", msg)

    # ---- running dox

    def dox(self, *args: str, cwd: Path | None = None) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        argv = list(args)
        if cwd is not None:
            argv[1:1] = ["-C", str(cwd)]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(argv)
        return code, out.getvalue(), err.getvalue()

    def lint_json(self, repo: Path, *extra: str) -> tuple[int, dict]:
        code, out, err = self.dox("lint", "--json", *extra, cwd=repo)
        self.assertNotEqual(code, 2, err)
        return code, json.loads(out)

    def findings(self, repo: Path, check: str | None = None, *extra: str) -> list[dict]:
        _, data = self.lint_json(repo, *extra)
        return [f for f in data["findings"] if check is None or f["check"] == check]
