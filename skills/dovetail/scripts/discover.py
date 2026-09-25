#!/usr/bin/env python3
"""
Build the repository inventory: one entry per file with the deterministic
evidence every later layer relies on.

Entries that cannot be read as a file — broken symlinks, submodule gitlinks,
directories — are skipped rather than raised, so one odd entry cannot fail a
whole scan.

A symlink to another file the scan reads is recorded in `symlinks` and left
out of `files`. Its content is its target's, so reading it again reported
every finding in the target twice, and the pair as an exact duplicate. It
stays in `all_paths`, so a link to it still resolves.

The decisions ledger is left out of `files` too, and kept in `all_paths`. It
is dovetail's own state, and each row's summary names the file it is about.
Read as text, that summary is a reference: an orphan the user marked
intentional stopped being an orphan, so the decision matched nothing.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os

from classify import classify
from gitmeta import last_commit_times, list_files
from globmatch import matches_any
from store import DECISIONS_REL

# Inventory paths always use `/`, whatever the host's separator.
LEDGER_PATH = DECISIONS_REL.replace(os.sep, '/')


def discover(repo_root: str, ignore: list[str] | None = None) -> dict:
    """Return the Inventory for repo_root."""
    root = os.path.abspath(repo_root)
    ignore = ignore or []

    all_paths = list_files(root)
    paths = [p for p in all_paths if not matches_any(p, ignore)]
    times = last_commit_times(root, paths)

    scanned = set(paths)
    symlinks: dict[str, str] = {}
    files: list[dict] = []
    for path in sorted(paths):
        if path == LEDGER_PATH:
            continue
        target = _symlink_target(root, path)
        if target is not None and target in scanned and target != path:
            symlinks[path] = target
            continue
        try:
            with open(os.path.join(root, path), 'rb') as fh:
                content = fh.read()
        except OSError:
            continue  # broken symlink, gitlink, or directory — not a file
        modality, category = classify(path, content)
        files.append({
            'path': path,
            'modality': modality,
            'category': category,
            'size_bytes': len(content),
            'sha256': hashlib.sha256(content).hexdigest(),
            'last_commit_iso': times.get(path),
        })

    return {
        'repo_root': root,
        'generated_at_iso': _dt.datetime.now(_dt.timezone.utc).isoformat(),
        'files': files,
        'all_paths': sorted(all_paths),
        'symlinks': symlinks,
    }


def _symlink_target(root: str, path: str) -> str | None:
    """The repo-relative file a symlink points at, or None if it is not a link into the repo."""
    full = os.path.join(root, path)
    if not os.path.islink(full):
        return None
    real_root = os.path.realpath(root)
    target = os.path.realpath(full)
    if os.path.commonpath([real_root, target]) != real_root:
        return None
    return os.path.relpath(target, real_root).replace(os.sep, '/')
