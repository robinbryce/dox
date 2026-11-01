"""Every subprocess goes through run(), so tests can replace it."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run(args, cwd=None, timeout=None, env=None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [str(a) for a in args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as e:
        return subprocess.CompletedProcess(args, 127, "", str(e))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", f"timed out after {timeout}s")


def quiet_env() -> dict:
    """Never prompt for credentials; fail fast offline."""
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_PROMPT_DISABLED"] = "1"
    ssh = os.environ.get("GIT_SSH_COMMAND", "ssh")
    env["GIT_SSH_COMMAND"] = f"{ssh} -o BatchMode=yes -o ConnectTimeout=5"
    return env


def git(repo: Path | str, *args, timeout=None) -> subprocess.CompletedProcess:
    return run(["git", "-C", repo, *args], timeout=timeout, env=quiet_env())
