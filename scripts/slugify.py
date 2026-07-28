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
_ATX = re.compile(r'^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$')
_FENCE = re.compile(r'^ {0,3}((?:`{3,})|(?:~{3,}))')


def slugify(heading_text: str) -> str:
    """Convert heading text to its GitHub anchor slug."""
    text = heading_text.strip().lower()
    text = _DROP.sub('', text)
    text = _SPACES.sub('-', text)
    return text.strip('-')


def heading_slugs(markdown: str) -> list[str]:
    """Slugs for every ATX heading, in document order, with duplicates suffixed."""
    slugs: list[str] = []
    occurrences: dict[str, int] = {}
    fence: str | None = None   # the opening delimiter run, e.g. '```'

    for line in markdown.split('\n'):
        fence_match = _FENCE.match(line)
        if fence_match:
            marker = fence_match.group(1)
            if fence is None:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = None
            continue
        if fence is not None:
            continue
        match = _ATX.match(line)
        if not match:
            continue
        base = slugify(match.group(2))
        if not base:
            continue
        # github-slugger's algorithm: every emitted slug is registered, and a
        # collision bumps the counter on the *base* until the result is unused.
        # This is what stops a literal `Setup-1` heading colliding with the
        # `setup-1` generated for a second `Setup`.
        result = base
        while result in occurrences:
            occurrences[base] = occurrences.get(base, 0) + 1
            result = f'{base}-{occurrences[base]}'
        occurrences[result] = 0
        slugs.append(result)

    return slugs
