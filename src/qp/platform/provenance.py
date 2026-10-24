"""Provenance for results (ARCHITECTURE: every result records git commit, config hash,
dataset snapshot, parameters, seed and package versions)."""

from __future__ import annotations

import platform
import subprocess
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

PACKAGES = ("qp", "numpy", "pandas", "scipy", "statsmodels", "arch", "duckdb", "pyarrow")


@dataclass(frozen=True, slots=True)
class GitState:
    commit: str | None  # None outside a git checkout
    dirty: bool  # uncommitted changes to tracked files: the code may differ from the commit


def git_state(repo: Path) -> GitState:
    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(  # noqa: S603 - fixed argv, no shell
                ["git", "-C", str(repo), *args],  # noqa: S607
                capture_output=True,
                text=True,
                timeout=10,
                check=True,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip()

    commit = git("rev-parse", "HEAD")
    status = git("status", "--porcelain", "--untracked-files=no")
    return GitState(commit, bool(status) if status is not None else True)


def package_versions(names: tuple[str, ...] = PACKAGES) -> dict[str, str]:
    out = {"python": platform.python_version()}
    for name in names:
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = "not installed"
    return out
