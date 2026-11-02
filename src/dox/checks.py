"""The offline checks. Each yields findings; lint assigns severity."""

from __future__ import annotations

import datetime
import difflib
import posixpath
import re
from functools import lru_cache

from jsonschema import Draft202012Validator, FormatChecker

from dox import globs
from dox.config import Config
from dox.docs import Corpus, Doc
from dox.findings import Finding
from dox.model import place_match
from dox.refs import Resolver, body, is_external_target, markdown_links, ref_values


class Context:
    def __init__(self, cfg: Config, corpus: Corpus):
        self.cfg = cfg
        self.corpus = corpus
        self.resolver = Resolver(corpus)
        self.today = datetime.date.today()

    @lru_cache(maxsize=None)
    def validator(self, schema_file: str) -> Draft202012Validator:
        schema = self.cfg.schemas[schema_file]
        return Draft202012Validator(schema, registry=self.cfg.registry, format_checker=FormatChecker())


# ---- config


def check_config(ctx: Context):
    for n in ctx.cfg.notes:
        yield Finding("config", n.path, 0, n.message, n.fix)


# ---- schema


def _own(ctx: Context):
    """Documents of types this repository holds. A document of any other
    type is reported once, by placement or no-plans-in-canon."""
    return [d for d in ctx.corpus.docs if d.type.name in ctx.cfg.repo_types]


def check_schema(ctx: Context):
    for doc in _own(ctx):
        fm = doc.fm
        if fm.data is None:
            if fm.error:
                yield Finding("schema", doc.path, 1, fm.error,
                              "fix the YAML between the opening and closing `---` lines")
            else:
                yield Finding("schema", doc.path, 1, f"no YAML frontmatter ({doc.type.name})",
                              f"start the file with `---`, the fields the {doc.type.schema} schema "
                              f"requires, and `---`; `dox new {doc.type.name} <slug>` shows a template")
            continue
        errors = list(ctx.validator(doc.type.schema).iter_errors(fm.data))
        # A failing field fails the whole allOf branch that declares it, and
        # draft 2020-12 then drops that branch's annotations, so
        # unevaluatedProperties also reports every valid sibling. Show it
        # only when it is the sole problem.
        real = [e for e in errors if e.validator != "unevaluatedProperties"]
        for e in sorted(real or errors, key=lambda e: (list(map(str, e.absolute_path)), e.message)):
            yield _schema_finding(doc, e)


def _schema_finding(doc: Doc, e) -> Finding:
    where = "/".join(map(str, e.absolute_path)) or "(root)"
    top = e.absolute_path[0] if e.absolute_path else None
    line = doc.fm.line_of(top) if top is not None else 1
    v = e.validator
    msg = e.message if len(e.message) <= 200 else e.message[:197] + "..."
    fix = f"edit the frontmatter so it validates against {doc.type.schema}"
    if v == "required":
        m = re.match(r"'([^']+)' is a required property", e.message)
        field = m.group(1) if m else "the field"
        fix = f"add `{field}:` to the frontmatter"
    elif v == "enum":
        fix = f"set `{where}` to one of: {', '.join(map(str, e.validator_value))}"
    elif v in ("unevaluatedProperties", "additionalProperties"):
        fix = "remove the unexpected field, or rename it to the name the schema declares"
    elif v == "pattern":
        fix = f"change `{where}` to match {e.validator_value}"
    elif v == "format":
        fix = f"write `{where}` as a valid {e.validator_value}"
    elif v == "type":
        fix = f"make `{where}` a {e.validator_value}"
    elif v == "not" and isinstance(e.validator_value, dict) and "required" in e.validator_value:
        fields = ", ".join(f"`{f}`" for f in e.validator_value["required"])
        msg = f"{fields} not allowed with these field values"
        fix = f"remove {fields}, or set the status that requires it"
    return Finding("schema", doc.path, line, f"{where}: {msg}", fix)


# ---- id-unique


