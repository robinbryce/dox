"""Findings, severities, the shrink-only baseline, and reporting."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

from dox.errors import ConfigError

BASELINE_VERSION = 1


@dataclass
class Finding:
    check: str
    path: str
    line: int
    message: str
    fix: str
    severity: str = "error"
    baselined: bool = False

    @property
    def key(self) -> tuple[str, str, str]:
        # No line number: lines move under unrelated edits.
        return (self.check, self.path, self.message)

    def text(self) -> str:
        mark = " (baselined)" if self.baselined else ""
        loc = f"{self.path}:{self.line}" if self.path else "(config)"
        return f"{loc}: {self.severity} [{self.check}]{mark} {self.message}\n    fix: {self.fix}"


def load_baseline(path: Path) -> Counter:
    if not path.is_file():
        return Counter()
    try:
        data = json.loads(path.read_text())
        entries = data["findings"]
        return Counter((e["check"], e["path"], e["message"]) for e in entries)
    except (ValueError, KeyError, TypeError) as e:
        raise ConfigError(f"baseline {path} is not a dox baseline: {e}") from None


def write_baseline(path: Path, keys: Counter) -> None:
    entries = [{"check": c, "path": p, "message": m}
               for (c, p, m), n in sorted(keys.items()) for _ in range(n)]
    path.write_text(json.dumps({"version": BASELINE_VERSION, "findings": entries}, indent=2) + "\n")


def apply_baseline(findings: list[Finding], baseline: Counter) -> Counter:
    """Mark findings the baseline holds; return the baseline entries still occurring."""
    budget = Counter(baseline)
    used: Counter = Counter()
    for f in findings:
        if budget[f.key] > 0:
            budget[f.key] -= 1
            used[f.key] += 1
            f.baselined = True
    return used


def summary(findings: list[Finding]) -> dict:
    live = [f for f in findings if not f.baselined]
    return {
        "errors": sum(f.severity == "error" for f in live),
        "warnings": sum(f.severity == "warn" for f in live),
        "baselined": sum(f.baselined for f in findings),
        "by_check": dict(sorted(Counter(f.check for f in findings).items())),
    }


def to_json(findings: list[Finding]) -> list[dict]:
    return [asdict(f) for f in findings]
