"""`dox retire`: one command for the retirement checklist.

Sets the status, writes the link fields on both sides, moves the document
to its type's `retired_dir`, and rewrites the repository's relative
Markdown links to the new path, including the moved document's own links.
Frontmatter is edited as text, so the author's formatting and comments
survive. Links from other homes cannot be rewritten here; the
`moved <old> <new>` lines are the moves map for that.
"""

from __future__ import annotations

import datetime
import json
import os
import posixpath
import re

import yaml

from dox import globs, proc
from dox.errors import ConfigError
from dox.refs import FENCE, INLINE_CODE, MD_LINK, is_external_target


def run(ctx, ref: str, *, by: str | None, absorbed: list[str], status: str | None,
        date: datetime.date, dry_run: bool) -> list[str]:
    cfg, corpus, res = ctx.cfg, ctx.corpus, ctx.resolver
    doc = _one(res, ref, "document to retire")
    t = doc.type
    if not by and not absorbed:
        raise ConfigError("say what retires it: --by <ref> or --absorbed-into <ref>")
    if not t.retired_dir or not globs.is_literal(t.retired_dir):
        raise ConfigError(f"{doc.stem}: type {t.name} has no literal retired_dir to move it to")
    retired = globs.norm(t.retired_dir)
    if doc.role == "retired" or (doc.docpath or "").startswith(retired + "/"):
        raise ConfigError(f"{doc.stem} is already retired ({doc.status}, {doc.path})")

    edits: dict[str, list] = {doc.path: []}
    succ = None
    if by:
        succ = _one(res, by, "successor")
        if succ is doc:
            raise ConfigError("a document cannot replace itself")
        rb = t.fields_meaning("replaced-by")
        if not rb:
            raise ConfigError(f"type {t.name} declares no `replaced-by` link field")
        edits[doc.path].append(("set", rb[0], succ.stem))
        rp = succ.type.fields_meaning("replaces")
        if rp:
            edits.setdefault(succ.path, []).append(("append", rp[0], doc.stem))
    if absorbed:
        ab = t.fields_meaning("absorbed-into")
        if not ab:
            raise ConfigError(f"type {t.name} declares no `absorbed-into` link field")
        for a in absorbed:
            edits[doc.path].append(("append", ab[0], a))

    new_status = status or (t.retire.get("by") if by else t.retire.get("absorbed"))
    if by and not new_status:
        raise ConfigError(f"type {t.name} has no [types.{t.name}.retire] by status; pass --status")
    if new_status:
        if t.status_field is None:
            raise ConfigError(f"type {t.name} has no status field")
        if new_status not in t.status_values:
            raise ConfigError(f"{new_status!r} is not a {t.name} status ({', '.join(t.status_values)})")
        if t.role(new_status) != "retired" and not absorbed:
            raise ConfigError(f"{new_status!r} is not a retired status for {t.name}")
        edits[doc.path].insert(0, ("set", t.status_field, new_status))
    for path in edits:
        d = corpus.doc_for(path)
        if d is not None and "updated" in d.data:
            edits[path].append(("set", "updated", date.isoformat()))

    old = doc.dir
    new = posixpath.join(cfg.to_repo(retired), posixpath.basename(old))
    if (cfg.repo_root / new).exists():
        raise ConfigError(f"{new} already exists")

    def moved(p: str) -> str:
        if p == old:
            return new
        if p.startswith(old + "/"):
            return new + p[len(old):]
        return p

    texts: dict[str, str] = {}
    for f in corpus.md:
        text = corpus.text(f)
        if f in edits:
            text = _edit_frontmatter(text, edits[f])
        text = _rewrite_links(text, corpus.frontmatter(f).body_line, f, moved(f), moved)
        if text != corpus.text(f):
            texts[f] = text

    out = [f"{'would move' if dry_run else 'moved'}\t{old}\t{new}"]
    out += [f"{'would edit' if dry_run else 'edited'}\t{moved(f)}" for f in sorted(texts)]
    if dry_run:
        return out
    root = cfg.repo_root
    for f, text in texts.items():
        (root / f).write_text(text, encoding="utf-8")
    (root / new).parent.mkdir(parents=True, exist_ok=True)
    tracked = proc.git(root, "ls-files", "--error-unmatch", old).returncode == 0
    if tracked:
        r = proc.git(root, "mv", old, new)
        if r.returncode:
            raise ConfigError(f"git mv {old} {new}: {r.stderr.strip()}")
    else:
        os.rename(root / old, root / new)
    return out


