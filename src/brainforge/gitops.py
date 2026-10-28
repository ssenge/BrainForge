"""Thin subprocess wrapper around the git CLI."""

from __future__ import annotations

import contextlib
import fcntl
import subprocess
from collections.abc import Iterator
from pathlib import Path


class GitError(Exception):
    pass


class Stale(GitError):
    """These paths changed elsewhere since this clone last saw them."""

    def __init__(self, paths: list[str], message: str | None = None):
        super().__init__(message or f"changed on the remote meanwhile: {', '.join(paths)}")
        self.paths = paths


def git(root: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    if check and proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout


def is_repo(root: Path) -> bool:
    return subprocess.run(["git", "rev-parse", "--git-dir"], cwd=root, capture_output=True).returncode == 0


def head(root: Path) -> str | None:
    out = git(root, "rev-parse", "--verify", "-q", "HEAD", check=False).strip()
    return out or None


def status(root: Path, *paths: str) -> list[tuple[str, str]]:
    """(XY code, path) for every changed or untracked file, untracked dirs expanded."""
    out = git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *paths)
    entries, parts, i = [], out.split("\0"), 0
    while i < len(parts):
        item = parts[i]
        i += 1
        if not item:
            continue
        code, path = item[:2], item[3:]
        if code[0] in "RC":  # rename/copy carries the source path as the next entry
            i += 1
        entries.append((code, path))
    return entries


def untracked(root: Path) -> set[str]:
    return {p for code, p in status(root) if code == "??"}


def dirty_tracked(root: Path) -> list[str]:
    return [p for code, p in status(root) if code != "??"]


def rollback(root: Path, baseline_untracked: set[str]) -> None:
    """Undo every change since the baseline: restore tracked files, delete new untracked ones."""
    changed = status(root)
    tracked = [p for code, p in changed if code != "??"]
    if tracked and head(root):
        git(root, "restore", "--source=HEAD", "--staged", "--worktree", "--", *tracked)
    for code, p in changed:
        if code == "??" and p not in baseline_untracked:
            (root / p).unlink(missing_ok=True)


def commit(root: Path, message: str, paths: list[str], author: str | None = None) -> str:
    """Commit exactly these paths; anything else the user has staged stays untouched."""
    existing = [p for p in paths if (root / p).exists() or git(root, "ls-files", "--", p).strip()]
    git(root, "add", "-A", "--", *existing)
    git(root, "commit", "-q", "-m", message, *([f"--author={author}"] if author else []), "--", *existing)
    return head(root) or ""


def has_upstream(root: Path) -> bool:
    return bool(git(root, "rev-parse", "--abbrev-ref", "@{u}", check=False).strip())


def _moved(root: Path, revs: str, paths: list[str] | None = None) -> list[str]:
    """Files changed in `revs`, optionally limited to `paths` (an empty list means none)."""
    if paths == []:
        return []
    return git(root, "diff", "--name-only", "-z", revs, "--", *(paths or [])).split("\0")[:-1]


def pull(root: Path, guard: list[str] | None = None) -> None:
    """Fast-forward to the remote. Raises Stale afterwards if that changed any `guard` path."""
    if has_upstream(root):
        before = head(root)
        git(root, "pull", "--ff-only", "-q")
        if before and guard and (moved := _moved(root, f"{before}..HEAD", guard)):
            raise Stale(moved)


def push(root: Path, tries: int = 3) -> None:
    """Push, never forcing. If the remote moved on, rebase onto it and try again, but only when the two
    sides changed different files. Same file on both sides raises Stale: a textual merge could join an
    approval with content nobody approved, so the caller undoes its run instead."""
    for _ in range(tries):
        proc = subprocess.run(["git", "push", "-q"], cwd=root, capture_output=True, text=True)
        if proc.returncode == 0:
            return
        git(root, "fetch", "-q", check=False)
        if not git(root, "rev-list", "HEAD..@{u}", check=False).strip():  # not a race: auth, no upstream, …
            raise GitError(f"git push failed: {proc.stderr.strip()}")
        if both := sorted(set(_moved(root, "HEAD...@{u}")) & set(_moved(root, "@{u}...HEAD"))):
            raise Stale(both)
        if subprocess.run(["git", "rebase", "-q", "--autostash", "@{u}"], cwd=root, capture_output=True).returncode:
            git(root, "rebase", "--abort", check=False)
            raise GitError("rebasing onto the remote failed; your commits stay local. Run `git pull --rebase`, "
                           "resolve, then push.")
    raise GitError(f"push rejected {tries} times in a row; try again")


def show(root: Path, rev: str, path: str) -> str | None:
    out = subprocess.run(["git", "show", f"{rev}:{path}"], cwd=root, capture_output=True, text=True)
    return out.stdout if out.returncode == 0 else None


def adding_commit(root: Path, path: str) -> str | None:
    out = git(root, "log", "--diff-filter=A", "--format=%H", "-1", "--", path, check=False).strip()
    return out or None


def trailers(root: Path, rev: str) -> dict[str, str]:
    out = git(root, "log", "-1", "--format=%(trailers:unfold,only)", rev, check=False)
    found: dict[str, str] = {}
    for line in out.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            found[key.strip()] = value.strip()
    return found


def changed_since(root: Path, rev: str, path: str) -> list[str]:
    out = git(root, "diff", "--name-only", f"{rev}..HEAD", "--", path, check=False)
    return [p for p in out.splitlines() if p]


def user(root: Path) -> str:
    return (git(root, "config", "user.email", check=False) or git(root, "config", "user.name", check=False)).strip()


@contextlib.contextmanager
def lock(root: Path) -> Iterator[None]:
    """Exclusive per-repo lock so two local BrainForge runs can't write at once."""
    git_dir = Path(git(root, "rev-parse", "--absolute-git-dir").strip())
    with open(git_dir / "brainforge.lock", "w") as fh:
        try:
            fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise GitError("another BrainForge run holds the lock on this repository") from None
        yield
