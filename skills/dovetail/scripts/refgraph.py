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

import textcache
from slugify import anchor_ids, track_fence

TEXT_MODALITIES = {'text', 'vector_diagram'}

# A destination wrapped in <> is CommonMark's way of carrying spaces, which is
# the only reason the form exists. Matching `<?([^)\s>]+)>?` looks like support
# for it but is not: the character class still stops at the first space, so
# `[x](<a b.pdf>)` silently resolved to `a` and the file read as an orphan.
# The alternation keeps the brackets in the capture; `_unbracket` strips them.
#
# The link text may hold one level of brackets, so a badge that links
# somewhere - `[![build](badge.svg)](ci.md)` - is read as the link it is. The
# old `[^\]]*` stopped at the image's own `]` and lost the outer target.
_MD_LINK = re.compile(
    r'(!?)\[((?:[^\[\]]|\[[^\[\]]*\])*)\]\(\s*(<[^<>\n]*>|[^)\s>]+)[^)]*\)')
# A run of backticks. An inline code span opens with one and closes at the
# next run of the same length; what is inside is an example, not a link.
_BACKTICKS = re.compile(r'`+')
# Link text that wraps is joined with the lines that follow it, up to this many
# lines in all, and never across a blank line.
_MAX_WRAPPED_LINES = 4
_MD_REFDEF = re.compile(r'^\s{0,3}\[[^\]]+\]:\s*(<[^<>\n]*>|[^\s>]+)')
_HTML_ATTR = re.compile(r'\b(?:src|href)\s*=\s*["\']([^"\']+)["\']')
_IMPORT = re.compile(r"""(?:from|require\s*\(|import)\s*['"]([^'"]+)['"]""")
_PATH_LITERAL = re.compile(r'(?<![\w/])((?:\.{1,2}/)?(?:[\w.-]+/)+[\w.-]+\.\w*[A-Za-z]\w*)')

# TypeScript sources compile to these specifiers, so an import of './a.js'
# resolves to a.ts when a.js does not exist.
_JS_TO_TS = {'.js': ['.ts', '.tsx'], '.jsx': ['.tsx'], '.mjs': ['.mts'], '.cjs': ['.cts']}

# What a bundler, TypeScript or Node tries for a specifier written without an
# extension, in order. `from "./site"` is the normal way to write an import in
# a TypeScript project, and reporting it as a broken link failed nearly every
# pull request in one. `.json` is last because Node's require resolves it too.
_JS_IMPLICIT_EXTS = ('.ts', '.tsx', '.d.ts', '.mts', '.cts',
                     '.js', '.jsx', '.mjs', '.cjs', '.json')

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
             allow_root_fallback: bool = False,
             js_specifier: bool = False) -> str | None:
    """Resolve a link target to a repo-relative path, or None.

    `js_specifier` marks a JS/TS import, which may leave out the extension or
    name a directory holding an `index` file. Those are only tried after the
    specifier fails to resolve as written.
    """
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

    if js_specifier:
        # Appended, not substituted: in `./app.module` the `.module` is part
        # of the name, and the file is app.module.ts.
        for base in (candidate, posixpath.join(candidate, 'index')):
            for implicit in _JS_IMPLICIT_EXTS:
                if base + implicit in known:
                    return base + implicit

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


def written_target(src: str, raw: str) -> str | None:
    """The repo-relative path a reference names, whether or not it exists.

    `--since` needs this for a link that no longer resolves: its `dst` is
    None, but the path it was written to point at is what a deleted or
    renamed file shows up as in the diff.
    """
    if _is_external(raw):
        return None
    path_part, _ = _split_anchor(raw)
    if not path_part:
        return src
    path_part = unquote(_unbracket(path_part))
    if path_part.startswith('/'):
        candidate = path_part.lstrip('/')
    else:
        candidate = posixpath.normpath(posixpath.join(posixpath.dirname(src), path_part))
    return None if candidate.startswith('..') or candidate == '.' else candidate


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


