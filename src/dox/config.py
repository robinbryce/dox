"""Layered configuration (ADR-0001 section 3, docs/contract.md).

Only the committed `.dox.toml` and the schema repo's `dox.toml` decide
results. The environment and workspace layers set mechanics and topology.
"""

from __future__ import annotations

import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from referencing import Registry, Resource

from dox import CONTRACT_MAJOR, proc, schemarepo
from dox.errors import ConfigError
from dox.model import MEANINGS, ROLES, IdPattern, TypeDef

CHECKS = {
    # id: offline?
    "config": True,
    "schema": True,
    "id-unique": True,
    "links-local": True,
    "placement": True,
    "index-fresh": True,
    "no-work-ids": True,
    "no-plans-in-canon": True,
    "links-remote": False,
}
LEVELS = ("error", "warn", "off")

TOP_KEYS = {"contract", "name", "schemas", "templates", "exclude", "namespaces", "types",
            "refs", "links", "severity", "checks", "index"}
TYPE_KEYS = {"kind", "schema", "identity", "id_format", "id_legacy", "cite", "allocate", "dirs",
             "retired_dir", "archive_dir", "scan_dirs", "layout", "fallback", "status", "links",
             "review_date", "index", "template", "template_dir"}
REPO_KEYS = {"schema_ref", "namespace", "root", "types", "exclude", "baseline", "severity", "checks"}
HOME_RX = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


@dataclass
class Note:
    """A configuration problem that does not stop the run (check `config`)."""

    path: str
    message: str
    fix: str


@dataclass
class Config:
    repo_root: Path
    config_path: Path
    root: str  # docs root relative to repo_root, "" for the repo root
    namespace: str
    repo_types: list[str]
    schema_repo: str
    schema_ref: str
    schema_dir: Path
    schema_source: str
    name: str
    namespaces: dict[str, str]
    types: dict[str, TypeDef]
    exclude: list[str]
    baseline: str | None
    severity: dict[str, str]
    severity_source: dict[str, str]
    options: dict[str, dict]
    ref_separator: str
    external: list[tuple[re.Pattern, str]]
    cross_repo: str | None
    digest: str | None
    digest_roles: list[str]
    templates_dir: Path
    registry: Registry
    clones: str | None
    timeout: float
    cache_dir: Path
    notes: list[Note] = field(default_factory=list)
    schemas: dict[str, dict] = field(default_factory=dict)

    @property
    def docs_root(self) -> Path:
        return self.repo_root / self.root if self.root else self.repo_root

    def to_docs(self, repo_path: str) -> str | None:
        """Repo-relative path to docs-root-relative, None when outside."""
        if not self.root:
            return repo_path
        if repo_path == self.root:
            return ""
        prefix = self.root + "/"
        return repo_path[len(prefix):] if repo_path.startswith(prefix) else None

    def to_repo(self, docs_path: str) -> str:
        if not self.root:
            return docs_path
        return f"{self.root}/{docs_path}" if docs_path else self.root

    def option(self, check: str, key: str, default=None):
        return self.options.get(check, {}).get(key, default)

    def holds_work(self) -> bool:
        return any(self.types[t].kind == "work" for t in self.repo_types)

    def resolved(self) -> dict:
        """The resolved configuration with the source of each value, for --json."""
        repo_src = str(self.config_path)
        schema_src = f"{self.schema_repo}@{self.schema_ref} ({self.schema_source})"
        return {
            "schema_repo": {"value": self.schema_repo, "source": self._schema_repo_source},
            "schema_ref": {"value": self.schema_ref, "source": repo_src},
            "schema_dir": {"value": str(self.schema_dir), "source": self.schema_source},
            "namespace": {"value": self.namespace, "source": repo_src},
            "root": {"value": self.root or ".", "source": repo_src},
            "types": {"value": self.repo_types, "source": repo_src},
            "exclude": {"value": self.exclude, "source": f"{schema_src} + {repo_src}"},
            "baseline": {"value": self.baseline, "source": repo_src},
            "severity": {
                c: {"value": self.severity[c], "source": self.severity_source[c]} for c in CHECKS
            },
            "cache_dir": {"value": str(self.cache_dir), "source": self._cache_source},
            "clones": {"value": self.clones, "source": self._clones_source},
        }

    _schema_repo_source: str = ""
    _cache_source: str = ""
    _clones_source: str = ""


