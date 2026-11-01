"""Generated indexes and the agent digest, and the index-fresh check.

Generated text sits between BEGIN and END markers; everything outside them
belongs to the author. Output is deterministic: no dates, sorted rows.
"""

from __future__ import annotations

import posixpath

from dox import globs
from dox.findings import Finding

BEGIN = "<!-- dox:index:begin -->"
END = "<!-- dox:index:end -->"


def _cell(s) -> str:
    return str(s).replace("|", "\\|").replace("\n", " ")


def _link(from_file: str, doc) -> str:
    target = doc.dir + "/" if doc.is_dir else doc.path
    return posixpath.relpath(target, posixpath.dirname(from_file) or ".") + ("/" if doc.is_dir else "")


def targets(ctx) -> dict[str, str]:
    """Repo-relative file path -> the block dox would generate in it."""
    cfg, corpus = ctx.cfg, ctx.corpus
    out: dict[str, str] = {}
    for tname in cfg.repo_types:
        t = cfg.types[tname]
        if not t.index:
            continue
        for d in t.dirs:
            if not globs.is_literal(d) or globs.norm(d).endswith(".md"):
                continue
            ddir = globs.norm(d)
            if not (cfg.docs_root / ddir).is_dir():
                continue  # nothing of this type here yet
            index_path = cfg.to_repo(posixpath.join(ddir, t.index) if ddir else t.index)
            docs = [doc for doc in corpus.docs if doc.type is t and doc.docpath is not None
                    and (doc.docpath.rsplit("/", 1)[0] if "/" in doc.docpath else "") == ddir]
            rows = ["| Id | Title | Status |", "|----|-------|--------|"]
            for doc in sorted(docs, key=lambda x: x.stem):
                status = doc.status if doc.status is not None else ""
                rows.append(f"| [{doc.stem}]({_link(index_path, doc)}) | {_cell(doc.title)} | {_cell(status)} |")
            if not docs:
                rows = ["_No documents._"]
            out[index_path] = "\n".join(rows) + "\n"
    if cfg.digest:
        path = cfg.to_repo(globs.norm(cfg.digest))
        lines = []
        for tname in cfg.repo_types:
            t = cfg.types[tname]
            docs = [d for d in corpus.docs if d.type is t
                    and (d.role in cfg.digest_roles or not t.status_field)]
            if not docs:
                continue
            lines.append(f"## {tname}\n")
            for d in sorted(docs, key=lambda x: x.stem):
                status = f" ({d.status})" if d.status is not None else ""
                lines.append(f"- [{d.stem}]({_link(path, d)}): {_cell(d.title)}{status}")
            lines.append("")
        out[path] = "\n".join(lines).rstrip("\n") + "\n" if lines else "_No documents._\n"
    return out


def render(existing: str | None, block: str) -> str:
    wrapped = f"{BEGIN}\n{block}{END}"
    if existing is None:
        return wrapped + "\n"
    b, e = existing.find(BEGIN), existing.find(END)
    if b >= 0 and e > b:
        return existing[:b] + wrapped + existing[e + len(END):]
    sep = "" if existing.endswith("\n\n") or not existing else ("\n" if existing.endswith("\n") else "\n\n")
    return existing + sep + wrapped + "\n"


def check_index_fresh(ctx):
    root = ctx.cfg.repo_root
    for path, block in sorted(targets(ctx).items()):
        f = root / path
        current = f.read_text(encoding="utf-8") if f.is_file() else None
        if current is None:
            yield Finding("index-fresh", path, 1, "generated index missing", "run `dox index` and commit the result")
        elif render(current, block) != current:
            what = "is stale" if BEGIN in current else "has no dox index block"
            yield Finding("index-fresh", path, 1, f"generated index {what}", "run `dox index` and commit the result")


def write(ctx) -> list[str]:
    changed = []
    root = ctx.cfg.repo_root
    for path, block in sorted(targets(ctx).items()):
        f = root / path
        current = f.read_text(encoding="utf-8") if f.is_file() else None
        new = render(current, block)
        if new != current:
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_text(new, encoding="utf-8")
            changed.append(path)
    return changed
