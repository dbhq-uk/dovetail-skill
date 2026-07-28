#!/usr/bin/env python3
"""
Glob matching with correct `**` semantics.

Python's fnmatch treats `**` as `*` and would match across path separators
incorrectly; pathspec would be a third-party dependency. This is a small
purpose-built translator instead.

Semantics:
  *     matches any run of characters except `/`
  **    matches any run of characters including `/`
  **/   matches zero or more whole path segments (so `**/README.md`
        matches `README.md` as well as `docs/README.md`, but never
        `xREADME.md`)
  ?     matches exactly one character except `/`
"""

from __future__ import annotations

import re
from typing import Iterable


def glob_to_regex(glob: str) -> re.Pattern:
    """Translate a glob into an anchored regex."""
    out: list[str] = []
    i = 0
    n = len(glob)
    while i < n:
        c = glob[i]
        if c == '*':
            if i + 1 < n and glob[i + 1] == '*':
                i += 1
                if i + 1 < n and glob[i + 1] == '/':
                    # `**/` anchors on a segment boundary, including zero segments
                    out.append('(?:.*/)?')
                    i += 1
                else:
                    out.append('.*')
            else:
                out.append('[^/]*')
        elif c == '?':
            out.append('[^/]')
        else:
            out.append(re.escape(c))
        i += 1
    return re.compile('^' + ''.join(out) + r'\Z')


def matches_any(path: str, globs: Iterable[str]) -> bool:
    """True when `path` matches at least one glob."""
    return any(glob_to_regex(g).match(path) for g in globs)
