#!/usr/bin/env python3
"""
Typed reference graph over repository files.

Every edge records where it came from (`src`, `line`), how it was written
(`kind`, `raw`), and what it resolves to (`dst`, `anchor`). This replaces the
basename word-boundary heuristic upkeep uses, where the word "config" in a
sentence counted as a reference to `config.py` — false edges of that kind
silently hide real orphans, because an orphan check treats any inbound edge as
proof the file is alive.

Recognised edge kinds:
  md_link       [text](target)
  md_image      ![alt](target)
  md_refdef     [label]: target
  html          src="target" / href="target"
  import        from './x.js' / require('./x.js') — quoted specifiers only
  path_literal  a bare slash-bearing path with an extension, in prose or code
"""

from __future__ import annotations

import os
import posixpath
import re

from slugify import heading_slugs

TEXT_MODALITIES = {'text', 'vector_diagram'}

_FENCE = re.compile(r'^\s*(```|~~~)')
_MD_LINK = re.compile(r'(!?)\[[^\]]*\]\(\s*<?([^)\s>]+)>?[^)]*\)')
_MD_REFDEF = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*<?([^\s>]+)>?')
_HTML_ATTR = re.compile(r'\b(?:src|href)\s*=\s*["\']([^"\']+)["\']')
_IMPORT = re.compile(r"""(?:from|require\s*\(|import)\s*['"]([^'"]+)['"]""")
_PATH_LITERAL = re.compile(r'(?<![\w/])((?:\.{1,2}/)?(?:[\w.-]+/)+[\w.-]+\.\w+)')

# TypeScript sources compile to these specifiers, so an import of './a.js'
# resolves to a.ts when a.js does not exist.
_JS_TO_TS = {'.js': ['.ts', '.tsx'], '.jsx': ['.tsx'], '.mjs': ['.mts'], '.cjs': ['.cts']}

_EXTERNAL = re.compile(r'^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)')


def _is_external(target: str) -> bool:
    return bool(_EXTERNAL.match(target))


def _resolve(src: str, target: str, known: set[str]) -> str | None:
    """Resolve a link target to a repo-relative path, or None."""
    if target.startswith('/'):
        candidate = target.lstrip('/')
    else:
        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(src), target))
    if candidate.startswith('..'):
        return None
    if candidate in known:
        return candidate

    stem, ext = posixpath.splitext(candidate)
    for alt_ext in _JS_TO_TS.get(ext, []):
        if stem + alt_ext in known:
            return stem + alt_ext
    return None


def _split_anchor(target: str) -> tuple[str, str | None]:
    if '#' not in target:
        return target, None
    path_part, _, anchor = target.partition('#')
    return path_part, (anchor or None)


def _scan_line(line: str) -> list[tuple[str, str]]:
    """Return (kind, raw_target) pairs found in one line."""
    found: list[tuple[str, str]] = []
    consumed: set[str] = set()

    def consume(target: str) -> None:
        # Record the target and its path part, so the path-literal sweep does
        # not re-report `docs/a.md` after `docs/a.md#install` was already seen.
        consumed.add(target)
        consumed.add(target.partition('#')[0])

    for bang, target in _MD_LINK.findall(line):
        found.append(('md_image' if bang else 'md_link', target))
        consume(target)

    match = _MD_REFDEF.match(line)
    if match:
        found.append(('md_refdef', match.group(1)))
        consume(match.group(1))

    for target in _HTML_ATTR.findall(line):
        if target not in consumed:
            found.append(('html', target))
            consume(target)

    for target in _IMPORT.findall(line):
        if target not in consumed:
            found.append(('import', target))
            consume(target)

    for target in _PATH_LITERAL.findall(line):
        if target not in consumed:
            found.append(('path_literal', target))
            consume(target)

    return found


def build_graph(repo_root: str, inventory: dict) -> dict:
    """Build the reference graph for an inventory."""
    root = os.path.abspath(repo_root)
    known = {f['path'] for f in inventory['files']}
    text_paths = [f['path'] for f in inventory['files'] if f['modality'] in TEXT_MODALITIES]

    edges: list[dict] = []
    headings: dict[str, list[str]] = {}

    for path in text_paths:
        try:
            with open(os.path.join(root, path), 'r', encoding='utf-8', errors='replace') as fh:
                body = fh.read()
        except OSError:
            continue

        if path.lower().endswith(('.md', '.markdown')):
            headings[path] = heading_slugs(body)

        in_fence = False
        for lineno, line in enumerate(body.split('\n'), start=1):
            if _FENCE.match(line):
                in_fence = not in_fence
                continue
            if in_fence:
                continue

            for kind, raw in _scan_line(line):
                if _is_external(raw):
                    continue
                path_part, anchor = _split_anchor(raw)
                if not path_part:
                    dst = path  # pure `#anchor` — same document
                else:
                    dst = _resolve(path, path_part, known)
                edges.append({
                    'src': path, 'line': lineno, 'kind': kind,
                    'raw': raw, 'dst': dst, 'anchor': anchor,
                })

    inbound: dict[str, list[str]] = {p: [] for p in known}
    for edge in edges:
        dst = edge['dst']
        if dst is None or dst == edge['src']:
            continue
        if edge['src'] not in inbound[dst]:
            inbound[dst].append(edge['src'])

    return {'edges': edges, 'inbound': inbound, 'headings': headings}
