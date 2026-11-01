"""The reference graph: typed frontmatter links and Markdown links.

Nodes are this repository's documents, plus one node per ref into another
home. Links inside generated index blocks are left out, so a generated
index does not hide an orphan.
"""

from __future__ import annotations

import json
import posixpath

from dox.index import BEGIN, END
from dox.refs import body, is_external_target, markdown_links, ref_values

LIFECYCLE = ("replaces", "replaced-by", "absorbed-into")


def build(ctx) -> dict:
    cfg, corpus, res = ctx.cfg, ctx.corpus, ctx.resolver
    sep = cfg.ref_separator
    nodes = {}
    for d in corpus.docs:
        nodes[d.stem] = {"id": d.stem, "type": d.type.name, "kind": d.type.kind, "path": d.path,
                         "title": d.title, "status": d.status, "role": d.role, "home": cfg.namespace}
    edges = []

    def add(src, dst, via, meaning, path, line):
        if src != dst:
            edges.append({"from": src, "to": dst, "via": via, "meaning": meaning, "path": path, "line": line})

    for d in corpus.docs:
        for field, meaning, value in ref_values(d):
            ref = res.parse(value)
            line = d.fm.line_of(field)
            if ref.kind in ("external", "url", "bad-home"):
                continue
            if ref.home is not None:
                ext = f"{ref.home}{sep}{ref.value}"
                nodes.setdefault(ext, {"id": ext, "type": None, "kind": None, "path": None, "title": ext,
                                       "status": None, "role": None, "home": ref.home})
                add(d.stem, ext, field, meaning, d.path, line)
                continue
            got = res.resolve(ref)
            target = None
            if isinstance(got, list) and len(got) == 1:
                target = got[0]
            elif isinstance(got, str):
                target = res.doc_at(got)
            if target:
                add(d.stem, target.stem, field, meaning, d.path, line)

    for f in corpus.md:
        src_doc = corpus.doc_for(f)
        src = src_doc.stem if src_doc else f"file:{f}"
        text, start = body(corpus, f)
        text = _strip_generated(text)
        for line, target in markdown_links(text, start):
            if is_external_target(target):
                continue
            path = target.split("#", 1)[0].split("?", 1)[0]
            if not path:
                continue
            p = posixpath.normpath(path.lstrip("/") if path.startswith("/")
                                   else posixpath.join(posixpath.dirname(f), path))
            dst = res.doc_at(p.rstrip("/"))
            if dst:
                add(src, dst.stem, "link", "link", f, line)

    edges.sort(key=lambda e: (e["from"], e["to"], e["via"], e["line"]))
    backlinks: dict[str, list[str]] = {}
    for e in edges:
        backlinks.setdefault(e["to"], [])
        if e["from"] not in backlinks[e["to"]]:
            backlinks[e["to"]].append(e["from"])
    orphans = sorted(n for n, v in nodes.items()
                     if v["path"] and v["kind"] == "canon" and not backlinks.get(n))

    succ = {}
    for e in edges:
        if e["meaning"] == "replaced-by":
            succ.setdefault(e["from"], e["to"])
        elif e["meaning"] == "replaces":
            succ.setdefault(e["to"], e["from"])
    has_pred = set(succ.values())
    chains = []
    for start in sorted(succ):
        if start in has_pred:
            continue
        chain, cur = [start], start
        while cur in succ and succ[cur] not in chain:
            cur = succ[cur]
            chain.append(cur)
        chains.append(chain)

    gone = ("retired", "closed")
    into_retired = [
        e for e in edges
        if e["meaning"] not in LIFECYCLE and nodes.get(e["to"], {}).get("role") in gone
        and (nodes.get(e["from"], {}).get("role") not in gone)
    ]
    return {
        "home": cfg.namespace,
        "nodes": [nodes[k] for k in sorted(nodes)],
        "edges": edges,
        "backlinks": {k: sorted(v) for k, v in sorted(backlinks.items())},
        "orphans": orphans,
        "chains": chains,
        "into_retired": into_retired,
    }


def _strip_generated(text: str) -> str:
    out, inside = [], False
    for line in text.split("\n"):
        if line.strip() == BEGIN:
            inside = True
        if inside:
            out.append("")  # keep line numbers
        else:
            out.append(line)
        if line.strip() == END:
            inside = False
    return "\n".join(out)


def to_json(g: dict) -> str:
    return json.dumps(g, indent=2, sort_keys=False)


def _ids(g: dict) -> dict[str, str]:
    return {n["id"]: f"n{i}" for i, n in enumerate(g["nodes"])}


def to_mermaid(g: dict) -> str:
    ids = _ids(g)
    lines = ["graph LR"]
    for n in g["nodes"]:
        label = n["id"].replace('"', "'")
        shape = f'(["{label}"])' if n["path"] is None else f'["{label}"]'
        lines.append(f"  {ids[n['id']]}{shape}")
    seen = set()
    for e in g["edges"]:
        if e["from"] not in ids:
            continue
        key = (e["from"], e["to"], e["meaning"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"  {ids[e['from']]} -->|{e['meaning']}| {ids[e['to']]}")
    return "\n".join(lines) + "\n"


def to_dot(g: dict) -> str:
    ids = _ids(g)
    lines = ["digraph dox {", "  rankdir=LR;"]
    for n in g["nodes"]:
        label = n["id"].replace('"', '\\"')
        style = ", style=dashed" if n["path"] is None else ""
        lines.append(f'  {ids[n["id"]]} [label="{label}"{style}];')
    seen = set()
    for e in g["edges"]:
        if e["from"] not in ids:
            continue
        key = (e["from"], e["to"], e["meaning"])
        if key in seen:
            continue
        seen.add(key)
        lines.append(f'  {ids[e["from"]]} -> {ids[e["to"]]} [label="{e["meaning"]}"];')
    lines.append("}")
    return "\n".join(lines) + "\n"