def _one(res, raw: str, what: str):
    ref = res.parse(raw)
    if ref.kind in ("external", "url", "bad-home") or ref.home is not None:
        raise ConfigError(f"{what} {raw!r} must be a document in this repository")
    got = res.resolve(ref)
    if isinstance(got, str):
        got = res.doc_at(got)
        got = [got] if got else None
    if not got:
        raise ConfigError(f"{what} {raw!r}: no such document")
    if len(got) > 1:
        raise ConfigError(f"{what} {raw!r} is ambiguous ({', '.join(d.stem for d in got)}); use the full stem")
    return got[0]


# ---- frontmatter, as text

PLAIN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/@+-]*(?:#[A-Za-z0-9._-]+)?$")


def _q(v: str) -> str:
    return v if PLAIN.match(v) else json.dumps(v)


def _entry(lines: list[str], key: str) -> tuple[int, int] | None:
    """[start, end) of a top-level key's lines, including its block items."""
    rx = re.compile(rf"^{re.escape(key)}\s*:")
    for i, line in enumerate(lines):
        if rx.match(line):
            j = i + 1
            while j < len(lines) and (lines[j].startswith((" ", "\t", "-")) or not lines[j].strip()):
                if not lines[j].strip() and (j + 1 >= len(lines) or not lines[j + 1].startswith((" ", "\t", "-"))):
                    break
                j += 1
            return i, j
    return None


def _edit_frontmatter(text: str, ops: list) -> str:
    if not text.startswith("---\n"):
        raise ConfigError("cannot edit a document without YAML frontmatter")
    end = re.search(r"^---[ \t]*$", text[4:], re.M)
    raw, rest = text[4 : 4 + end.start()], text[4 + end.start():]
    lines = raw.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for op, key, value in ops:
        span = _entry(lines, key)
        if op == "set":
            new = [f"{key}: {_q(value)}"]
        else:
            items = []
            if span:
                cur = yaml.safe_load("\n".join(lines[span[0]:span[1]])) or {}
                v = cur.get(key)
                items = v if isinstance(v, list) else ([v] if v is not None else [])
            if value not in items:
                items.append(value)
            new = [f"{key}: [{', '.join(_q(str(x)) for x in items)}]"]
        if span:
            lines[span[0]:span[1]] = new
        else:
            lines += new
    return "---\n" + "\n".join(lines) + "\n" + rest


# ---- links


def _rewrite_links(text: str, body_line: int, old_file: str, new_file: str, moved) -> str:
    """Rewrite relative links in a file's body for the move, and for the file's own move."""
    lines = text.split("\n")
    old_dir = posixpath.dirname(old_file)
    new_dir = posixpath.dirname(new_file) or "."
    in_fence = None

    def fix(m: re.Match) -> str:
        target = m.group(1)
        if is_external_target(target):
            return m.group(0)
        cut = min([i for i in (target.find("#"), target.find("?")) if i >= 0], default=len(target))
        path, suffix = target[:cut], target[cut:]
        if not path:
            return m.group(0)
        slash = "/" if path.endswith("/") else ""
        if path.startswith("/"):
            resolved = posixpath.normpath(path.lstrip("/"))
        else:
            resolved = posixpath.normpath(posixpath.join(old_dir, path))
        if resolved == ".." or resolved.startswith("../"):
            return m.group(0)
        dest = moved(resolved)
        if dest == resolved and new_file == old_file:
            return m.group(0)
        if path.startswith("/"):
            new_path = "/" + dest
        else:
            new_path = posixpath.relpath(dest, new_dir)
        if new_path.rstrip("/") == path.rstrip("/"):
            return m.group(0)
        start, stop = m.span(1)
        whole = m.group(0)
        off = m.start(0)
        return whole[: start - off] + new_path + slash + suffix + whole[stop - off:]

    for i in range(max(body_line - 1, 0), len(lines)):
        line = lines[i]
        fm = FENCE.match(line)
        if fm:
            in_fence = fm.group(2) if in_fence is None else (None if fm.group(2) == in_fence else in_fence)
            continue
        if in_fence:
            continue
        codes = [c.span() for c in INLINE_CODE.finditer(line)]
        lines[i] = MD_LINK.sub(lambda m: m.group(0) if any(a <= m.start() < b for a, b in codes) else fix(m), line)
    return "\n".join(lines)
