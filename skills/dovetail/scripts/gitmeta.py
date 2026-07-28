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

# Failures every public function degrades on rather than propagating:
# git missing (FileNotFoundError), repo_root not a directory
# (NotADirectoryError), or git itself reporting an error (CalledProcessError).
_SOFT_ERRORS = (subprocess.CalledProcessError, FileNotFoundError, NotADirectoryError)

# `last_commit_times` passes every path as a pathspec argument to a single
# `git log` call. Linux ARG_MAX is ~2MB, so an unbatched call raises a plain
# OSError (E2BIG) somewhere around 50-70k paths -- not one of _SOFT_ERRORS, so
# it must not happen at all rather than being caught. This chunk size keeps
# each command line well under that limit for any realistic path length.
_PATH_CHUNK_SIZE = 2000


def _run(repo_root: str, args: list[str]) -> str:
    return subprocess.run(
        ['git', *args], cwd=repo_root, check=True,
        capture_output=True, text=True, errors='replace',
    ).stdout


def is_git_repo(repo_root: str) -> bool:
    """True when repo_root is inside a git work tree."""
    try:
        out = _run(repo_root, ['rev-parse', '--is-inside-work-tree'])
    except _SOFT_ERRORS:
        return False
    return out.strip() == 'true'


def list_files(repo_root: str) -> list[str]:
    """Tracked and untracked-but-not-ignored files, repo-relative POSIX paths.

    Degrades like `is_git_repo`: callers are expected to check `is_git_repo()`
    first, so an empty list here means "nothing to scan" — either a genuinely
    empty repo, or a non-repo/missing-git failure that was swallowed rather
    than a silently-lost error.
    """
    try:
        out = _run(repo_root, [
            'ls-files', '--cached', '--others', '--exclude-standard', '-z',
        ])
    except _SOFT_ERRORS:
        return []
    return [p for p in out.split('\0') if p]


def last_commit_times(repo_root: str, paths: list[str]) -> dict[str, str | None]:
    """Map each path to its most recent committer ISO time, or None.

    `paths` is chunked into batches of `_PATH_CHUNK_SIZE` so the command line
    for any single `git log` invocation cannot overflow ARG_MAX. `times` is
    one dict shared across every batch, so the existing first-occurrence-wins
    guard (`times.get(line, 'x') is None`) still gives the most recent commit
    overall: each batch is independently reverse-chronological for its own
    paths, and a path only ever appears in one batch.
    """
    times: dict[str, str | None] = {p: None for p in paths}
    if not paths:
        return times

    for start in range(0, len(paths), _PATH_CHUNK_SIZE):
        batch = paths[start:start + _PATH_CHUNK_SIZE]
        try:
            out = _run(repo_root, [
                '-c', 'core.quotePath=false',
                'log', '--diff-merges=first-parent',
                f'--format={RECORD_SEP}%cI', '--name-only', '--', *batch,
            ])
        except _SOFT_ERRORS:
            continue  # no commits yet, or git failed — this batch stays None

        current: str | None = None
        for line in out.split('\n'):
            if line.startswith(RECORD_SEP):
                current = line[1:].strip() or None
            elif line and current is not None and times.get(line, 'x') is None:
                # First occurrence is the most recent commit; later ones are older.
                times[line] = current
    return times


def rev_exists(repo_root: str, ref: str) -> bool:
    """True when `ref` resolves to a commit in this repository."""
    try:
        _run(repo_root, ['rev-parse', '--verify', '--quiet', f'{ref}^{{commit}}'])
    except _SOFT_ERRORS:
        return False
    return True


def changed_since(repo_root: str, ref: str) -> set[str]:
    """Files changed between the merge base with `ref` and HEAD.

    Uncommitted and staged changes are not reflected — only committed diffs
    up to HEAD are considered.
    """
    try:
        out = _run(repo_root, ['diff', '--name-only', f'{ref}...HEAD'])
    except _SOFT_ERRORS:
        return set()
    return {line for line in out.split('\n') if line}