def _toml(path: Path, what: str) -> dict:
    try:
        with open(path, "rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        raise ConfigError(f"{what} not found: {path}") from None
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"{path}: invalid TOML: {e}") from None


def _str(d: dict, key: str, where: str, required=True, default=None):
    v = d.get(key, default)
    if v is None:
        if required:
            raise ConfigError(f"{where}: missing required key `{key}`")
        return None
    if not isinstance(v, str):
        raise ConfigError(f"{where}: `{key}` must be a string")
    return v


def _strlist(d: dict, key: str, where: str, required=False) -> list[str]:
    v = d.get(key)
    if v is None:
        if required:
            raise ConfigError(f"{where}: missing required key `{key}`")
        return []
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ConfigError(f"{where}: `{key}` must be a list of strings")
    return v


def _unknown(d: dict, known: set, where: str, path: str, notes: list[Note]):
    for k in sorted(set(d) - known):
        notes.append(Note(path, f"{where}: unknown key `{k}` ignored",
                          f"remove `{k}`, or upgrade dox if it belongs to a newer contract minor"))


def _workspace() -> tuple[dict, str]:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    path = Path(base) / "dox" / "config.toml"
    if path.is_file():
        return _toml(path, "workspace config"), str(path)
    return {}, ""


def repo_root_of(cwd: Path) -> Path:
    r = proc.git(cwd, "rev-parse", "--show-toplevel")
    if r.returncode:
        raise ConfigError(f"{cwd} is not inside a git repository")
    return Path(r.stdout.strip()).resolve()


