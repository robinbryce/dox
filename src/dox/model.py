"""Document types as the schema repo declares them (docs/contract.md)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dox import globs
from dox.errors import ConfigError

TOKEN = re.compile(r"\{(n|yymm|slug)(?::0?(\d+))?\}")
SLUG_RX = r"[a-z0-9]+(?:-[a-z0-9]+)*"
ROLES = ("discussion", "in-force", "retired", "closed")
MEANINGS = ("replaces", "replaced-by", "absorbed-into", "cites", "outcome")


class IdPattern:
    """A format such as `adr-{n:04}-{slug}`, usable both ways: match and render."""

    def __init__(self, fmt: str, where: str, allowed=("n", "yymm", "slug")):
        self.fmt = fmt
        self.width = 0
        self.fields: list[str] = []
        parts, pos = [], 0
        for m in TOKEN.finditer(fmt):
            literal = fmt[pos : m.start()]
            if "{" in literal or "}" in literal:
                raise ConfigError(f"{where}: unknown token in {fmt!r}")
            parts.append(re.escape(literal))
            name, width = m.group(1), m.group(2)
            if name not in allowed:
                raise ConfigError(f"{where}: token {{{name}}} not allowed in {fmt!r}")
            if name in self.fields:
                raise ConfigError(f"{where}: token {{{name}}} repeated in {fmt!r}")
            self.fields.append(name)
            if name == "n":
                self.width = int(width or 0)
                rx = f"[0-9]{{{self.width},}}" if self.width else "[0-9]+"
            elif name == "yymm":
                rx = "[0-9]{4}"
            else:
                rx = SLUG_RX
            parts.append(f"(?P<{name}>{rx})")
            pos = m.end()
        tail = fmt[pos:]
        if "{" in tail or "}" in tail:
            raise ConfigError(f"{where}: unknown token in {fmt!r}")
        parts.append(re.escape(tail))
        self.body = "".join(parts)
        self.regex = re.compile("^" + self.body + "$")

    def match(self, s: str) -> dict | None:
        m = self.regex.match(s)
        return m.groupdict() if m else None

    def render(self, n=None, yymm=None, slug=None) -> str:
        def sub(m):
            name = m.group(1)
            if name == "n":
                return str(n).zfill(self.width) if self.width else str(n)
            return {"yymm": yymm, "slug": slug}[name]

        return TOKEN.sub(sub, self.fmt)

    def prefix(self, n=None, yymm=None) -> str:
        """The id up to the slug: `adr-0072` for `adr-{n:04}-{slug}`."""
        return self.render(n=n, yymm=yymm, slug="\0").split("\0")[0].rstrip("-_")


@dataclass
class TypeDef:
    name: str
    kind: str
    schema: str
    identity: str
    id_format: IdPattern | None = None
    id_legacy: list[re.Pattern] = field(default_factory=list)
    cite: IdPattern | None = None
    allocate: str | None = None
    dirs: list[str] = field(default_factory=list)
    retired_dir: str | None = None
    archive_dir: str | None = None
    scan_dirs: list[str] = field(default_factory=list)
    layout: str = "either"
    fallback: bool = False
    status_field: str | None = None
    status_values: dict[str, str] = field(default_factory=dict)
    links: dict[str, str] = field(default_factory=dict)
    review_date: str | None = None
    index: str | None = None
    template: str | None = None
    template_dir: str | None = None

    @property
    def has_ids(self) -> bool:
        return self.id_format is not None or bool(self.id_legacy)

    def id_match(self, ident: str) -> dict | None:
        """Fields of an allocated id, {} for a legacy id, None otherwise."""
        if self.id_format:
            got = self.id_format.match(ident)
            if got is not None:
                return got
        if any(rx.match(ident) for rx in self.id_legacy):
            return {}
        return None

    def role(self, status) -> str | None:
        if not isinstance(status, str):
            return None
        return self.status_values.get(status)

    def placement_globs(self, role: str | None) -> list[str]:
        if role == "retired" and self.retired_dir:
            return [self.retired_dir]
        if role == "closed" and self.archive_dir:
            return [self.archive_dir]
        return list(self.dirs)

    def all_globs(self) -> list[str]:
        out = list(self.dirs)
        out += [d for d in (self.retired_dir, self.archive_dir) if d]
        return out

    def fields_meaning(self, meaning: str) -> list[str]:
        return [f for f, m in self.links.items() if m == meaning]


def place_match(pats, docpath: str) -> bool:
    """Does a document path (relative to the docs root) sit where `pats` say?

    An entry ending in `.md` matches that file; any other entry names a
    directory and matches documents directly inside it.
    """
    parent = docpath.rsplit("/", 1)[0] if "/" in docpath else ""
    for p in pats:
        if globs.norm(p).endswith(".md"):
            if globs.match(p, docpath):
                return True
        elif globs.match(p, parent):
            return True
    return False
