#!/usr/bin/env python3
"""
Finding identity, the decisions ledger, and cache locations.

Fingerprints deliberately exclude line numbers. A finding must keep its
identity when unrelated edits move it down the file, otherwise a decision
recorded once would stop suppressing the finding it was about. Change the
substance and the fingerprint correctly changes with it.

The ledger is append-only JSONL, committed to the repository: single-line git
diffs, no reformatting churn, and two people dismissing different findings do
not conflict.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from typing import Iterable

DECISIONS_REL = os.path.join('.dovetail', 'decisions.jsonl')
_WS = re.compile(r'\s+')


def _normalise(text: str) -> str:
    return _WS.sub(' ', text).strip().lower()


def fingerprint(category: str, files: Iterable[str], claim: str) -> str:
    """Stable content-derived identity for a finding."""
    # json.dumps rather than string concatenation: a delimiter inside a
    # component (a filename containing a comma, say) would otherwise let two
    # genuinely different findings collapse to the same key.
    key = json.dumps([category, sorted(set(files)), _normalise(claim)],
                     sort_keys=True, ensure_ascii=False)
    return 'sha256:' + hashlib.sha256(key.encode('utf-8')).hexdigest()


def make_finding(
    *,
    source: str,
    category: str,
    problem: str,
    evidence: list[dict],
    suggestion: str,
    severity: str,
    blast_radius: list[str] | None = None,
    claim: str | None = None,
) -> dict:
    """Build a Finding with Phase 1 defaults applied."""
    files = [e['file'] for e in evidence]
    return {
        'id': fingerprint(category, files, claim if claim is not None else problem),
        'source': source,
        'category': category,
        'problem': problem,
        'evidence': evidence,
        'suggestion': suggestion,
        'fix': {'kind': 'none'},
        'blast_radius': blast_radius or [],
        'severity': severity,
        'confidence': 'high',
        'ssot_direction': 'n/a',
    }


def load_decisions(repo_root: str) -> dict[str, dict]:
    """Read the ledger, keyed by finding id. Later lines override earlier ones."""
    path = os.path.join(repo_root, DECISIONS_REL)
    if not os.path.exists(path):
        return {}
    decisions: dict[str, dict] = {}
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # a hand-edited bad line must not break the run
            if isinstance(row, dict) and 'id' in row:
                decisions[row['id']] = row
    return decisions


def append_decision(repo_root: str, decision: dict) -> None:
    """Append one decision to the ledger, creating it if needed."""
    path = os.path.join(repo_root, DECISIONS_REL)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(decision, sort_keys=True, ensure_ascii=False) + '\n')
