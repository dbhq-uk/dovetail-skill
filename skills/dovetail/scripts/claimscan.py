#!/usr/bin/env python3
"""
Candidate clustering, so contradiction detection is tractable.

Naively, finding contradictions means comparing every claim in a repository
against every other claim. That is quadratic in the corpus and would mean
reading the whole thing twice through a model - the reason the prior tool has no
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
    # A number followed by a noun that can carry a threshold. Unit-bearing
    # quantities above only recognise ms/s/%/B and so on, which misses the
    # common documentation case: "20,000 words" in one file and "18,000 words"
    # in another is a contradiction and neither carries a unit. Found by
    # running head-to-head against the prior tool, which caught it while dovetail
    # did not.
    #
    # The noun list is curated rather than "any word". Matching any word was
    # tried and abandoned: it took one repository from 12 clusters to 238,
    # keyed on things like "2 above" and "3 complete". Every cluster costs the
    # adjudicating model real tokens, so a loose key is not free.
    ('measure', re.compile(
        r'(?<![\w.])(\d[\d,]*)\s+('
        r'words?|tokens?|characters?|chars?|lines?|pages?|'
        r'retries|retry|attempts?|iterations?|rounds?|'
        r'sources?|results?|items?|entries|records?|rows?|'
        r'requests?|calls?|queries|files?|steps?'
        r')(?![\w])')),
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
            elif kind == 'measure':
                # Crude singularisation is enough: the key only has to be
                # stable across spans, not linguistically correct.
                noun = groups[-1].lower().rstrip('s') or groups[-1].lower()
                found.add((kind, noun))
            else:
                found.add((kind, groups[0].lower()))
    return found


_NUMERIC_KINDS = frozenset({'measure', 'quantity'})
_NUMBERS = re.compile(r'(?<![\w.])(\d[\d,]*(?:\.\d+)?)')


def _values_disagree(kind: str, key: str, spans: list[dict]) -> bool:
    """Whether the numbers attached to this entity differ across spans.

    A cluster of spans that all state the same figure is agreement, and paying
    a model to read it produces nothing. Ranges ("2-3 paragraphs") contribute
    every number they contain, so a range that overlaps another span's figure
    still counts as agreement rather than a spurious conflict.
    """
    seen: set[str] = set()
    for span in spans:
        for raw in _NUMBERS.findall(span['quote']):
            seen.add(raw.replace(',', '').lstrip('0') or '0')
    return len(seen) > 1


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
        if kind in _NUMERIC_KINDS and not _values_disagree(kind, value, spans):
            # Every span agrees on the number, so there is nothing to
            # adjudicate. Without this, counting nouns in prose - "2-3
            # paragraphs" repeated in five templates - flood the adjudicator
            # with clusters that are agreement by construction.
            continue
        clusters.append({
            'entity_kind': kind,
            'entity': value,
            'spans': sorted(spans, key=lambda s: (s['file'], s['line'])),
        })

    # Fewest spans first: a two-span cluster is the cheapest to adjudicate and
    # the most likely to be a genuine head-to-head disagreement.
    clusters.sort(key=lambda c: (len(c['spans']), c['entity_kind'], c['entity']))
    return clusters
