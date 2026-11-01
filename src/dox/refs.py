"""Parse and resolve refs: link-field values and Markdown links."""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass

from dox.config import HOME_RX
from dox.docs import Corpus, Doc

MD_LINK = re.compile(r"(?<!!)\[(?:[^\]\\]|\\.)*\]\(\s*<?([^)\s>]+)>?(?:\s+[\"'][^\"']*[\"'])?\s*\)")
FENCE = re.compile(r"^(\s*)(```|~~~)")
INLINE_CODE = re.compile(r"`[^`\n]*`")


@dataclass
class Ref:
    raw: str
    kind: str  # external | url | cite | id | path | bad-home
    home: str | None  # None: the citing document's own home
    value: str  # the ref without its home
    anchor: str | None = None


class Resolver:
    def __init__(self, corpus: Corpus):
        self.corpus = corpus
        self.cfg = corpus.cfg
        self.by_ident: dict[str, list[Doc]] = {}
        self.by_cite: dict[str, list[Doc]] = {}
        for d in corpus.docs:
            self.by_ident.setdefault(d.stem, []).append(d)
            c = cite_of(d)
            if c:
                self.by_cite.setdefault(c, []).append(d)

    def parse(self, raw: str) -> Ref:
        cfg = self.cfg
        for rx, _ in cfg.external:
            if rx.search(raw):
                return Ref(raw, "external", None, raw)
        if re.match(r"^https?://", raw):
            return Ref(raw, "url", None, raw)
        home, rest = None, raw
        sep = cfg.ref_separator
        if sep in raw:
            head, tail = raw.split(sep, 1)
            if HOME_RX.match(head):
                if head not in cfg.namespaces:
                    return Ref(raw, "bad-home", head, tail)
                home, rest = (None if head == cfg.namespace else head), tail
        anchor = None
        if "#" in rest:
            rest, anchor = rest.split("#", 1)
        if rest.endswith(".md") or "/" in rest:
            return Ref(raw, "path", home, rest, anchor)
        for t in cfg.types.values():
            if t.cite and t.cite.match(rest) is not None:
                return Ref(raw, "cite", home, rest, anchor)
        return Ref(raw, "id", home, rest, anchor)

    def resolve(self, ref: Ref) -> list[Doc] | str | None:
        """Resolve a ref in this home: documents, a path, or None.

        Callers handle `external`, `url` and other homes first.
        """
        if ref.kind == "cite":
            return self.by_cite.get(ref.value) or None
        if ref.kind == "id":
            return self.by_ident.get(ref.value) or None
        if ref.kind == "path":
            for candidate in (ref.value, self.cfg.to_repo(ref.value)):
                p = posixpath.normpath(candidate)
                if self._exists(p):
                    return p
            return None
        return None

    def _exists(self, p: str) -> bool:
        return p in self.corpus.file_set or (self.cfg.repo_root / p).exists()

    def doc_at(self, path: str) -> Doc | None:
        """The document a repo-relative file or directory path names."""
        d = self.corpus.doc_for(path)
        if d:
            return d
        return self.corpus.doc_for(posixpath.join(path, "README.md"))


def cite_of(doc: Doc) -> str | None:
    t = doc.type
    if not t.cite or not t.id_format:
        return None
    got = t.id_format.match(doc.stem)
    if not got or "n" not in got:
        return None
    return t.cite.render(n=int(got["n"]))


def ref_values(doc: Doc) -> list[tuple[str, str, str]]:
    """(field, meaning, value) for every typed link in a document's frontmatter."""
    out = []
    for f, meaning in doc.type.links.items():
        v = doc.data.get(f)
        if v is None:
            continue
        items = v if isinstance(v, list) else [v]
        for item in items:
            if isinstance(item, str):
                out.append((f, meaning, item))
    return out


def markdown_links(text: str, first_line: int = 1):
    """(line, target) for each inline Markdown link outside code."""
    in_fence = None
    for i, line in enumerate(text.splitlines(), first_line):
        m = FENCE.match(line)
        if m:
            if in_fence is None:
                in_fence = m.group(2)
            elif m.group(2) == in_fence:
                in_fence = None
            continue
        if in_fence:
            continue
        for lm in MD_LINK.finditer(INLINE_CODE.sub("", line)):
            yield i, lm.group(1)


def body(corpus: Corpus, path: str) -> tuple[str, int]:
    """A file's text after its frontmatter, and the line the body starts on."""
    text = corpus.text(path)
    fm = corpus.frontmatter(path)
    if fm.body_line > 1:
        lines = text.splitlines()
        return "\n".join(lines[fm.body_line - 1 :]), fm.body_line
    return text, 1


def is_external_target(target: str) -> bool:
    return bool(re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*:", target)) or target.startswith("#")