def _mask_code_spans(text: str) -> str:
    """`text` with every inline code span blanked out, so offsets still line up.

    Done by pairing backtick runs rather than with one regex: a regex that
    looks for "the next run of the same length" backtracks on every start
    position, and a long line of backticks took minutes.
    """
    if '`' not in text:
        return text
    runs = [(m.start(), m.end() - m.start()) for m in _BACKTICKS.finditer(text)]
    # later[n] holds the indexes of runs of length n, last first, so the
    # nearest one still ahead is always at the end of the list.
    later: dict[int, list[int]] = {}
    for index in range(len(runs) - 1, -1, -1):
        later.setdefault(runs[index][1], []).append(index)
    out = list(text)
    index = 0
    while index < len(runs):
        start, length = runs[index]
        same = later[length]
        while same and same[-1] <= index:
            same.pop()
        if not same:
            index += 1  # nothing closes it: these backticks are literal
            continue
        close = same.pop()
        end = runs[close][0] + length
        out[start:end] = ' ' * (end - start)
        index = close + 1
    return ''.join(out)


def _md_links(text: str) -> list[tuple[str, str]]:
    """(bang, target) for every markdown link or image, including one inside link text."""
    found: list[tuple[str, str]] = []
    for match in _MD_LINK.finditer(text):
        found.append((match.group(1), match.group(3)))
        if '](' in match.group(2):
            found += _md_links(match.group(2))
    return found


def _opens_link_text(text: str) -> bool:
    """Whether `text` leaves a `[` open, so link text may carry on past it."""
    if '[' not in text:
        return False
    depth = 0
    for char in _mask_code_spans(text):
        if char == '[':
            depth += 1
        elif char == ']' and depth:
            depth -= 1
    return depth > 0


def _scan_line(line: str, allowed: frozenset) -> list[tuple[str, str]]:
    """Return (kind, raw_target) pairs found in one line.

    Only runs the patterns whose kind is in `allowed`, so a disallowed kind
    (e.g. a markdown link inside a Python file) can never consume a target
    and thereby suppress a kind that is allowed there.

    Links and HTML attributes are matched with inline code spans blanked
    out: `` `[text](path/to/file.md)` `` shows how to write a link, and was
    reported as a broken one. Bare path literals still count inside code
    spans, because a path in backticks is a real reference to that file.
    """
    found: list[tuple[str, str]] = []
    consumed: set[str] = set()
    # Code spans are a markdown idea, so only markdown has them masked.
    markup = _mask_code_spans(line) if 'md_link' in allowed else line

    def consume(target: str) -> None:
        # Record the target and its path part, so the path-literal sweep does
        # not re-report `docs/a.md` after `docs/a.md#install` was already seen.
        consumed.add(target)
        consumed.add(target.partition('#')[0])

    # Each pattern needs a character the line may not have. Most lines have
    # none of them, and testing for the character first skips the regex
    # without changing what it would have found.
    if ('md_link' in allowed or 'md_image' in allowed) and '](' in markup:
        for bang, raw_target in _md_links(markup):
            kind = 'md_image' if bang else 'md_link'
            if kind in allowed:
                target = _unbracket(raw_target)
                found.append((kind, target))
                consume(target)

    if 'md_refdef' in allowed:
        match = _MD_REFDEF.match(markup)
        if match:
            target = _unbracket(match.group(1))
            found.append(('md_refdef', target))
            consume(target)

    if 'html' in allowed and '=' in markup:
        for target in _HTML_ATTR.findall(markup):
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

    if 'path_literal' in allowed and '/' in line:
        for target in _PATH_LITERAL.findall(line):
            if target not in consumed:
                found.append(('path_literal', target))
                consume(target)

    return found


