"""Fetch the schema repo at a pinned ref into a cache (ADR-0001 section 3)."""

from __future__ import annotations

import base64
import os
import re
import shutil
import tempfile
from pathlib import Path

from dox import proc
from dox.errors import ConfigError, RemoteError

REPO_RX = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
REF_RX = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_./-]*$")


def check_repo(name: str, source: str) -> str:
    if not REPO_RX.match(name or ""):
        raise ConfigError(f"{source}: schema repo must be <owner>/<repo>, got {name!r}")
    return name


def fetch(repo: str, ref: str, cache_dir: Path, timeout: float) -> Path:
    """Return a directory holding the schema repo at `ref`, fetching if needed.

    The cache is keyed by <owner>/<repo>/<ref> and never refreshed: pin a
    tag or a commit, not a branch.
    """
    if not REF_RX.match(ref or "") or ".." in ref:
        raise ConfigError(f".dox.toml: schema_ref {ref!r} is not a tag or commit")
    owner, name = repo.split("/", 1)
    dest = cache_dir / owner / name / ref.replace("/", "%2F")
    if (dest / "dox.toml").is_file():
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    url = os.environ.get("DOX_SCHEMA_URL", "https://github.com/{repo}.git").format(repo=repo)
    tmp = Path(tempfile.mkdtemp(prefix=".fetch-", dir=dest.parent))
    try:
        r = proc.git(tmp, "init", "-q", timeout=timeout)
        if r.returncode:
            raise ConfigError(f"git init failed: {r.stderr.strip()}")
        auth = []
        token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
        if token and url.startswith("https://github.com/"):
            basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
            auth = ["-c", f"http.https://github.com/.extraheader=AUTHORIZATION: basic {basic}"]
        r = proc.git(tmp, *auth, "fetch", "-q", "--depth", "1", url, ref, timeout=timeout)
        if r.returncode:
            err = r.stderr.strip()
            if re.search(r"couldn't find remote ref|not our ref|unadvertised object", err):
                raise ConfigError(f"schema repo {repo} has no ref {ref!r}: {err}")
            raise RemoteError(f"cannot fetch schema repo {repo} at {ref} from {url}: {err}")
        r = proc.git(tmp, "checkout", "-q", "FETCH_HEAD", timeout=timeout)
        if r.returncode:
            raise ConfigError(f"cannot check out {repo}@{ref}: {r.stderr.strip()}")
        shutil.rmtree(tmp / ".git")
        if not (tmp / "dox.toml").is_file():
            raise ConfigError(f"schema repo {repo}@{ref} has no dox.toml")
        try:
            tmp.rename(dest)
        except OSError:
            if not (dest / "dox.toml").is_file():  # lost a race to a valid copy
                raise
        return dest
    finally:
        if tmp.exists():
            shutil.rmtree(tmp, ignore_errors=True)
