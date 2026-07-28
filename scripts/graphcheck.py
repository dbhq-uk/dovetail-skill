#!/usr/bin/env python3
"""
Findings derived from the reference graph and the inventory.

Everything here is exact: it is computed from resolved edges and content
hashes, never inferred. Task 10 adds the duplicate, orphan, asset, and
translation checks to this module.
"""

from __future__ import annotations

import difflib
import os
import posixpath
import re

from store import make_finding

# Kinds strong enough to call a broken link. A `path_literal` is a plausible
# path spotted in prose; treating one as broken produces false positives, so
# those are collected as evidence elsewhere but never reported as broken.
LINK_KINDS = {'md_link', 'md_image', 'md_refdef', 'html', 'import'}


def broken_links(inventory: dict, graph: dict) -> list[dict]:
    """Links whose target does not exist in the repository."""
    grouped: dict[tuple[str, str], list[dict]] = {}
    for edge in graph['edges']:
        if edge['kind'] not in LINK_KINDS or edge['dst'] is not None:
            continue
        grouped.setdefault((edge['src'], edge['raw']), []).append(edge)

    findings = []
    for (src, raw), edges in sorted(grouped.items()):
        evidence = [
            {'file': src, 'line': e['line'], 'quote': f'link target: {raw}'}
            for e in sorted(edges, key=lambda e: e['line'])
        ]
        findings.append(make_finding(
            source='graph',
            category='broken_link',
            problem=f'{src} links to {raw}, which does not exist.',
            evidence=evidence,
            suggestion=f'Update or remove the link to {raw}.',
            severity='high',
            claim=f'{src} -> {raw}',
        ))
    return findings


def dangling_anchors(inventory: dict, graph: dict) -> list[dict]:
    """Links to a heading anchor that the target document does not define."""
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for edge in graph['edges']:
        if edge['kind'] not in LINK_KINDS:
            continue
        dst, anchor = edge['dst'], edge['anchor']
        if dst is None or not anchor:
            continue
        if dst not in graph['headings']:
            continue  # not a markdown file — no anchors to check against
        if anchor in graph['headings'][dst]:
            continue
        grouped.setdefault((edge['src'], dst, anchor), []).append(edge)

    findings = []
    for (src, dst, anchor), edges in sorted(grouped.items()):
        available = graph['headings'][dst]
        shown = ', '.join(available[:5]) if available else '(none)'
        evidence = [
            {'file': src, 'line': e['line'], 'quote': f'anchor: #{anchor}'}
            for e in sorted(edges, key=lambda e: e['line'])
        ]
        findings.append(make_finding(
            source='graph',
            category='dangling_anchor',
            problem=f'{src} links to #{anchor} in {dst}, which has no such heading.',
            evidence=evidence,
            suggestion=f'Anchors available in {dst}: {shown}',
            severity='medium',
            claim=f'{src} -> {dst}#{anchor}',
        ))
    return findings


# Files that are legitimately unreferenced: entry points, legal and meta
# documents, dotfiles, and CI configuration. Flagging these as orphans is the
# fastest way to make an orphan check useless.
ENTRY_POINT_NAMES = {
    'readme', 'license', 'licence', 'changelog', 'contributing',
    'security', 'code_of_conduct', 'notice', 'authors', 'codeowners',
}
# Nothing imports a test — the runner discovers them — so a test file with no
# inbound references is normal, not an orphan. This is deliberately narrow:
# `spec/` and `specs/` commonly hold specification *documents*, not tests, so
# they are not exempted here — only basename patterns like `.spec.ts` are.
TEST_DIR_NAMES = frozenset({'test', 'tests', '__tests__'})
NEAR_DUPLICATE_MIN_BYTES = 200
LOCALE_DIR = re.compile(r'^docs/([a-z]{2}(?:-[A-Za-z]{2,4})?)/(.+)$')
BASE_LOCALE = 'en'


def _is_entry_point(path: str) -> bool:
    """True for files that are legitimately referenced by nothing."""
    parts = path.split('/')
    if any(part.startswith('.') for part in parts):
        return True  # dotfiles and .github/, .dovetail/, etc.
    if any(part in TEST_DIR_NAMES for part in parts[:-1]):
        return True
    basename = parts[-1]
    if basename.startswith('test_') or basename.startswith('test.'):
        return True
    stem = posixpath.splitext(basename)[0]
    if stem.endswith('_test') or stem.endswith('.test') or stem.endswith('.spec'):
        return True
    if len(parts) == 1:
        stem = posixpath.splitext(parts[0])[0].lower()
        if stem in ENTRY_POINT_NAMES:
            return True
    return False


def orphans(inventory: dict, graph: dict) -> list[dict]:
    """Files with no inbound references that are not legitimate entry points."""
    inbound = graph['inbound']
    findings = []
    for entry in inventory['files']:
        path = entry['path']
        if _is_entry_point(path) or inbound.get(path):
            continue
        findings.append(make_finding(
            source='graph',
            category='orphan',
            problem=f'{path} is not referenced by any file.',
            evidence=[{'file': path, 'line': 1, 'quote': 'no inbound references'}],
            suggestion='Confirm this file is still needed, or link it from somewhere.',
            severity='low',
            claim=f'orphan:{path}',
        ))
    return findings