def load(cwd: Path, schema_flag: str | None = None, config_path: Path | None = None) -> Config:
    repo_root = repo_root_of(cwd)
    notes: list[Note] = []

    cfg_path = Path(config_path).resolve() if config_path else repo_root / ".dox.toml"
    if not cfg_path.is_file():
        raise ConfigError(
            f"no .dox.toml at {cfg_path}; add one with schema_ref, namespace, root and types "
            "(see docs/contract.md)")
    repo = _toml(cfg_path, ".dox.toml")
    rname = cfg_path.name
    _unknown(repo, REPO_KEYS, rname, rname, notes)

    if schema_flag:
        schema_repo, schema_repo_src = schema_flag, "--dox-schema"
    elif os.environ.get("DOX_SCHEMA_REPO"):
        schema_repo, schema_repo_src = os.environ["DOX_SCHEMA_REPO"], "DOX_SCHEMA_REPO"
    else:
        raise ConfigError(
            "no schema repo: pass --dox-schema <owner>/<repo> or set DOX_SCHEMA_REPO "
            "(in CI, the action's `schema-repo` input)")
    schemarepo.check_repo(schema_repo, schema_repo_src)

    ws, ws_path = _workspace()
    cache_dir, cache_src = _pick("DOX_CACHE_DIR", ws, "cache_dir", ws_path)
    if not cache_dir:
        base = os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache")
        cache_dir, cache_src = str(Path(base) / "dox"), "built-in"
    clones, clones_src = _pick("DOX_CLONES", ws, "clones", ws_path)
    timeout_v, _ = _pick("DOX_TIMEOUT", ws, "timeout", ws_path)
    try:
        timeout = float(timeout_v) if timeout_v else 20.0
    except ValueError:
        raise ConfigError(f"DOX_TIMEOUT must be a number, got {timeout_v!r}") from None

    schema_ref = _str(repo, "schema_ref", rname)
    local = os.environ.get("DOX_SCHEMA_PATH")
    if local:
        schema_dir = Path(local).expanduser().resolve()
        schema_source = f"DOX_SCHEMA_PATH={schema_dir}"
        if not (schema_dir / "dox.toml").is_file():
            raise ConfigError(f"DOX_SCHEMA_PATH {schema_dir} has no dox.toml")
    else:
        schema_dir = schemarepo.fetch(schema_repo, schema_ref, Path(cache_dir).expanduser(), timeout)
        schema_source = "fetched"

    pre = _toml(schema_dir / "dox.toml", "dox.toml")
    sname = f"{schema_repo}:dox.toml"
    contract = pre.get("contract")
    if not isinstance(contract, str) or not re.match(r"^\d+(\.\d+)?$", contract):
        raise ConfigError(f"{sname}: `contract` must be a version string such as \"1.0\"")
    major = int(contract.split(".")[0])
    if major != CONTRACT_MAJOR:
        raise ConfigError(
            f"{sname}: contract {contract} is not supported by this dox (contract {CONTRACT_MAJOR}.x); "
            "use a dox release that supports it")
    _unknown(pre, TOP_KEYS, "dox.toml", sname, notes)

    namespaces = pre.get("namespaces") or {}
    if not isinstance(namespaces, dict) or not all(
            isinstance(v, str) and HOME_RX.match(k) for k, v in namespaces.items()):
        raise ConfigError(f"{sname}: [namespaces] must map home names to <owner>/<repo>")

    types = _types(pre.get("types"), sname, notes)

    namespace = _str(repo, "namespace", rname)
    if namespace not in namespaces:
        raise ConfigError(f"{rname}: namespace {namespace!r} is not in the schema repo's [namespaces] "
                          f"({', '.join(sorted(namespaces))})")
    root = _str(repo, "root", rname)
    from dox import globs
    root = globs.norm(root)
    if root.startswith("/") or ".." in root.split("/"):
        raise ConfigError(f"{rname}: root must be a path inside the repository")
    if not (repo_root / root).is_dir():
        raise ConfigError(f"{rname}: docs root {root or '.'!r} does not exist")
    repo_types = _strlist(repo, "types", rname, required=True)
    for t in repo_types:
        if t not in types:
            raise ConfigError(f"{rname}: type {t!r} is not declared in the schema repo "
                              f"({', '.join(types)})")

    exclude = _strlist(pre, "exclude", sname) + _strlist(repo, "exclude", rname)
    baseline = _str(repo, "baseline", rname, required=False)

    severity, sev_src = {}, {}
    s_sev, r_sev = pre.get("severity") or {}, repo.get("severity") or {}
    for where, table in ((sname, s_sev), (rname, r_sev)):
        if not isinstance(table, dict):
            raise ConfigError(f"{where}: [severity] must be a table")
        for k, v in table.items():
            if k not in CHECKS:
                notes.append(Note(where, f"[severity]: unknown check `{k}` ignored",
                                  f"use one of: {', '.join(CHECKS)}"))
            elif v not in LEVELS:
                raise ConfigError(f"{where}: [severity] {k} must be one of {', '.join(LEVELS)}")
    for c in CHECKS:
        if c in r_sev:
            severity[c], sev_src[c] = r_sev[c], rname
        elif c in s_sev:
            severity[c], sev_src[c] = s_sev[c], sname
        else:
            severity[c], sev_src[c] = "off", "default (not listed by the schema repo)"

    options: dict[str, dict] = {}
    for where, table in ((sname, pre.get("checks") or {}), (rname, repo.get("checks") or {})):
        if not isinstance(table, dict):
            raise ConfigError(f"{where}: [checks] must be a table of tables")
        for k, v in table.items():
            if k not in CHECKS:
                notes.append(Note(where, f"[checks.{k}]: unknown check ignored",
                                  f"use one of: {', '.join(CHECKS)}"))
                continue
            if not isinstance(v, dict):
                raise ConfigError(f"{where}: [checks.{k}] must be a table")
            options.setdefault(k, {}).update(v)
    _check_options(options, notes)

    refs = pre.get("refs") or {}
    sep = refs.get("separator", ":")
    external = []
    for i, e in enumerate(refs.get("external") or []):
        if not isinstance(e, dict) or not isinstance(e.get("pattern"), str):
            raise ConfigError(f"{sname}: [[refs.external]] entry {i + 1} needs `pattern`")
        try:
            external.append((re.compile(e["pattern"]), e.get("url", "")))
        except re.error as err:
            raise ConfigError(f"{sname}: [[refs.external]] pattern {e['pattern']!r}: {err}") from None
    links = pre.get("links") or {}
    cross = links.get("cross_repo")
    index = pre.get("index") or {}
    digest = index.get("digest")
    digest_roles = index.get("digest_roles", ["in-force", "discussion"])
    for r in digest_roles:
        if r not in ROLES:
            raise ConfigError(f"{sname}: [index] digest_roles: unknown role {r!r}")

    schemas_dir = schema_dir / pre.get("schemas", "schemas")
    registry, schemas = _registry(schemas_dir, sname)
    for t in types.values():
        if t.schema not in schemas:
            raise ConfigError(f"{sname}: type {t.name}: schema {t.schema!r} not found in {schemas_dir}")
        _status_agrees(t, schemas[t.schema], sname, notes)

    cfg = Config(
        repo_root=repo_root, config_path=cfg_path, root=root, namespace=namespace,
        repo_types=repo_types, schema_repo=schema_repo, schema_ref=schema_ref,
        schema_dir=schema_dir, schema_source=schema_source, name=pre.get("name", schema_repo),
        namespaces=namespaces, types=types, exclude=exclude, baseline=baseline,
        severity=severity, severity_source=sev_src, options=options, ref_separator=sep,
        external=external, cross_repo=cross, digest=digest, digest_roles=digest_roles,
        templates_dir=schema_dir / pre.get("templates", "templates"), registry=registry,
        clones=clones, timeout=timeout, cache_dir=Path(cache_dir).expanduser(), notes=notes,
        schemas=schemas)
    cfg._schema_repo_source = schema_repo_src
    cfg._cache_source = cache_src
    cfg._clones_source = clones_src
    return cfg


