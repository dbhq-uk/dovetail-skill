#!/usr/bin/env python3
"""
GitHub-compatible heading slugs.

GitHub's algorithm, which is what actually renders the documents:
  lowercase, drop everything that is not a word character, space, or hyphen,
  then replace spaces with hyphens. Repeated slugs get `-1`, `-2`, and so on
  in document order.

Fenced code blocks are skipped so a `# comment` inside an example is not
mistaken for a heading.
"""

from __future__ import annotations

import re

_DROP = re.compile(r'[^\w\s-]', re.UNICODE)
_SPACES = re.compile(r'\s')
_ATX = re.compile(r'^(#{1,6})\s+(.*?)\s*#*\s*$')
_FENCE = re.compile(r'^\s*(```|~~~)')


def slugify(heading_text: str) -> str:
    """Convert heading text to its GitHub anchor slug."""
    text = heading_text.strip().lower()
    text = _DROP.sub('', text)
    text = _SPACES.sub('-', text)
    return text.strip('-')


def heading_slugs(markdown: str) -> list[str]:
    """Slugs for every ATX heading, in document order, with duplicates suffixed."""
    slugs: list[str] = []
    seen: dict[str, int] = {}
    in_fence = False

    for line in markdown.split('\n'):
        if _FENCE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _ATX.match(line)
        if not match:
            continue
        base = slugify(match.group(2))
        if not base:
            continue
        count = seen.get(base, 0)
        seen[base] = count + 1
        slugs.append(base if count == 0 else f'{base}-{count}')

    return slugs
