"""`dox next` and `dox new`: allocate an id against the remote, scaffold it.

Allocation counts every path that can hold an id of the type, anywhere in
the repository: the working tree (tracked and untracked), origin's default
branch after a fetch, and the files of open pull requests. A stale clone
therefore cannot hand out an id that main or an open PR already holds.
"""

from __future__ import annotations

import datetime
import re
from pathlib import Path

from dox import globs, proc, remote
from dox.errors import ConfigError, DoxError
from dox.model import SLUG_RX, TypeDef


def slugify(s: str) -> str:
    s = re.sub(r"[^a-z0-9-]+", "-", s.lower().replace(" ", "-"))
    return re.sub(r"-+", "-", s).strip("-")


def _working_paths(repo: Path) -> list[str]:
    r = proc.git(repo, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    return [p for p in r.stdout.split("\0") if p] if r.returncode == 0 else []


def taken_paths(cfg, offline: bool) -> tuple[list[str], list[str]]:
    repo = cfg.repo_root
    paths = _working_paths(repo)
    notes = []
    if offline:
        notes.append("offline: allocated against the working tree only")
        return paths, notes
    url = remote.origin_url(repo)
    if not url:
        notes.append("no origin remote: allocated against the working tree only")
        return paths, notes
    remote.fetch_origin(repo, cfg.timeout)
    ref = remote.default_ref(repo)
    if ref:
        paths += remote.tree_paths(repo, ref)
    else:
        notes.append("origin has no default branch yet")
    slug = remote.github_slug(url)
    if slug:
        paths += remote.open_pr_paths(slug, cfg.timeout)
    else:
        notes.append(f"origin {url} is not on GitHub: open pull requests not scanned")
    return paths, notes


def next_number(t: TypeDef, paths: list[str], yymm: str) -> int:
    best = 0
    for p in paths:
        for comp in p.split("/"):
            stem = comp[:-3] if comp.endswith(".md") else comp
            got = t.id_format.match(stem) if t.id_format else None
            if not got:
                continue
            if t.allocate == "month" and got.get("yymm") != yymm:
                continue
            best = max(best, int(got["n"]))
    return best + 1


def allocatable(cfg, tname: str) -> TypeDef:
    if tname not in cfg.types:
        raise ConfigError(f"unknown type {tname!r}; the schema repo declares: {', '.join(cfg.types)}")
    if tname not in cfg.repo_types:
        raise ConfigError(f"this repository does not hold {tname} documents (.dox.toml types: "
                          f"{', '.join(cfg.repo_types)})")
    return cfg.types[tname]


def next_id(cfg, tname: str, slug: str | None, date: datetime.date, offline: bool):
    t = allocatable(cfg, tname)
    if not t.id_format or "n" not in t.id_format.fields:
        raise ConfigError(f"{tname} ids are chosen by the author; there is nothing to allocate")
    yymm = date.strftime("%y%m")
    paths, notes = taken_paths(cfg, offline)
    n = next_number(t, paths, yymm)
    ident = t.id_format.render(n=n, yymm=yymm, slug=slug) if slug else t.id_format.prefix(n=n, yymm=yymm)
    return ident, notes


def new_doc(cfg, tname: str, slug: str, *, as_dir: bool | None, title: str | None,
            date: datetime.date, offline: bool):
    t = allocatable(cfg, tname)
    slug = slugify(slug)
    if not re.fullmatch(SLUG_RX, slug):
        raise ConfigError(f"slug {slug!r} must be kebab-case")
    notes: list[str] = []
    if t.id_format and "n" in t.id_format.fields:
        ident, notes = next_id(cfg, tname, slug, date, offline)
    elif t.id_format:
        ident = t.id_format.render(yymm=date.strftime("%y%m"), slug=slug)
    else:
        ident = slug
    if as_dir is None:
        as_dir = t.layout == "dir"
    if as_dir and t.layout == "file":
        raise ConfigError(f"{tname} documents are single files")
    if not as_dir and t.layout == "dir":
        raise ConfigError(f"{tname} documents are directories")
    target = next((d for d in t.dirs if globs.is_literal(d) and not globs.norm(d).endswith(".md")), None)
    if target is None and not any(globs.norm(d).endswith(f"/{ident}.md") or globs.norm(d) == f"{ident}.md"
                                  for d in t.dirs):
        raise ConfigError(f"{tname}: no literal directory in `dirs` to create the document in")
    base = cfg.docs_root / globs.norm(target or "")
    title = title or slug.replace("-", " ").capitalize()
    values = {"ID": ident, "DATE": date.isoformat(), "TITLE": title, "SLUG": slug,
              "HOME": cfg.namespace, "TYPE": tname}

    def render(text: str) -> str:
        return re.sub(r"\{\{(ID|DATE|TITLE|SLUG|HOME|TYPE)\}\}", lambda m: values[m.group(1)], text)

    if as_dir:
        dest = base / ident
        if dest.exists():
            raise DoxError(f"refusing to overwrite {dest}")
        if not t.template_dir:
            raise ConfigError(f"{tname}: the schema repo has no template_dir for directory documents")
        src = cfg.templates_dir / t.template_dir
        if not src.is_dir():
            raise ConfigError(f"template directory {src} not found")
        for f in sorted(src.rglob("*")):
            if f.is_file():
                out = dest / f.relative_to(src)
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(render(f.read_text(encoding="utf-8")), encoding="utf-8")
        return dest, notes
    dest = base / f"{ident}.md"
    named = [globs.norm(d) for d in t.dirs if globs.is_literal(d) and globs.norm(d).endswith(".md")]
    for d in named:
        if d.rsplit("/", 1)[-1] == f"{ident}.md":
            dest = cfg.docs_root / d
    if dest.exists():
        raise DoxError(f"refusing to overwrite {dest}")
    if not t.template:
        raise ConfigError(f"{tname}: the schema repo has no template")
    src = cfg.templates_dir / t.template
    if not src.is_file():
        raise ConfigError(f"template {src} not found")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(render(src.read_text(encoding="utf-8")), encoding="utf-8")
    return dest, notes