def check_id_unique(ctx: Context):
    corpus = ctx.corpus
    own = _own(ctx)
    for doc in own:
        ident = doc.ident
        if isinstance(ident, str) and ident != doc.stem:
            what = "directory name" if doc.is_dir else "file stem"
            yield Finding("id-unique", doc.path, doc.fm.line_of(doc.type.identity),
                          f"`{doc.type.identity}` is {ident!r} but the {what} is {doc.stem!r}",
                          f"set `{doc.type.identity}: {doc.stem}`, or `git mv` the document to {ident!r}")
    by_stem: dict[str, list[Doc]] = {}
    for doc in own:
        by_stem.setdefault(doc.stem, []).append(doc)
    for stem, docs in sorted(by_stem.items()):
        if len(docs) > 1:
            first = docs[0]
            for d in docs[1:]:
                yield Finding("id-unique", d.path, 1, f"id {stem!r} is also used by {first.path}",
                              "keep one; if both are live, give this one a new id "
                              f"(`dox next {d.type.name}`) and update links to it")
    by_num: dict[tuple, list[Doc]] = {}
    for doc in own:
        t = doc.type
        if not t.id_format or "n" not in t.id_format.fields:
            continue
        got = t.id_format.match(doc.stem)
        if not got:
            continue
        key = (t.name, got.get("yymm"), int(got["n"]))
        by_num.setdefault(key, []).append(doc)
    allow = set(ctx.cfg.option("id-unique", "allow", []) or [])
    for (tname, yymm, n), docs in sorted(by_num.items(), key=lambda kv: (kv[0][0], kv[0][1] or "", kv[0][2])):
        stems = sorted({d.stem for d in docs})
        if len(stems) < 2:
            continue
        t = ctx.cfg.types[tname]
        label = t.cite.render(n=n) if t.cite else t.id_format.prefix(n=n, yymm=yymm)
        if label in allow:
            continue  # a legacy shared number, kept on purpose
        for d in docs:
            others = ", ".join(s for s in stems if s != d.stem)
            yield Finding("id-unique", d.path, 1, f"number {label} is shared with {others}",
                          f"cite these by full stem (`{ctx.cfg.namespace}{ctx.cfg.ref_separator}{d.stem}`); "
                          f"new documents take `dox next {tname}`")


# ---- links-local


def check_links_local(ctx: Context):
    cfg, corpus, res = ctx.cfg, ctx.corpus, ctx.resolver
    forbid = cfg.option("links-local", "forbid", []) or []
    basenames: dict[str, list[str]] = {}
    for f in corpus.files:
        basenames.setdefault(posixpath.basename(f), []).append(f)

    for f in corpus.md:
        text = corpus.text(f)
        for i, line in enumerate(text.splitlines(), 1):
            for needle in forbid:
                if needle in line:
                    yield Finding("links-local", f, i, f"forbidden reference {needle}",
                                  f"remove the reference; [checks.links-local] forbid lists {needle!r}")
        btext, start = body(corpus, f)
        for line, target in markdown_links(btext, start):
            if is_external_target(target):
                continue
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not path:
                continue
            if path.startswith("/"):
                resolved = posixpath.normpath(path.lstrip("/"))
            else:
                resolved = posixpath.normpath(posixpath.join(posixpath.dirname(f), path))
            if resolved == ".." or resolved.startswith("../"):
                yield Finding("links-local", f, line, f"link leaves the repository: {target}",
                              "link to the other repository by its forge URL instead of a relative path")
                continue
            if resolved in corpus.file_set or (cfg.repo_root / resolved).exists():
                continue
            fix = "fix the link target or remove the link"
            cands = basenames.get(posixpath.basename(resolved), [])
            if len(cands) == 1:
                rel = posixpath.relpath(cands[0], posixpath.dirname(f) or ".")
                fix = f"change the link to `{rel}`"
            yield Finding("links-local", f, line, f"relative link does not resolve: {target}", fix)

    for doc in _own(ctx):
        for field, meaning, value in ref_values(doc):
            ref = res.parse(value)
            line = doc.fm.line_of(field)
            if ref.kind in ("external", "url"):
                continue
            if ref.kind == "bad-home":
                yield Finding("links-local", doc.path, line, f"{field}: unknown home {ref.home!r} in {value!r}",
                              f"use a home from the schema repo's [namespaces]: {', '.join(sorted(cfg.namespaces))}")
                continue
            if ref.home is not None:
                continue  # another home: links-remote
            got = res.resolve(ref)
            if got is None:
                fix = "fix the ref, or remove it"
                close = difflib.get_close_matches(ref.value, list(res.by_ident), n=1)
                if close:
                    fix = f"did you mean {close[0]!r}?"
                yield Finding("links-local", doc.path, line, f"{field}: {value!r} does not resolve", fix)
            elif isinstance(got, list) and len(got) > 1:
                yield Finding("links-local", doc.path, line,
                              f"{field}: {value!r} is ambiguous ({', '.join(d.stem for d in got)})",
                              "cite by full stem")

    yield from _pairs(ctx)


def _single(res: Resolver, value: str) -> Doc | None:
    ref = res.parse(value)
    if ref.kind not in ("cite", "id", "path") or ref.home is not None:
        return None
    got = res.resolve(ref)
    if isinstance(got, list):
        return got[0] if len(got) == 1 else None
    if isinstance(got, str):
        return res.doc_at(got)
    return None


def _pairs(ctx: Context):
    res = ctx.resolver
    sep = ctx.cfg.ref_separator
    for a in ctx.corpus.docs:
        for field, meaning, value in ref_values(a):
            if meaning not in ("replaces", "replaced-by"):
                continue
            b = _single(res, value)
            if b is None or b is a:
                continue
            back = "replaced-by" if meaning == "replaces" else "replaces"
            back_fields = b.type.fields_meaning(back)
            if not back_fields:
                continue
            if any(_single(res, v) is a for f, m, v in ref_values(b) if m == back):
                continue
            yield Finding("links-local", b.path, b.fm.line_of(back_fields[0]),
                          f"{a.stem} says `{field}: {value}` but {b.stem} has no `{back_fields[0]}` back to it",
                          f"add `{back_fields[0]}: {a.stem}` to {b.path}, or remove `{field}` from {a.path}")


