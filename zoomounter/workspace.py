"""Where ZooMounter keeps its runs, and what it throws away.

ZooMounter used to write `./output/` relative to wherever it was invoked. That
is fine from the repo and wrong everywhere else: the whole point of putting it
on your PATH is to call it from inside your CAD project, and a generator that
scatters build folders through someone's source tree is a generator people stop
running. Runs now go to one place ZooMounter owns.

## Why a run gets pruned at all

A finished run is about 400KB, most of it the exploded assembly -- a
deliberately not-to-scale copy that exists to be photographed once. Keeping it
after the PNG is written buys nothing and makes the folder harder to read.

Pruning is destructive, which in this repo means it has to say what it will not
do rather than quietly doing nothing. `prune_runs()` returns the reason it
declined, and the caller prints it.

## The two rules that keep this from eating something it shouldn't

**`inspection_report.md` embeds `preview.png` by relative path.** Delete one
without the other and every report in the folder renders a broken image. They
are kept or discarded as a pair, and a test pins that.

**A directory ZooMounter did not create is not a run.** Pruning only ever
touches the workspace it owns. A `--runs-dir` the user pointed at is theirs, and
anything under a `.git` is someone's source tree -- both are refused, with a
reason, rather than trusted to look run-shaped.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

ENV_HOME = "ZOOMOUNTER_HOME"

DEFAULT_KEEP_RUNS = 5

# Written by the run and worth keeping.
KEEP_IN_RUN = ("main.kcl", "project.toml", "preview.png", "inspection_report.md")

# The exploded assembly is render-only and never verified -- its job is done
# once preview.png exists. Named here rather than inlined so the retention
# policy is one list somebody can read.
PRUNE_FROM_RUN = ("assembly-exploded",)

# Zoo Design Studio scaffolds this into any folder it opens. ZooMounter never
# writes one, so any that appear are drift.
FOREIGN_ARTIFACTS = ("thumbnail.png",)

# Never treat these as runs, wherever they appear.
NEVER_PRUNE_NAMES = {"tests", "examples", "probes", ".git"}


@dataclass
class PruneResult:
    """What pruning did, and what it refused to do."""

    trimmed: list[Path] = field(default_factory=list)  # dirs slimmed in place
    removed: list[Path] = field(default_factory=list)  # whole runs deleted
    refused: str = ""  # non-empty means nothing was touched, and why

    @property
    def did_nothing(self) -> bool:
        return not self.trimmed and not self.removed


def home() -> Path:
    """The folder ZooMounter owns.

    `ZOOMOUNTER_HOME` first so a user can point it at a bigger disk or a
    scratch volume without passing a flag to every invocation.
    """
    env = os.environ.get(ENV_HOME)
    if env:
        return Path(env).expanduser().resolve()
    return Path.home() / ".zoomounter"


def runs_dir(override: str | Path | None = None) -> Path:
    """Where run folders go. Never the current directory."""
    if override:
        return Path(override).expanduser().resolve()
    return home() / "runs"


def _under_git(path: Path) -> bool:
    """Is this inside a git working tree?

    A run folder someone version-controls is not a cache, whatever it looks
    like. Checking parents as well as the path itself, because the dangerous
    case is a runs-dir pointed at a subfolder of a real repo.
    """
    for candidate in [path, *path.parents]:
        if (candidate / ".git").exists():
            return True
    return False


def _is_safe_to_prune(root: Path, user_supplied: bool) -> str:
    """Empty string means safe; anything else is the reason it is not."""
    if user_supplied:
        return (
            f"{root} was given with --runs-dir, so it is yours rather than "
            f"ZooMounter's. Pruning only ever touches its own workspace."
        )
    if root.name in NEVER_PRUNE_NAMES or any(
        p.name in NEVER_PRUNE_NAMES for p in root.parents
    ):
        return f"{root} is inside a directory this tool never prunes."
    if _under_git(root):
        return (
            f"{root} is inside a git working tree, so it is someone's source "
            f"rather than a cache."
        )
    return ""


def trim_run(run_dir: Path) -> list[Path]:
    """Delete what the run no longer needs, keeping what a person does.

    Deliberately does NOT touch preview.png or inspection_report.md. The report
    embeds the PNG by relative path, so removing the image to save 90KB breaks
    every report in the folder -- a saving nobody asked for at a cost nobody
    would accept.
    """
    removed = []
    for name in PRUNE_FROM_RUN:
        target = run_dir / name
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
            removed.append(target)
    for pattern in FOREIGN_ARTIFACTS:
        for stray in run_dir.rglob(pattern):
            stray.unlink(missing_ok=True)
            removed.append(stray)
    return removed


def list_runs(root: Path) -> list[Path]:
    """Run folders, newest first. Anything that is not a directory, or that is
    named like something we protect, is not a run."""
    if not root.is_dir():
        return []
    runs = [
        p
        for p in root.iterdir()
        if p.is_dir() and p.name not in NEVER_PRUNE_NAMES and not p.name.startswith(".")
    ]
    return sorted(runs, key=lambda p: p.stat().st_mtime, reverse=True)


def prune_runs(
    root: Path,
    keep: int = DEFAULT_KEEP_RUNS,
    user_supplied: bool = False,
    trim_kept: bool = True,
) -> PruneResult:
    """Slim the kept runs and delete the ones past `keep`.

    Returns what it did. A refusal is a returned reason, not an exception and
    not silence -- the caller prints it, because a tool that quietly declines to
    do the thing you asked is worse than one that says no.
    """
    result = PruneResult()
    refusal = _is_safe_to_prune(root, user_supplied)
    if refusal:
        result.refused = refusal
        return result
    if keep < 1:
        result.refused = f"--keep-runs must be at least 1, got {keep}."
        return result

    runs = list_runs(root)
    if trim_kept:
        for run in runs[:keep]:
            if trim_run(run):
                result.trimmed.append(run)
    for run in runs[keep:]:
        shutil.rmtree(run, ignore_errors=True)
        result.removed.append(run)
    return result
