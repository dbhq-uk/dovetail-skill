#!/usr/bin/env python3
"""
Typed reference graph over repository files.

Every edge records where it came from (`src`, `line`), how it was written
(`kind`, `raw`), and what it resolves to (`dst`, `anchor`). This replaces the
basename word-boundary heuristic the prior tool uses, where the word "config"
in a sentence counted as a reference to `config.py` - false edges of that kind
silently hide real orphans, because an orphan check treats any inbound edge as
proof the file is alive.

Recognised edge kinds:
  md_link       [text](target)
  md_image      ![alt](target)
  md_refdef     [label]: target
  html          src="target" / href="target"
  import        from './x.js' / require('./x.js') quoted specifiers (JS/TS),
                and Python imports resolved via ast
  path_literal  a bare slash-bearing path with an extension, in prose or code

Each kind is only searched for in files where it can mean something: a
quoted JS specifier or an HTML attribute has no meaning in Python source, and
a bare `import` has no meaning in markdown. See `_kinds_for`.
"""

from __future__ import annotations

import ast
import os
import posixpath
import re
import sys
from urllib.parse import unquote

from slugify import heading_slugs, track_fence

TEXT_MODALITIES = {'text', 'vector_diagram'}

# A destination wrapped in <> is CommonMark's way of carrying spaces, which is
# the only reason the form exists. Matching `<?([^)\s>]+)>?` looks like support
# for it but is not: the character class still stops at the first space, so
# `[x](<a b.pdf>)` silently resolved to `a` and the file read as an orphan.
# The alternation keeps the brackets in the capture; `_unbracket` strips them.
_MD_LINK = re.compile(r'(!?)\[[^\]]*\]\(\s*(<[^<>\n]*>|[^)\s>]+)[^)]*\)')
_MD_REFDEF = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*(<[^<>\n]*>|[^\s>]+)')
_HTML_ATTR = re.compile(r'\b(?:src|href)\s*=\s*["\']([^"\']+)["\']')
_IMPORT = re.compile(r"""(?:from|require\s*\(|import)\s*['"]([^'"]+)['"]""")
_PATH_LITERAL = re.compile(r'(?<![\w/])((?:\.{1,2}/)?(?:[\w.-]+/)+[\w.-]+\.\w*[A-Za-z]\w*)')

# TypeScript sources compile to these specifiers, so an import of './a.js'
# resolves to a.ts when a.js does not exist.
_JS_TO_TS = {'.js': ['.ts', '.tsx'], '.jsx': ['.tsx'], '.mjs': ['.mts'], '.cjs': ['.cts']}

_EXTERNAL = re.compile(r'^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|//)')

# Which edge kinds are meaningful in which sources. Running a JS-specifier or
# markdown-link regex over Python source fabricates edges out of docstrings and
# comments — a false edge makes a dead file look alive and hides a real orphan.
_MARKDOWN_KINDS = frozenset({'md_link', 'md_image', 'md_refdef', 'html', 'path_literal'})
_JS_KINDS = frozenset({'import', 'path_literal'})
_MARKUP_KINDS = frozenset({'html', 'path_literal'})
# Python import edges come from `ast`, not from `_scan_line`; `path_literal`
# still applies so a real reference like open('data/config.json') is captured.
_PYTHON_KINDS = frozenset({'path_literal'})
_DEFAULT_KINDS = frozenset({'path_literal'})

_MARKDOWN_EXTS = frozenset({'.md', '.markdown'})
_JS_EXTS = frozenset({'.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs'})
_MARKUP_EXTS = frozenset({'.html', '.htm', '.svg'})


def _kinds_for(path: str) -> frozenset:
    """Edge kinds worth looking for in this file's language."""
    ext = posixpath.splitext(path.lower())[1]
    if ext in _MARKDOWN_EXTS:
        return _MARKDOWN_KINDS
    if ext == '.py':
        return _PYTHON_KINDS
    if ext in _JS_EXTS:
        return _JS_KINDS
    if ext in _MARKUP_EXTS:
        return _MARKUP_KINDS
    return _DEFAULT_KINDS


def _is_external(target: str) -> bool:
    return bool(_EXTERNAL.match(target))


def _unbracket(target: str) -> str:
    """Strip CommonMark's <> destination wrapper, if present."""
    if len(target) > 1 and target.startswith('<') and target.endswith('>'):
        return target[1:-1].strip()
    return target


def _resolve(src: str, target: str, known: set[str], *,
             allow_root_fallback: bool = False) -> str | None:
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

    # Deliberately restricted to path_literal: a bare markdown or HTML target
    # is unambiguously relative to its own file, so when it does not resolve
    # there it must be reported as a broken link, not silently redirected. A
    # bare target in Python code (no explicit './' or '../' prefix) may be
    # written relative to the repo root instead of the referencing file's own
    # directory -- e.g. a Python `open('data/config.json')` call resolves
    # against the process's working directory, not against the importing
    # module's folder.
    if allow_root_fallback and not target.startswith(('/', './', '../')):
        root_candidate = posixpath.normpath(target)
        if not root_candidate.startswith('..') and root_candidate in known:
            return root_candidate
    return None


def _split_anchor(target: str) -> tuple[str, str | None]:
    if '#' not in target:
        return target, None
    path_part, _, anchor = target.partition('#')
    return path_part, (anchor or None)


