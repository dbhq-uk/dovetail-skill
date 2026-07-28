#!/usr/bin/env python3
"""
Findings derived from the reference graph and the inventory.

Everything here is exact: it is computed from resolved edges and content
hashes, never inferred. Task 10 adds the duplicate, orphan, asset, and
translation checks to this module.
"""

from __future__ import annotations

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
