#!/usr/bin/env python3
"""
Build the repository inventory: one entry per file with the deterministic
evidence every later layer relies on.

Entries that cannot be read as a file — broken symlinks, submodule gitlinks,
directories — are skipped rather than raised, so one odd entry cannot fail a
whole scan.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import os

from classify import classify
from gitmeta import last_commit_times, list_files
from globmatch import matches_any


def discover(repo_root: str, ignore: list[str] | None = None) -> dict:
    """Return the Inventory for repo_root."""
    root = os.path.abspath(repo_root)
    ignore = ignore or []

    paths = [p for p in list_files(root) if not matches_any(p, ignore)]
    times = last_commit_times(root, paths)

    files: list[dict] = []
    for path in sorted(paths):
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
    }
