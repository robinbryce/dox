"""Rules documents: the shape check and the rules digest.

A rules document is a list of rules, one heading each. The schema repo's
`[rules]` says what a rule heading looks like and which fields each rule
carries. The digest is one line per rule (id and imperative), from the
rules files of other homes pinned in `.dox.toml` `[digest] include`, then
this repository's own, written between dox:digest markers so text around
it stays the author's.
"""

from __future__ import annotations

import base64
import posixpath
import re
from dataclasses import dataclass
from pathlib import Path

from dox import proc
from dox.config import Config, Include
from dox.docs import parse_frontmatter
from dox.errors import RemoteError
from dox.findings import Finding
from dox.index import render
from dox.refs import FENCE, MD_LINK, body

BEGIN = "<!-- dox:digest:begin -->"
END = "<!-- dox:digest:end -->"
UNPINNED = ("main", "master", "HEAD")


@dataclass
class Rule:
    id: str
    text: str
    line: int
    section: str


def parse(text: str, first_line: int, spec) -> tuple[list[Rule], list[tuple[int, str]]]:
    """Rules in a document body, and headings at the rule level that are not rules."""
    rules: list[Rule] = []
    bad: list[tuple[int, str]] = []
    marker = "#" * spec.level + " "
    upto = re.compile(rf"^#{{1,{spec.level}}} ")
    cur: Rule | None = None
    buf: list[str] = []
    in_fence = None

    def close():
        if cur is not None:
            cur.section = "\n".join(buf)
            rules.append(cur)

    for i, line in enumerate(text.splitlines(), first_line):
        m = FENCE.match(line)
        if m:
            in_fence = m.group(2) if in_fence is None else (None if m.group(2) == in_fence else in_fence)
        if in_fence is None and not m and upto.match(line):
            close()
            cur, buf = None, []
            if line.startswith(marker):
                hm = spec.heading.match(line)
                if hm:
                    cur = Rule(hm["id"], hm["text"].strip(), i, "")
                else:
                    bad.append((i, line.strip()))
            continue
        if cur is not None:
            buf.append(line)
    close()
    return rules, bad


def _rule_docs(ctx):
    spec = ctx.cfg.rules
    return [d for d in ctx.corpus.docs if d.type.name == spec.type and d.type.name in ctx.cfg.repo_types]


def check_rules_shape(ctx):
    spec = ctx.cfg.rules
    if spec is None:
        return
    for doc in _rule_docs(ctx):
        text, start = body(ctx.corpus, doc.path)
        rules, bad = parse(text, start, spec)
        for line, heading in bad:
            yield Finding("rules-shape", doc.path, line,
                          f"heading does not match the rule heading pattern: {heading}",
                          f"write rule headings as {spec.heading.pattern}, or use another heading level")
        seen: dict[str, int] = {}
        for r in rules:
            if r.id in seen:
                yield Finding("rules-shape", doc.path, r.line, f"rule id {r.id} is used twice",
                              f"give this rule a new id; {r.id} is at line {seen[r.id]}")
                continue
            seen[r.id] = r.line
            for name, rx in spec.fields:
                if not rx.search(r.section):
                    yield Finding("rules-shape", doc.path, r.line, f"rule {r.id} has no {name}",
                                  f"add a line matching {rx.pattern} to rule {r.id}")
            if spec.link and not MD_LINK.search(r.section):
                yield Finding("rules-shape", doc.path, r.line, f"rule {r.id} has no link",
                              f"link rule {r.id} to the document that carries its reasoning")


# ---- digest


def _fetch(cfg: Config, inc: Include) -> str:
    """The include's text at its pinned ref: sibling clone first, then the GitHub API."""
    repo = cfg.namespaces[inc.home]
    cache = cfg.cache_dir / "pinned" / repo / inc.ref / inc.path
    pinned = inc.ref not in UNPINNED
    if pinned and cache.is_file():
        return cache.read_text(encoding="utf-8")
    text = None
    clone = _clone(cfg, inc.home)
    if clone:
        proc.git(clone, "fetch", "--quiet", "--tags", "origin", timeout=cfg.timeout)
        for ref in (inc.ref, f"origin/{inc.ref}"):
            r = proc.git(clone, "show", f"{ref}:{inc.path}")
            if r.returncode == 0:
                text = r.stdout
                break
        if text is None:
            raise RemoteError(f"{inc.label}: not found in {clone}")
    else:
        r = proc.run(["gh", "api", f"repos/{repo}/contents/{inc.path}?ref={inc.ref}"],
                     timeout=cfg.timeout, env=proc.quiet_env())
        if r.returncode:
            raise RemoteError(f"{inc.label}: {(r.stderr or r.stdout).strip()}")
        import json
        try:
            text = base64.b64decode(json.loads(r.stdout)["content"]).decode("utf-8")
        except (ValueError, KeyError) as e:
            raise RemoteError(f"{inc.label}: unreadable GitHub API response: {e}") from None
    if pinned:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(text, encoding="utf-8")
    return text


def _clone(cfg: Config, home: str) -> Path | None:
    if not cfg.clones:
        return None
    owner, name = cfg.namespaces[home].split("/", 1)
    p = Path(cfg.clones.format(repo=name, home=home, owner=owner)).expanduser()
    return p if (p / ".git").exists() else None


def _section(label: str, link: str, version, rules: list[Rule]) -> list[str]:
    v = f", version {version}" if version else ""
    out = [f"[{label}{v}]({link})", ""]
    out += [f"- **{r.id}** {r.text}" for r in rules] or ["_No rules._"]
    out.append("")
    return out


def block(ctx) -> str:
    cfg, spec = ctx.cfg, ctx.cfg.rules
    out_dir = posixpath.dirname(cfg.digest_out) or "."
    lines: list[str] = []
    for inc in cfg.digest_include:
        text = _fetch(cfg, inc)
        fm = parse_frontmatter(text)
        rules, _ = parse(text, 1, spec)
        version = (fm.data or {}).get("version")
        url = (cfg.cross_repo or "").format(repo=cfg.namespaces[inc.home], ref=inc.ref, path=inc.path)
        lines += _section(f"{inc.home} {inc.path} @ {inc.ref}", url or inc.path, version, rules)
    for doc in sorted(_rule_docs(ctx), key=lambda d: d.path):
        text, start = body(ctx.corpus, doc.path)
        rules, _ = parse(text, start, spec)
        rel = posixpath.relpath(doc.path, out_dir)
        lines += _section(f"{cfg.namespace} {doc.path}", rel, doc.data.get("version"), rules)
    return "\n".join(lines).rstrip("\n") + "\n" if lines else "_No rules._\n"


def check_digest_fresh(ctx):
    cfg = ctx.cfg
    if not cfg.digest_out or cfg.rules is None:
        return
    f = cfg.repo_root / cfg.digest_out
    current = f.read_text(encoding="utf-8") if f.is_file() else None
    fix = "run `dox digest` and commit the result"
    if current is None or BEGIN not in current:
        yield Finding("digest-fresh", cfg.digest_out, 1, "rules digest missing", fix)
    elif render(current, block(ctx), BEGIN, END) != current:
        yield Finding("digest-fresh", cfg.digest_out, 1, "rules digest is stale", fix)


def write(ctx) -> list[str]:
    cfg = ctx.cfg
    f = cfg.repo_root / cfg.digest_out
    current = f.read_text(encoding="utf-8") if f.is_file() else None
    new = render(current, block(ctx), BEGIN, END)
    if new == current:
        return []
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(new, encoding="utf-8")
    return [cfg.digest_out]
