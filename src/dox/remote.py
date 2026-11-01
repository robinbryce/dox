"""Everything that reads beyond the working tree: origin, open pull
requests, and other homes' default branches."""

from __future__ import annotations

import base64
import json
import re
import tomllib
from pathlib import Path

from dox import proc
from dox.errors import RemoteError
from dox.findings import Finding
from dox.refs import body, ref_values

DEFAULT_REFS = ("main", "master", "HEAD")


def github_slug(url: str) -> str | None:
    m = re.match(r"^(?:git@github\.com:|ssh://git@github\.com/|https://github\.com/)([^/]+/[^/]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None


def origin_url(repo: Path) -> str | None:
    r = proc.git(repo, "config", "--get", "remote.origin.url")
    return r.stdout.strip() if r.returncode == 0 else None


def fetch_origin(repo: Path, timeout: float) -> None:
    r = proc.git(repo, "fetch", "--quiet", "origin", timeout=timeout)
    if r.returncode:
        raise RemoteError(f"git fetch origin failed in {repo}: {r.stderr.strip()}")


def default_ref(repo: Path) -> str | None:
    r = proc.git(repo, "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if r.returncode == 0 and r.stdout.strip():
        return r.stdout.strip()
    for cand in ("origin/main", "origin/master"):
        if proc.git(repo, "rev-parse", "--verify", "--quiet", f"{cand}^{{commit}}").returncode == 0:
            return cand
    return None


def tree_paths(repo: Path, ref: str) -> list[str]:
    r = proc.git(repo, "ls-tree", "-r", "-t", "--name-only", "--full-tree", ref)
    if r.returncode:
        raise RemoteError(f"cannot list {ref} in {repo}: {r.stderr.strip()}")
    return [p for p in r.stdout.splitlines() if p]


def gh_json(args: list[str], timeout: float):
    r = proc.run(["gh", *args], timeout=timeout, env=proc.quiet_env())
    if r.returncode:
        raise RemoteError(f"gh {' '.join(args[:3])} failed: {(r.stderr or r.stdout).strip()}")
    try:
        return json.loads(r.stdout or "null")
    except json.JSONDecodeError as e:
        raise RemoteError(f"gh {' '.join(args[:3])}: unreadable output: {e}") from None


def open_pr_paths(slug: str, timeout: float) -> list[str]:
    data = gh_json(["pr", "list", "-R", slug, "--state", "open", "--limit", "200", "--json", "files"], timeout)
    out = []
    for pr in data or []:
        for f in pr.get("files") or []:
            if f.get("path"):
                out.append(f["path"])
    return out


class HomeTree:
    """The default branch of another home: its paths, ids and citations."""

    def __init__(self, cfg, home: str, paths: list[str], root: str):
        self.home = home
        self.root = root
        self.paths = set(paths)
        self.idents: set[str] = set()
        self.cites: set[str] = set()
        for p in paths:
            name = p.rsplit("/", 1)[-1]
            stem = name[:-3] if name.endswith(".md") else name
            self.idents.add(stem)
            for t in cfg.types.values():
                if t.cite and t.id_format:
                    got = t.id_format.match(stem)
                    if got and "n" in got:
                        self.cites.add(t.cite.render(n=int(got["n"])))

    def has_path(self, p: str) -> bool:
        p = p.strip("/")
        cands = [p, f"{self.root}/{p}" if self.root else p]
        return any(c in self.paths for c in cands)


class Homes:
    def __init__(self, cfg):
        self.cfg = cfg
        self._cache: dict[str, HomeTree] = {}

    def get(self, home: str) -> HomeTree:
        if home not in self._cache:
            self._cache[home] = self._load(home)
        return self._cache[home]

    def _clone(self, home: str) -> Path | None:
        tmpl = self.cfg.clones
        if not tmpl:
            return None
        repo = self.cfg.namespaces[home]
        owner, name = repo.split("/", 1)
        p = Path(tmpl.format(repo=name, home=home, owner=owner)).expanduser()
        return p if (p / ".git").exists() else None

    def _load(self, home: str) -> HomeTree:
        cfg = self.cfg
        repo = cfg.namespaces[home]
        clone = self._clone(home)
        if clone:
            fetch_origin(clone, cfg.timeout)
            ref = default_ref(clone)
            if not ref:
                raise RemoteError(f"{clone}: no origin default branch")
            paths = tree_paths(clone, ref)
            r = proc.git(clone, "show", f"{ref}:.dox.toml")
            dox_toml = r.stdout if r.returncode == 0 else ""
        else:
            data = gh_json(["api", f"repos/{repo}/git/trees/HEAD?recursive=1"], cfg.timeout)
            paths = [e["path"] for e in (data or {}).get("tree", [])]
            dox_toml = ""
            try:
                c = gh_json(["api", f"repos/{repo}/contents/.dox.toml"], cfg.timeout)
                dox_toml = base64.b64decode(c.get("content", "")).decode()
            except RemoteError:
                pass
        root = ""
        if dox_toml:
            try:
                root = str(tomllib.loads(dox_toml).get("root", "")).strip("./")
            except tomllib.TOMLDecodeError:
                root = ""
        return HomeTree(cfg, home, paths, root)


def url_regex(template: str) -> re.Pattern:
    parts, pos = [], 0
    for m in re.finditer(r"\{(repo|ref|path)\}", template):
        parts.append(re.escape(template[pos:m.start()]))
        parts.append({"repo": r"(?P<repo>[^/\s]+/[^/\s]+)", "ref": r"(?P<ref>[^/\s]+)",
                      "path": r"(?P<path>[^\s#?)>\]`'\"]+)"}[m.group(1)])
        pos = m.end()
    parts.append(re.escape(template[pos:]))
    return re.compile("".join(parts))


def check_links_remote(ctx):
    cfg, corpus, res = ctx.cfg, ctx.corpus, ctx.resolver
    homes = Homes(cfg)
    for doc in corpus.docs:
        for field, meaning, value in ref_values(doc):
            ref = res.parse(value)
            if ref.kind in ("external", "url", "bad-home") or ref.home is None:
                continue
            tree = homes.get(ref.home)
            ok = ((ref.kind == "cite" and ref.value in tree.cites)
                  or (ref.kind == "id" and ref.value in tree.idents)
                  or (ref.kind == "path" and tree.has_path(ref.value)))
            if not ok:
                yield Finding("links-remote", doc.path, doc.fm.line_of(field),
                              f"{field}: {value!r} not found on {ref.home}'s default branch",
                              f"fix the ref, or land the target in {cfg.namespaces[ref.home]} first")
    if not cfg.cross_repo:
        return
    rx = url_regex(cfg.cross_repo)
    home_of = {repo.lower(): h for h, repo in cfg.namespaces.items()}
    for f in corpus.md:
        text, start = body(corpus, f)
        for i, line in enumerate(text.splitlines(), start):
            for m in rx.finditer(line):
                home = home_of.get(m.group("repo").lower())
                if not home or m.group("ref") not in DEFAULT_REFS:
                    continue  # pinned commits and other branches are not checked
                path = m.group("path").rstrip(".,;:")
                if home == cfg.namespace:
                    if path.strip("/") in corpus.file_set or (cfg.repo_root / path.strip("/")).exists():
                        continue
                elif homes.get(home).has_path(path):
                    continue
                yield Finding("links-remote", f, i, f"{m.group(0)} does not exist on {home}'s default branch",
                              "fix the URL, or link a path that exists")
