"""Command line: lint, next, new, graph, index (ADR-0001 section 5).

Exit codes: 0 clean (warnings allowed), 1 errors, 2 usage or
configuration error, 3 a remote source could not be reached.
"""

from __future__ import annotations

import argparse
import datetime
import json
import sys
from collections import Counter
from pathlib import Path

from dox import __version__, allocate, config, findings, graph, index, proc, remote
from dox.checks import OFFLINE, Context
from dox.docs import Corpus
from dox.errors import ConfigError, DoxError

ALL_CHECKS = dict(OFFLINE)
ALL_CHECKS["index-fresh"] = index.check_index_fresh
ALL_CHECKS["links-remote"] = remote.check_links_remote


class Parser(argparse.ArgumentParser):
    def error(self, message):
        self.print_usage(sys.stderr)
        raise ConfigError(f"{self.prog}: {message}")


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dox-schema", metavar="OWNER/REPO",
                        help="schema repo (default: $DOX_SCHEMA_REPO)")
    common.add_argument("--config", type=Path, metavar="PATH",
                        help="use this .dox.toml instead of the repository's (reported in the output)")
    common.add_argument("-C", dest="cwd", type=Path, default=Path.cwd(), metavar="DIR",
                        help="run as if started in DIR")
    p = Parser(prog="dox", description="A generic docs checker driven by a schema repo.")
    p.add_argument("--version", action="version", version=f"dox {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True, parser_class=Parser)

    lint = sub.add_parser("lint", parents=[common], help="run the checks")
    lint.add_argument("--changed", action="store_true",
                      help="report only files changed against origin's default branch")
    lint.add_argument("--remote", action="store_true", help="also run remote checks")
    lint.add_argument("--skip", default="", metavar="CHECK[,CHECK]", help="skip checks (always reported)")
    lint.add_argument("--json", action="store_true")
    g = lint.add_mutually_exclusive_group()
    g.add_argument("--update-baseline", action="store_true",
                   help="drop baseline entries that no longer occur (never adds)")
    g.add_argument("--init-baseline", action="store_true",
                   help="write a baseline of the current findings; refuses if one exists")

    nxt = sub.add_parser("next", parents=[common], help="allocate the next free id")
    nxt.add_argument("type")
    nxt.add_argument("slug", nargs="?")
    nxt.add_argument("--date", type=_date, help="allocation date YYYY-MM-DD (default today)")
    nxt.add_argument("--offline", action="store_true", help="skip origin and open PRs (warned)")
    nxt.add_argument("--json", action="store_true")

    new = sub.add_parser("new", parents=[common], help="allocate an id and scaffold a document")
    new.add_argument("type")
    new.add_argument("slug")
    lay = new.add_mutually_exclusive_group()
    lay.add_argument("--dir", dest="as_dir", action="store_const", const=True)
    lay.add_argument("--file", dest="as_dir", action="store_const", const=False)
    new.add_argument("--title")
    new.add_argument("--date", type=_date)
    new.add_argument("--offline", action="store_true")

    gr = sub.add_parser("graph", parents=[common], help="the reference graph")
    fmt = gr.add_mutually_exclusive_group()
    fmt.add_argument("--json", dest="fmt", action="store_const", const="json")
    fmt.add_argument("--mermaid", dest="fmt", action="store_const", const="mermaid")
    fmt.add_argument("--dot", dest="fmt", action="store_const", const="dot")

    ix = sub.add_parser("index", parents=[common], help="regenerate indexes and the digest")
    ix.add_argument("--check", action="store_true", help="exit 1 if anything would change; write nothing")
    return p


def _date(s: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(s)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date: {s!r}") from None


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return COMMANDS[args.cmd](args)
    except DoxError as e:
        print(f"dox: {e}", file=sys.stderr)
        return e.exit_code
    except BrokenPipeError:
        return 0


def _load(args) -> config.Config:
    return config.load(args.cwd.resolve(), args.dox_schema, args.config)


def _overrides(cfg: config.Config, args, skipped: list[str]) -> list[str]:
    """Environment-layer choices that a reviewer must see."""
    out = []
    if skipped:
        out.append(f"skipped checks: {', '.join(skipped)}")
    if args.config:
        out.append(f"configuration from --config {cfg.config_path}")
    if cfg.schema_source != "fetched":
        out.append(f"schema repo from {cfg.schema_source}, not {cfg.schema_repo}@{cfg.schema_ref}")
    return out


def cmd_lint(args) -> int:
    cfg = _load(args)
    skipped = [s for s in (x.strip() for x in args.skip.split(",")) if s]
    for s in skipped:
        if s not in ALL_CHECKS:
            raise ConfigError(f"--skip: unknown check {s!r} ({', '.join(ALL_CHECKS)})")
    active, inactive = [], {}
    for c in ALL_CHECKS:
        if c in skipped:
            continue
        if cfg.severity[c] == "off":
            inactive[c] = f"off ({cfg.severity_source[c]})"
        elif not config.CHECKS[c] and not args.remote:
            inactive[c] = "remote; pass --remote"
        else:
            active.append(c)

    ctx = Context(cfg, Corpus(cfg))
    found: list[findings.Finding] = []
    for c in active:
        for f in ALL_CHECKS[c](ctx):
            f.severity = cfg.severity[c]
            found.append(f)
    changed = _changed(cfg) if args.changed else None
    if changed is not None:
        found = [f for f in found if not f.path or f.path in changed or f.check == "config"]
    found.sort(key=lambda f: (f.path, f.line, f.check, f.message))

    base_path = cfg.repo_root / cfg.baseline if cfg.baseline else None
    if (args.update_baseline or args.init_baseline) and not base_path:
        raise ConfigError("no `baseline` in .dox.toml")
    if args.init_baseline and base_path.exists():
        raise ConfigError(f"{cfg.baseline} exists; a baseline may only shrink (--update-baseline)")
    baseline = findings.load_baseline(base_path) if base_path else Counter()
    used = findings.apply_baseline(found, baseline)

    notes = _overrides(cfg, args, skipped)
    if args.init_baseline:
        findings.write_baseline(base_path, Counter(f.key for f in found))
        notes.append(f"wrote {cfg.baseline} with {len(found)} entries")
        for f in found:
            f.baselined = True
    elif args.update_baseline:
        keep = Counter({k: n for k, n in baseline.items()
                        if k[0] not in active or (changed is not None and k[1] not in changed)})
        new = keep + used
        findings.write_baseline(base_path, new)
        notes.append(f"{cfg.baseline}: {sum(baseline.values())} -> {sum(new.values())} entries")

    summ = findings.summary(found)
    if args.json:
        print(json.dumps({
            "dox": __version__,
            "findings": findings.to_json(found),
            "skipped": skipped,
            "inactive": inactive,
            "notes": notes,
            "summary": summ,
            "config": cfg.resolved(),
        }, indent=2))
    else:
        for f in found:
            print(f.text())
        for n in notes:
            print(f"note: {n}")
        print(f"{summ['errors']} error(s), {summ['warnings']} warning(s), {summ['baselined']} baselined; "
              f"checks: {', '.join(active) or 'none'}")
    return 1 if summ["errors"] else 0


def _changed(cfg) -> set[str]:
    repo = cfg.repo_root
    ref = remote.default_ref(repo)
    out: set[str] = set()
    if ref:
        mb = proc.git(repo, "merge-base", "HEAD", ref)
        if mb.returncode == 0:
            r = proc.git(repo, "diff", "--name-only", mb.stdout.strip())
            out.update(p for p in r.stdout.splitlines() if p)
    r = proc.git(repo, "diff", "--name-only", "HEAD")
    out.update(p for p in r.stdout.splitlines() if p)
    r = proc.git(repo, "ls-files", "--others", "--exclude-standard")
    out.update(p for p in r.stdout.splitlines() if p)
    return out


def cmd_next(args) -> int:
    cfg = _load(args)
    ident, notes = allocate.next_id(cfg, args.type, args.slug and allocate.slugify(args.slug),
                                    args.date or datetime.date.today(), args.offline)
    for n in notes:
        print(f"dox: warning: {n}", file=sys.stderr)
    if args.json:
        print(json.dumps({"id": ident, "type": args.type, "home": cfg.namespace, "notes": notes}))
    else:
        print(ident)
    return 0


def cmd_new(args) -> int:
    cfg = _load(args)
    dest, notes = allocate.new_doc(cfg, args.type, args.slug, as_dir=args.as_dir, title=args.title,
                                   date=args.date or datetime.date.today(), offline=args.offline)
    for n in notes:
        print(f"dox: warning: {n}", file=sys.stderr)
    print(f"{dest}/" if dest.is_dir() else dest)
    return 0


def cmd_graph(args) -> int:
    cfg = _load(args)
    g = graph.build(Context(cfg, Corpus(cfg)))
    fmt = args.fmt or "json"
    print({"json": graph.to_json, "mermaid": graph.to_mermaid, "dot": graph.to_dot}[fmt](g), end="" if fmt != "json" else "\n")
    return 0


def cmd_index(args) -> int:
    cfg = _load(args)
    ctx = Context(cfg, Corpus(cfg))
    if args.check:
        stale = list(index.check_index_fresh(ctx))
        for f in stale:
            print(f.text())
        return 1 if stale else 0
    for p in index.write(ctx):
        print(p)
    return 0


COMMANDS = {"lint": cmd_lint, "next": cmd_next, "new": cmd_new, "graph": cmd_graph, "index": cmd_index}