def _logical_lines(body: str, is_markdown: bool) -> list[tuple[int, str]]:
    """(first line number, text) for every line outside a code fence.

    In markdown, a line that leaves link text open is joined with the lines
    after it in the same paragraph, so a link whose text wraps is still read:

        See [the configuration
        guide](docs/config.md) for details.

    The joined lines are separated by newlines, so a match can be traced
    back to the line it is on.
    """
    lines = body.split('\n')
    out: list[tuple[int, str]] = []
    fence: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        fence, is_fence_line = track_fence(fence, line)
        if is_fence_line or fence is not None:
            index += 1
            continue
        start = index
        if is_markdown:
            while (_opens_link_text('\n'.join(lines[start:index + 1]))
                   and index + 1 < len(lines)
                   and index + 1 - start < _MAX_WRAPPED_LINES
                   and lines[index + 1].strip()
                   and not track_fence(None, lines[index + 1])[1]):
                index += 1
        out.append((start + 1, '\n'.join(lines[start:index + 1])))
        index += 1
    return out


def _scan_logical(lineno: int, text: str, allowed: frozenset) -> list[tuple[int, str, str]]:
    """(line number, kind, raw target) for everything `_scan_line` finds in `text`.

    `text` may be several joined lines. Each target is given the line of its
    first unused occurrence, so a link on the second line of a paragraph is
    reported there and not on the first.
    """
    if '\n' not in text:
        return [(lineno, kind, raw) for kind, raw in _scan_line(text, allowed)]
    joined = text.replace('\n', ' ')
    starts = [0]
    for part in text.split('\n')[:-1]:
        starts.append(starts[-1] + len(part) + 1)
    seen: dict[str, int] = {}
    out = []
    for kind, raw in _scan_line(joined, allowed):
        at = -1
        for _ in range(seen.get(raw, 0) + 1):
            at = joined.find(raw, at + 1)
        seen[raw] = seen.get(raw, 0) + 1
        offset = max(at, 0)
        line_index = sum(1 for begin in starts[1:] if begin <= offset)
        out.append((lineno + line_index, kind, raw))
    return out


def build_graph(repo_root: str, inventory: dict) -> dict:
    """Build the reference graph for an inventory."""
    if 'repo_root' not in inventory:  # a hand-built inventory, as tests make
        inventory = {**inventory, 'repo_root': os.path.abspath(repo_root)}
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
        body = textcache.read(inventory, path, strict=False)
        if body is None:
            continue

        if path.lower().endswith(('.md', '.markdown')):
            # Every anchor a link can land on: heading ids and HTML ids.
            headings[path] = anchor_ids(body)

        allowed = _kinds_for(path)

        is_markdown = path.lower().endswith(('.md', '.markdown'))
        for lineno, line in _logical_lines(body, is_markdown):
            for found_at, kind, raw in _scan_logical(lineno, line, allowed):
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
                    options = {'allow_root_fallback': kind == 'path_literal',
                               'js_specifier': kind == 'import'}
                    dst = _resolve(path, decoded, known, **options)
                    if dst is None and decoded != path_part:
                        dst = _resolve(path, path_part, known, **options)
                edges.append({
                    'src': path, 'line': found_at, 'kind': kind,
                    'raw': raw, 'dst': dst, 'anchor': anchor,
                })

        if path.lower().endswith('.py'):
            for lineno, dots, module in _python_imports(body):
                dst = _resolve_py_module(path, dots, module, known)
                if dst is not None:
                    edges.append({'src': path, 'line': lineno, 'kind': 'import',
                                  'raw': f'{dots}{module}', 'dst': dst,
                                  'anchor': None})

    # A symlink is not read (discover leaves it out of `files`), but a link to
    # it lands on its target: the target's anchors are its anchors, and a link
    # to the symlink keeps the target from reading as an orphan.
    symlinks = inventory.get('symlinks', {})
    for link, target in symlinks.items():
        if target in headings:
            headings[link] = headings[target]

    inbound: dict[str, list[str]] = {p: [] for p in known}
    for edge in edges:
        dst = edge['dst']
        if dst is None or dst == edge['src']:
            continue
        for landed in (dst, symlinks.get(dst)):
            if (landed and landed != edge['src'] and landed in inbound
                    and edge['src'] not in inbound[landed]):
                inbound[landed].append(edge['src'])

    return {'edges': edges, 'inbound': inbound, 'headings': headings}