def exact_duplicates(inventory: dict, graph: dict) -> list[dict]:
    """Groups of files with byte-identical content."""
    by_hash: dict[str, list[str]] = {}
    for entry in inventory['files']:
        if entry['size_bytes'] == 0:
            continue
        by_hash.setdefault(entry['sha256'], []).append(entry['path'])

    findings = []
    for digest, paths in sorted(by_hash.items()):
        if len(paths) < 2:
            continue
        paths = sorted(paths)
        findings.append(make_finding(
            source='graph',
            category='duplicate',
            problem=f'{len(paths)} files have identical content: {", ".join(paths)}.',
            evidence=[{'file': p, 'line': 1, 'quote': f'sha256 {digest[:12]}'}
                      for p in paths],
            suggestion='Keep one copy and reference it from the others.',
            severity='medium',
            claim='duplicate:' + ','.join(paths),
        ))
    return findings


def near_duplicates(inventory: dict, graph: dict, threshold: float = 0.95) -> list[dict]:
    """Text files that are nearly, but not exactly, identical."""
    root = inventory['repo_root']
    candidates = [
        e for e in inventory['files']
        if e['modality'] == 'text' and e['size_bytes'] >= NEAR_DUPLICATE_MIN_BYTES
    ]

    bodies: dict[str, str] = {}
    for entry in candidates:
        try:
            with open(os.path.join(root, entry['path']), encoding='utf-8',
                      errors='replace') as fh:
                bodies[entry['path']] = ' '.join(fh.read().split()).lower()
        except OSError:
            continue

    seen_hashes = {e['path']: e['sha256'] for e in candidates}

    # `real_quick_ratio() >= threshold` is algebraically `r >= t/(2-t)` where
    # r is the length ratio. Checking it directly costs O(1) and avoids
    # building a matcher for hopeless pairs. Note t/(2-t), NOT t: requiring
    # r >= t is strictly tighter than difflib's own bound and silently
    # discards genuine near-duplicates.
    min_length_ratio = threshold / (2 - threshold)

    findings = []
    paths = sorted(bodies)
    matcher = difflib.SequenceMatcher(None)
    for i, left in enumerate(paths):
        # set_seq2 is the cached side in difflib, so it belongs in the outer
        # loop: the index over `left` is built once and reused for every right.
        matcher.set_seq2(bodies[left])
        for right in paths[i + 1:]:
            if seen_hashes[left] == seen_hashes[right]:
                continue  # exact_duplicates owns this pair

            a, b = bodies[left], bodies[right]
            shorter, longer = sorted((len(a), len(b)))
            if longer == 0 or shorter / longer < min_length_ratio:
                continue

            matcher.set_seq1(b)
            if matcher.quick_ratio() < threshold:
                continue
            ratio = matcher.ratio()
            if ratio < threshold:
                continue
            percent = round(ratio * 100)
            findings.append(make_finding(
                source='graph',
                category='near_duplicate',
                problem=f'{left} and {right} are {percent}% identical.',
                evidence=[{'file': left, 'line': 1, 'quote': f'{percent}% similar'},
                          {'file': right, 'line': 1, 'quote': f'{percent}% similar'}],
                suggestion='Merge them, or make the difference explicit.',
                severity='low',
                claim=f'near_duplicate:{left},{right}',
            ))
    return findings


def translation_lag(inventory: dict, graph: dict) -> list[dict]:
    """Translated documents whose last commit predates their base document.

    Layout convention: multilingual docs live at `docs/<locale>/<name>`, with
    the base at `docs/en/<name>`. The one exception is the repository-root
    `README.md`, which is the English base for `docs/<locale>/README.md`.
    """
    times = {e['path']: e['last_commit_iso'] for e in inventory['files']}
    findings = []

    for path, when in sorted(times.items()):
        match = LOCALE_DIR.match(path)
        if not match:
            continue
        locale, name = match.group(1), match.group(2)
        if locale == BASE_LOCALE:
            continue

        base = f'docs/{BASE_LOCALE}/{name}'
        if base not in times and name == 'README.md':
            base = 'README.md'
        if base not in times:
            continue

        base_when = times[base]
        if when is None or base_when is None or when >= base_when:
            continue

        findings.append(make_finding(
            source='graph',
            category='staleness',
            problem=f'{path} was last updated before {base}.',
            evidence=[{'file': base, 'line': 1, 'quote': f'last commit {base_when}'},
                      {'file': path, 'line': 1, 'quote': f'last commit {when}'}],
            suggestion=f'Check {path} against {base} and update it if it has drifted.',
            severity='medium',
            claim=f'translation_lag:{base},{path}',
        ))
    return findings


ALL_CHECKS = [
    broken_links,
    dangling_anchors,
    orphans,
    exact_duplicates,
    near_duplicates,
    translation_lag,
]