def _python_imports(body: str) -> list[tuple[int, str, str]]:
    """Extract (lineno, dots, module) for every import in a Python source.

    Uses ast rather than regex so that import-shaped lines inside docstrings
    and comments cannot create edges — a false edge makes a dead file look
    alive and hides a real orphan, which is worse than missing one.

    A file that does not parse yields no import edges rather than raising.
    """
    try:
        tree = ast.parse(body)
    except (SyntaxError, ValueError):
        return []

    found: list[tuple[int, str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((node.lineno, '', alias.name))
        elif isinstance(node, ast.ImportFrom):
            found.append((node.lineno, '.' * (node.level or 0), node.module or ''))
    return found


def _resolve_py_module(src: str, dots: str, module: str, known: set[str]) -> str | None:
    """Resolve a Python import to a repo file, or None if it is not one of ours.

    Tries the importing file's own directory first (how these skills import
    siblings), then a package path from the repository root. `import os`
    resolves to neither, so stdlib imports correctly produce no edge.
    """
    parts = [p for p in module.split('.') if p] if module else []

    # An absolute import of a stdlib name is ambiguous when the repo also
    # contains a file of that name; prefer no edge over a false one.
    if not dots and parts and parts[0] in sys.stdlib_module_names:
        return None

    bases = []
    if dots:
        base = posixpath.dirname(src)
        for _ in range(len(dots) - 1):
            base = posixpath.dirname(base)
        bases.append(base)
    else:
        bases.append(posixpath.dirname(src))
        bases.append('')

    for base in bases:
        stem = posixpath.normpath(posixpath.join(base, *parts)) if parts else base
        if not stem or stem.startswith('..'):
            continue
        for candidate in (f'{stem}.py', posixpath.join(stem, '__init__.py')):
            if candidate in known:
                return candidate
    return None


def _scan_line(line: str, allowed: frozenset) -> list[tuple[str, str]]:
    """Return (kind, raw_target) pairs found in one line.

    Only runs the patterns whose kind is in `allowed`, so a disallowed kind
    (e.g. a markdown link inside a Python file) can never consume a target
    and thereby suppress a kind that is allowed there.
    """
    found: list[tuple[str, str]] = []
    consumed: set[str] = set()

    def consume(target: str) -> None:
        # Record the target and its path part, so the path-literal sweep does
        # not re-report `docs/a.md` after `docs/a.md#install` was already seen.
        consumed.add(target)
        consumed.add(target.partition('#')[0])

    if 'md_link' in allowed or 'md_image' in allowed:
        for bang, raw_target in _MD_LINK.findall(line):
            kind = 'md_image' if bang else 'md_link'
            if kind in allowed:
                target = _unbracket(raw_target)
                found.append((kind, target))
                consume(target)

    if 'md_refdef' in allowed:
        match = _MD_REFDEF.match(line)
        if match:
            target = _unbracket(match.group(1))
            found.append(('md_refdef', target))
            consume(target)

    if 'html' in allowed:
        for target in _HTML_ATTR.findall(line):
            if target not in consumed:
                found.append(('html', target))
                consume(target)

    if 'import' in allowed:
        for target in _IMPORT.findall(line):
            # A bare specifier (no './', '../' or '/' prefix) names a package,
            # not a path — e.g. `import React from 'react'`. Only relative or
            # absolute specifiers are file references worth an edge.
            if target not in consumed and target.startswith(('./', '../', '/')):
                found.append(('import', target))
                consume(target)

    if 'path_literal' in allowed:
        for target in _PATH_LITERAL.findall(line):
            if target not in consumed:
                found.append(('path_literal', target))
                consume(target)

    return found


def build_graph(repo_root: str, inventory: dict) -> dict:
    """Build the reference graph for an inventory."""
    root = os.path.abspath(repo_root)
    # Resolution must see the whole repository even when reporting is scoped
    # by --ignore: `inventory['files']` is filtered, but a link into an
    # ignored path is still a real, resolvable file on disk. Falling back to
    # the file list keeps existing callers and test fixtures (which only ever
    # set 'files') working unchanged.
    known = set(inventory['all_paths']) if 'all_paths' in inventory \
        else {f['path'] for f in inventory['files']}
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

        allowed = _kinds_for(path)

        fence: str | None = None
        for lineno, line in enumerate(body.split('\n'), start=1):
            fence, is_fence_line = track_fence(fence, line)
            if is_fence_line:
                continue
            if fence is not None:
                continue

            for kind, raw in _scan_line(line, allowed):
                if _is_external(raw):
                    continue
                path_part, anchor = _split_anchor(raw)
                if not path_part:
                    dst = path  # pure `#anchor` — same document
                else:
                    # A path containing a space must be percent-encoded to
                    # survive `_MD_LINK`'s whitespace boundary — decode before
                    # resolving. Fall back to the raw form so a literal '%'
                    # in a filename (not a valid escape) still resolves.
                    decoded = unquote(path_part)
                    dst = _resolve(path, decoded, known,
                                    allow_root_fallback=(kind == 'path_literal'))
                    if dst is None and decoded != path_part:
                        dst = _resolve(path, path_part, known,
                                        allow_root_fallback=(kind == 'path_literal'))
                edges.append({
                    'src': path, 'line': lineno, 'kind': kind,
                    'raw': raw, 'dst': dst, 'anchor': anchor,
                })

        if path.lower().endswith('.py'):
            for lineno, dots, module in _python_imports(body):
                dst = _resolve_py_module(path, dots, module, known)
                if dst is not None:
                    edges.append({'src': path, 'line': lineno, 'kind': 'import',
                                  'raw': f'{dots}{module}', 'dst': dst,
                                  'anchor': None})

    inbound: dict[str, list[str]] = {p: [] for p in known}
    for edge in edges:
        dst = edge['dst']
        if dst is None or dst == edge['src']:
            continue
        if edge['src'] not in inbound[dst]:
            inbound[dst].append(edge['src'])

    return {'edges': edges, 'inbound': inbound, 'headings': headings}