# ---- placement


def check_placement(ctx: Context):
    cfg, corpus = ctx.cfg, ctx.corpus
    for doc in corpus.docs:
        t = doc.type
        if t.name not in cfg.repo_types:
            if t.kind == "work":
                continue  # no-plans-in-canon
            yield Finding("placement", doc.path, 1, f"{t.name} document, but .dox.toml types omit {t.name}",
                          f"add {t.name!r} to `types` in .dox.toml, or move it to the home that holds "
                          f"{t.name} documents")
            continue
        if doc.is_dir and t.layout == "file":
            yield Finding("placement", doc.path, 1, f"{t.name} documents are single files",
                          f"`git mv {doc.dir}/README.md {doc.dir}.md` and fold its parts in")
        if not doc.is_dir and t.layout == "dir":
            yield Finding("placement", doc.path, 1, f"{t.name} documents are directories",
                          f"`git mv {doc.path} {doc.path[:-3]}/README.md`")
        role = doc.role
        if t.fallback:
            if not (role == "retired" and t.retired_dir):
                continue
            want = [t.retired_dir]
        else:
            want = t.placement_globs(role)
        if doc.docpath is None:
            yield Finding("placement", doc.path, 1, f"{t.name} document outside the docs root {cfg.root!r}",
                          f"`git mv {doc.dir} {cfg.to_repo(_dest(want, ctx))}/`")
            continue
        if want and not place_match(want, doc.docpath):
            why = f"{t.name} with status {doc.status!r} ({role})" if role else f"{t.name} document"
            yield Finding("placement", doc.path, 1, f"{why}: belongs in {' or '.join(want)}",
                          f"`git mv {doc.dir} {cfg.to_repo(_dest(want, ctx))}/` and rewrite links to it")
    for f, d in sorted(corpus.part_of.items()):
        if d.type.name in cfg.repo_types and corpus.has_frontmatter(f):
            yield Finding("placement", f, 1, f"part of {d.stem} carries frontmatter",
                          f"remove the frontmatter; only {d.path} is the document")


def _dest(want: list[str], ctx: Context) -> str:
    pat = next((w for w in want if not globs.norm(w).endswith(".md")), want[0] if want else ".")
    return globs.norm(pat).replace("{yymm}", ctx.today.strftime("%y%m")) or "."


# ---- no-work-ids


def check_no_work_ids(ctx: Context):
    cfg, corpus = ctx.cfg, ctx.corpus
    if cfg.holds_work():
        return
    patterns = [(re.compile(p["regex"]), p.get("what", "work id"))
                for p in cfg.option("no-work-ids", "patterns", []) or []]
    exempt = cfg.option("no-work-ids", "exempt", []) or []
    if not patterns:
        return
    for f in corpus.md:
        if globs.match_any(exempt, f) or any(globs.match(p.rstrip("/") + "/**", f) for p in exempt):
            continue
        d = corpus.doc_for(f)
        if d and d.type.kind == "work":
            continue
        btext, start = body(corpus, f)
        for i, line in enumerate(btext.splitlines(), start):
            for rx, what in patterns:
                for m in rx.finditer(line):
                    yield Finding("no-work-ids", f, i, f"{what} {m.group(0)} in canon prose",
                                  "remove it: canon says what is true, not under which ticket or plan; "
                                  "cite canon, and keep the work id in the plan or PR")


# ---- no-plans-in-canon


def check_no_plans_in_canon(ctx: Context):
    cfg, corpus = ctx.cfg, ctx.corpus
    if cfg.holds_work():
        return
    work_types = sorted(t.name for t in cfg.types.values() if t.kind == "work")
    seen = set()
    for doc in corpus.docs:
        if doc.type.kind == "work":
            seen.add(doc.path)
            seen.update(doc.parts)
            yield Finding("no-plans-in-canon", doc.path, 1, f"{doc.type.name} document in a canon repository",
                          f"move it to the home that holds {doc.type.name} documents, or delete it if done")
    dirs = cfg.option("no-plans-in-canon", "dirs", []) or []
    for f in corpus.md:
        if f in seen or not globs.match_any(dirs, f):
            continue
        yield Finding("no-plans-in-canon", f, 1, "work file in a canon tree",
                      f"move it to the home that holds {'/'.join(work_types) or 'work'} documents, "
                      "or delete it if done")


OFFLINE = {
    "config": check_config,
    "schema": check_schema,
    "id-unique": check_id_unique,
    "links-local": check_links_local,
    "placement": check_placement,
    "no-work-ids": check_no_work_ids,
    "no-plans-in-canon": check_no_plans_in_canon,
}
