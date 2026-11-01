"""Path globs: `*` within a segment, `**` across segments, `{yymm}` a month."""

from __future__ import annotations

import functools
import re


def norm(pat: str) -> str:
    pat = pat.strip()
    while pat.startswith("./"):
        pat = pat[2:]
    pat = pat.rstrip("/")
    return "" if pat == "." else pat


@functools.lru_cache(maxsize=None)
def compile_glob(pat: str) -> re.Pattern:
    pat = norm(pat)
    out, i = [], 0
    while i < len(pat):
        if pat.startswith("{yymm}", i):
            out.append("[0-9]{4}")
            i += 6
        elif pat.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pat.startswith("**", i):
            out.append(".*")
            i += 2
        elif pat[i] == "*":
            out.append("[^/]*")
            i += 1
        elif pat[i] == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(pat[i]))
            i += 1
    return re.compile("^" + "".join(out) + "$")


def match(pat: str, path: str) -> bool:
    return bool(compile_glob(pat).match(path))


def match_any(pats, path: str) -> bool:
    return any(match(p, path) for p in pats)


def is_literal(pat: str) -> bool:
    return not any(c in norm(pat) for c in "*?{")
