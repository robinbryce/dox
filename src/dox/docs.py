"""Find the repository's documents and assign each a type.

A document is `<id>.md` or a directory `<id>/` whose `README.md` is the
document; other files in the directory are its parts. A file belongs to a
type when its identity (stem or directory name) matches the type's id
shape, or, for a type whose ids the author chooses, when it sits in one of
the type's directories. The fallback type takes any other Markdown file
that has frontmatter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from dox import globs, proc
from dox.config import Config
from dox.errors import ConfigError
from dox.model import TypeDef, place_match


class StringDateLoader(yaml.SafeLoader):
    """SafeLoader that keeps dates as strings, as the schemas expect."""


StringDateLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:timestamp"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}

FM_END = re.compile(r"^---[ \t]*$", re.M)


@dataclass
class Frontmatter:
    data: dict | None
    error: str | None
    text: str  # raw YAML, "" if none
    body_line: int  # 1-based line where the body starts

    def line_of(self, key) -> int:
        """Line of a top-level key in the file, or 1."""
        if not self.text:
            return 1
        for i, line in enumerate(self.text.splitlines(), 2):
            if re.match(rf"^{re.escape(str(key))}\s*:", line):
                return i
        return 1


def parse_frontmatter(text: str) -> Frontmatter:
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        return Frontmatter(None, None, "", 1)
    m = FM_END.search(text, 4)
    if not m:
        return Frontmatter(None, "unterminated YAML frontmatter", "", 1)
    raw = text[4 : m.start()]
    body_line = text[: m.end()].count("\n") + 2
    try:
        data = yaml.load(raw, Loader=StringDateLoader)
    except yaml.YAMLError as e:
        return Frontmatter(None, f"invalid YAML: {e}".replace("\n", " "), raw, body_line)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return Frontmatter(None, "frontmatter is not a mapping", raw, body_line)
    return Frontmatter(data, None, raw, body_line)


@dataclass
class Doc:
    path: str  # repo-relative file; README.md for a directory document
    docpath: str | None  # the document (file or directory) relative to the docs root
    stem: str  # identity expected from the filesystem
    is_dir: bool
    type: TypeDef
    fm: Frontmatter
    parts: list[str] = field(default_factory=list)

    @property
    def dir(self) -> str:
        """Repo-relative path of the document itself (file or directory)."""
        return self.path.rsplit("/", 1)[0] if self.is_dir else self.path

    @property
    def data(self) -> dict:
        return self.fm.data or {}

    @property
    def ident(self):
        return self.data.get(self.type.identity)

    @property
    def id(self) -> str:
        """The document's id for graph and index: its stem."""
        return self.stem

    @property
    def status(self):
        return self.data.get(self.type.status_field) if self.type.status_field else None

    @property
    def role(self) -> str | None:
        """The status role; a document absorbed into another counts as retired."""
        role = self.type.role(self.status)
        if role in (None, "in-force", "discussion"):
            for f in self.type.fields_meaning("absorbed-into"):
                if self.data.get(f):
                    return "retired"
        return role

    @property
    def title(self) -> str:
        t = self.data.get("title")
        return t if isinstance(t, str) else self.stem


class Corpus:
    """Every file dox reads in one repository, and the documents among them."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.files = list_files(cfg.repo_root, cfg.exclude)
        self.file_set = set(self.files)
        self.md = [f for f in self.files if f.endswith(".md")]
        self._text: dict[str, str] = {}
        self._fm: dict[str, Frontmatter] = {}
        self.docs: list[Doc] = []
        self.part_of: dict[str, Doc] = {}
        self._classify()

    def text(self, path: str) -> str:
        if path not in self._text:
            try:
                self._text[path] = (self.cfg.repo_root / path).read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                self._text[path] = ""
        return self._text[path]

    def frontmatter(self, path: str) -> Frontmatter:
        if path not in self._fm:
            self._fm[path] = parse_frontmatter(self.text(path))
        return self._fm[path]

    def has_frontmatter(self, path: str) -> bool:
        return self.text(path).startswith("---\n") or self.text(path).startswith("---\r\n")

    def doc_for(self, path: str) -> Doc | None:
        """The document a file is, or is a part of."""
        return self._by_path.get(path) or self.part_of.get(path)

    def _make(self, path: str, stem: str, is_dir: bool, t: TypeDef) -> Doc:
        target = path.rsplit("/", 1)[0] if is_dir else path
        return Doc(path, self.cfg.to_docs(target), stem, is_dir, t, self.frontmatter(path))

    def _claim(self, ident: str, docpath: str | None) -> TypeDef | None:
        for t in self.cfg.types.values():
            if t.fallback:
                continue
            if t.has_ids:
                if t.id_match(ident) is not None:
                    return t
            elif docpath is not None and place_match(t.all_globs(), docpath):
                return t
        return None

    def _classify(self):
        cfg = self.cfg
        taken: set[str] = set()
        # Pass 1: directory documents (`<id>/README.md`).
        for f in self.md:
            if not f.endswith("/README.md"):
                continue
            d = f.rsplit("/", 1)[0]
            if d == cfg.root:
                continue
            ident = d.rsplit("/", 1)[-1]
            t = self._claim(ident, cfg.to_docs(d))
            if t:
                self.docs.append(self._make(f, ident, True, t))
                taken.add(f)
        # Pass 2: parts of directory documents.
        dir_docs = {doc.dir: doc for doc in self.docs}
        for f in self.md:
            if f in taken:
                continue
            parent = f.rsplit("/", 1)[0] if "/" in f else ""
            while parent:
                if parent in dir_docs:
                    self.part_of[f] = dir_docs[parent]
                    dir_docs[parent].parts.append(f)
                    taken.add(f)
                    break
                parent = parent.rsplit("/", 1)[0] if "/" in parent else ""
        # Pass 3: file documents.
        for f in self.md:
            if f in taken:
                continue
            stem = f.rsplit("/", 1)[-1][:-3]
            t = self._claim(stem, cfg.to_docs(f))
            if t:
                self.docs.append(self._make(f, stem, False, t))
                taken.add(f)
        # Pass 4: the fallback type takes the rest that has frontmatter.
        fb = next((t for t in cfg.types.values() if t.fallback), None)
        if fb:
            for f in self.md:
                if f in taken or not self.has_frontmatter(f):
                    continue
                is_dir = f.endswith("README.md") and "/" in f
                stem = f.rsplit("/", 2)[-2] if is_dir else f.rsplit("/", 1)[-1][:-3]
                if f == "README.md":
                    stem = "README"
                doc = self._make(f, stem, is_dir, fb)
                self.docs.append(doc)
                taken.add(f)
        self.docs.sort(key=lambda d: d.path)
        self._by_path = {d.path: d for d in self.docs}

    def in_repo_types(self, doc: Doc) -> bool:
        return doc.type.name in self.cfg.repo_types


def list_files(repo_root: Path, exclude: list[str]) -> list[str]:
    r = proc.git(repo_root, "ls-files", "-z", "--cached", "--others", "--exclude-standard")
    if r.returncode:
        raise ConfigError(f"git ls-files failed in {repo_root}: {r.stderr.strip()}")
    out = []
    for f in sorted(set(r.stdout.split("\0"))):
        if not f or globs.match_any(exclude, f):
            continue
        if any(globs.match(p.rstrip("/") + "/**", f) for p in exclude if not p.endswith("**")):
            continue
        if (repo_root / f).is_file():
            out.append(f)
    return out
