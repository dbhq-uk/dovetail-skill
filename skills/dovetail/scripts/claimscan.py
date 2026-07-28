#!/usr/bin/env python3
"""
Candidate clustering, so contradiction detection is tractable.

Naively, finding contradictions means comparing every claim in a repository
against every other claim. That is quadratic in the corpus and would mean
reading the whole thing twice through a model - the reason upkeep has no
document-versus-document contradiction check at all: each of its reviewers is
handed a disjoint file list and told to review only those, so no reviewer ever
holds two documents at once.

The fix is to do the expensive narrowing in Python. Group candidate spans by a
shared *entity* - a flag, a path, a number with a unit, a command, a version -
and hand the adjudicating reviewer a handful of tight clusters instead of a
corpus. Extraction is mechanical and free; adjudication is where judgement is
actually needed, and it now sees a few candidate pairs rather than everything.

This module decides nothing. It never emits a finding. A cluster is a question
for Layer 2, and two spans mentioning the same entity are very often in
complete agreement.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict

# Entities worth grouping on: each is a token whose value is a fact a document
# can be wrong about. Prose nouns are deliberately excluded - grouping on
# "timeout" rather than "30s" produces clusters too loose to be worth reading.
_ENTITY_PATTERNS = [
    ('flag', re.compile(r'(?<![\w-])(--[A-Za-z][A-Za-z0-9_-]*)')),
    ('quantity', re.compile(
        r'(?<![\w.])(\d+(?:\.\d+)?)\s?'
        r'(ms|s|sec|secs|seconds?|m|min|mins|minutes?|h|hrs?|hours?|d|days?|'
        r'[KMGT]?B|%|px|rem)(?![\w])')),
    ('path', re.compile(r'`([\w.@-]+(?:/[\w.@-]+)+)`')),
    ('env', re.compile(r'(?<![\w])([A-Z][A-Z0-9]{2,}(?:_[A-Z0-9]+)+)(?![\w])')),
    ('port', re.compile(r'(?<![\w.:])(?:port\s+|:)(\d{2,5})(?![\w.])', re.I)),
]

# A span longer than this is a paragraph, not a claim; quoting it back adds
# tokens without adding evidence.
MAX_SPAN_CHARS = 300
# One entity appearing in a great many places is structural vocabulary, not a
# fact under dispute. Clustering on it produces a cluster nobody can read.
MAX_CLUSTER_SPANS = 12
MIN_CLUSTER_FILES = 2

_SENTENCE = re.compile(r'(?<=[.!?])\s+|\n')
_FENCE = re.compile(r'^```.*?^```', re.S | re.M)


def _read(repo_root: str, path: str) -> str | None:
    try:
        with open(os.path.join(repo_root, path), encoding='utf-8') as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _spans(text: str) -> list[tuple[int, str]]:
    """(line number, sentence) for each prose sentence, code blocks removed."""
    stripped = _FENCE.sub(lambda m: '\n' * m.group(0).count('\n'), text)
    out: list[tuple[int, str]] = []
    line = 1
    for chunk in _SENTENCE.split(stripped):
        if chunk is None:
            continue
        piece = chunk.strip()
        if piece and len(piece) <= MAX_SPAN_CHARS:
            out.append((line, piece))
        line += chunk.count('\n') + (1 if '\n' not in chunk else 0)
    return out


# Unit aliases, so "30 seconds", "30 secs" and "30s" land in one bucket.
_UNIT_ALIASES = {
    'ms': 'ms',
    's': 's', 'sec': 's', 'secs': 's', 'second': 's', 'seconds': 's',
    'm': 'min', 'min': 'min', 'mins': 'min', 'minute': 'min', 'minutes': 'min',
    'h': 'h', 'hr': 'h', 'hrs': 'h', 'hour': 'h', 'hours': 'h',
    'd': 'd', 'day': 'd', 'days': 'd',
}


def _entities(span: str) -> set[tuple[str, str]]:
    """Entities a span mentions, as (kind, key).

    The key is what spans are grouped *by*, and for quantities it is the unit
    rather than the number. Keying on the number was the original mistake: it
    grouped spans that agree and separated the ones that disagree, which is
    exactly backwards for finding contradictions. "30 seconds" and "60 seconds"
    have to meet in one cluster for anyone to notice they conflict.

    For a flag, path, env var or port the value *is* the subject, so those key
    on the value as before.
    """
    found: set[tuple[str, str]] = set()
    for kind, pattern in _ENTITY_PATTERNS:
        for match in pattern.finditer(span):
            groups = [g for g in match.groups() if g]
            if not groups:
                continue
            if kind == 'quantity':
                unit = groups[-1].lower()
                found.add((kind, _UNIT_ALIASES.get(unit, unit)))
            else:
                found.add((kind, groups[0].lower()))
    return found


def build_clusters(inventory: dict, graph: dict) -> list[dict]:
    """Candidate clusters for the contradiction reviewer.

    A cluster is an entity plus every span mentioning it, and only survives if
    those spans live in at least two different files: a document repeating its
    own flag name is not a candidate contradiction with itself.
    """
    repo_root = inventory['repo_root']
    by_entity: dict[tuple[str, str], list[dict]] = defaultdict(list)

    for entry in inventory['files']:
        path = entry['path']
        if not path.lower().endswith(('.md', '.markdown')):
            continue
        text = _read(repo_root, path)
        if text is None:
            continue
        for line, span in _spans(text):
            for entity in _entities(span):
                by_entity[entity].append(
                    {'file': path, 'line': line, 'quote': span})

    clusters: list[dict] = []
    for (kind, value), spans in sorted(by_entity.items()):
        files = {span['file'] for span in spans}
        if len(files) < MIN_CLUSTER_FILES:
            continue
        if len(spans) > MAX_CLUSTER_SPANS:
            continue  # structural vocabulary, not a disputed fact
        clusters.append({
            'entity_kind': kind,
            'entity': value,
            'spans': sorted(spans, key=lambda s: (s['file'], s['line'])),
        })

    # Fewest spans first: a two-span cluster is the cheapest to adjudicate and
    # the most likely to be a genuine head-to-head disagreement.
    clusters.sort(key=lambda c: (len(c['spans']), c['entity_kind'], c['entity']))
    return clusters