def _pick(env: str, ws: dict, key: str, ws_path: str):
    if os.environ.get(env):
        return os.environ[env], env
    if ws.get(key) is not None:
        return str(ws[key]), f"{ws_path}:{key}"
    return None, ""


def _types(table, sname: str, notes: list[Note]) -> dict[str, TypeDef]:
    if not isinstance(table, dict) or not table:
        raise ConfigError(f"{sname}: declare at least one [types.<type>] table")
    out: dict[str, TypeDef] = {}
    fallbacks = []
    for name, d in table.items():
        w = f"{sname} [types.{name}]"
        if not isinstance(d, dict):
            raise ConfigError(f"{w} must be a table")
        _unknown(d, TYPE_KEYS, f"[types.{name}]", sname, notes)
        kind = _str(d, "kind", w)
        if kind not in ("canon", "work"):
            raise ConfigError(f"{w}: kind must be \"canon\" or \"work\"")
        t = TypeDef(name=name, kind=kind, schema=_str(d, "schema", w), identity=_str(d, "identity", w))
        fmt = _str(d, "id_format", w, required=False)
        t.id_format = IdPattern(fmt, w) if fmt else None
        try:
            t.id_legacy = [re.compile(p) for p in _strlist(d, "id_legacy", w)]
        except re.error as e:
            raise ConfigError(f"{w}: id_legacy: {e}") from None
        cite = _str(d, "cite", w, required=False)
        t.cite = IdPattern(cite, f"{w} cite", allowed=("n",)) if cite else None
        if t.cite and not (t.id_format and "n" in t.id_format.fields):
            raise ConfigError(f"{w}: `cite` needs an id_format with {{n}}")
        t.allocate = _str(d, "allocate", w, required=False)
        if t.id_format and "n" in t.id_format.fields:
            if t.allocate not in ("namespace", "month"):
                raise ConfigError(f"{w}: allocate must be \"namespace\" or \"month\"")
            if t.allocate == "month" and "yymm" not in t.id_format.fields:
                raise ConfigError(f"{w}: allocate = \"month\" needs {{yymm}} in id_format")
        t.fallback = bool(d.get("fallback", False))
        t.dirs = _strlist(d, "dirs", w, required=not t.fallback)
        t.retired_dir = _str(d, "retired_dir", w, required=False)
        t.archive_dir = _str(d, "archive_dir", w, required=False)
        t.scan_dirs = _strlist(d, "scan_dirs", w)
        t.layout = _str(d, "layout", w, required=False, default="either")
        if t.layout not in ("file", "dir", "either"):
            raise ConfigError(f"{w}: layout must be file, dir or either")
        status = d.get("status")
        if status == "none":
            pass
        elif isinstance(status, dict):
            t.status_field = _str(status, "field", f"{w}.status")
            vals = status.get("values")
            if not isinstance(vals, dict) or not vals:
                raise ConfigError(f"{w}.status: `values` must map each status to a role")
            for v, role in vals.items():
                if role not in ROLES:
                    raise ConfigError(f"{w}.status: {v} has unknown role {role!r} ({', '.join(ROLES)})")
            t.status_values = dict(vals)
        else:
            raise ConfigError(f"{w}: `status` must be a table or \"none\"")
        links = d.get("links") or {}
        if not isinstance(links, dict):
            raise ConfigError(f"{w}: `links` must be a table")
        for f, m in links.items():
            if m not in MEANINGS:
                raise ConfigError(f"{w}.links: {f} has unknown meaning {m!r} ({', '.join(MEANINGS)})")
        t.links = dict(links)
        has = set(t.links.values())
        if ("replaces" in has) != ("replaced-by" in has):
            raise ConfigError(f"{w}.links: `replaces` and `replaced-by` must be declared together")
        t.review_date = _str(d, "review_date", w, required=False)
        t.index = _str(d, "index", w, required=False)
        t.template = _str(d, "template", w, required=False)
        t.template_dir = _str(d, "template_dir", w, required=False)
        if t.fallback:
            fallbacks.append(name)
        out[name] = t
    if len(fallbacks) > 1:
        raise ConfigError(f"{sname}: at most one type may set fallback = true ({', '.join(fallbacks)})")
    return out


