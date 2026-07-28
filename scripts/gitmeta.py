#!/usr/bin/env python3
"""
Git-derived metadata: file listing, last-commit times, and changed-file sets.

last_commit_times() deliberately uses a single `git log` traversal rather than
one `git log -1` per file. Reverse-chronological order means a path's first
occurrence in the output is its most recent commit, so one subprocess replaces
N of them.
"""

from __future__ import annotations

import subprocess

RECORD_SEP = '\x1e'
MAX_BYTES = 64 * 1024 * 1024


def _run(repo_root: str, args: list[str]) -> str:
    return subprocess.run(
        ['git', *args], cwd=repo_root, check=True,
        capture_output=True, text=True, errors='replace',
    ).stdout


def is_git_repo(repo_root: str) -> bool:
    """True when repo_root is inside a git work tree."""
    try:
        out = _run(repo_root, ['rev-parse', '--is-inside-work-tree'])
    except (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError):
        return False
    return out.strip() == 'true'


def list_files(repo_root: str) -> list[str]:
    """Tracked and untracked-but-not-ignored files, repo-relative POSIX paths."""
    out = _run(repo_root, [
        'ls-files', '--cached', '--others', '--exclude-standard', '-z',
    ])
    return [p for p in out.split('\0') if p]


def last_commit_times(repo_root: str, paths: list[str]) -> dict[str, str | None]:
    """Map each path to its most recent committer ISO time, or None."""
    times: dict[str, str | None] = {p: None for p in paths}
    if not paths:
        return times

    try:
        out = _run(repo_root, [
            '-c', 'core.quotePath=false',
            'log', '--diff-merges=first-parent',
            f'--format={RECORD_SEP}%cI', '--name-only', '--', *paths,
        ])
    except subprocess.CalledProcessError:
        return times  # no commits yet, or git failed — everything stays None

    current: str | None = None
    for line in out.split('\n'):
        if line.startswith(RECORD_SEP):
            current = line[1:].strip() or None
        elif line and current is not None and times.get(line, 'x') is None:
            # First occurrence is the most recent commit; later ones are older.
            times[line] = current
    return times


def changed_since(repo_root: str, ref: str) -> set[str]:
    """Files changed between the merge base with `ref` and the working tree."""
    try:
        out = _run(repo_root, ['diff', '--name-only', f'{ref}...HEAD'])
    except subprocess.CalledProcessError:
        return set()
    return {line for line in out.split('\n') if line}