def _check_options(options: dict, notes: list[Note]):
    allow = options.get("id-unique", {}).get("allow", [])
    if not isinstance(allow, list) or not all(isinstance(a, str) for a in allow):
        raise ConfigError("[checks.id-unique] allow: a list of citations, such as [\"ADR-0023\"]")
    for p in options.get("no-work-ids", {}).get("patterns", []):
        if not isinstance(p, dict) or "regex" not in p:
            raise ConfigError("[checks.no-work-ids] patterns: each entry needs `regex` and `what`")
        try:
            re.compile(p["regex"])
        except re.error as e:
            raise ConfigError(f"[checks.no-work-ids] regex {p['regex']!r}: {e}") from None


def _registry(schemas_dir: Path, sname: str):
    if not schemas_dir.is_dir():
        raise ConfigError(f"{sname}: schemas directory {schemas_dir} not found")
    resources, by_file = [], {}
    for path in sorted(schemas_dir.glob("*.schema.json")):
        try:
            schema = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            raise ConfigError(f"{path}: invalid JSON: {e}") from None
        by_file[path.name] = schema
        if "$id" in schema:
            resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources), by_file


def _status_agrees(t: TypeDef, schema: dict, sname: str, notes: list[Note]):
    if not t.status_field:
        return
    enum = ((schema.get("properties") or {}).get(t.status_field) or {}).get("enum")
    if enum is None:
        return
    if set(enum) != set(t.status_values):
        notes.append(Note(
            sname,
            f"type {t.name}: schema enum {sorted(enum)} and status.values {sorted(t.status_values)} disagree",
            "make the schema's enum and dox.toml's [types.<type>.status.values] list the same values"))
