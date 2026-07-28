#!/usr/bin/env python3
"""Tests for store.py — fingerprints, decisions ledger, cache paths."""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from store import (  # noqa: E402
    append_decision, fingerprint, load_decisions, make_finding,
)


class TestFingerprint(unittest.TestCase):
    def test_has_sha256_prefix_and_64_hex(self):
        fp = fingerprint('broken_link', ['a.md'], 'a.md -> gone.md')
        self.assertTrue(fp.startswith('sha256:'))
        self.assertEqual(len(fp), len('sha256:') + 64)

    def test_is_deterministic(self):
        a = fingerprint('broken_link', ['a.md'], 'x')
        b = fingerprint('broken_link', ['a.md'], 'x')
        self.assertEqual(a, b)

    def test_file_order_does_not_matter(self):
        a = fingerprint('contradiction', ['a.md', 'b.md'], 'x')
        b = fingerprint('contradiction', ['b.md', 'a.md'], 'x')
        self.assertEqual(a, b)

    def test_whitespace_and_case_are_normalised(self):
        a = fingerprint('staleness', ['a.md'], 'Timeout   is 30s')
        b = fingerprint('staleness', ['a.md'], 'timeout is 30s')
        self.assertEqual(a, b)

    def test_different_category_gives_different_id(self):
        a = fingerprint('broken_link', ['a.md'], 'x')
        b = fingerprint('orphan', ['a.md'], 'x')
        self.assertNotEqual(a, b)

    def test_different_claim_gives_different_id(self):
        a = fingerprint('staleness', ['a.md'], 'timeout is 30s')
        b = fingerprint('staleness', ['a.md'], 'timeout is 60s')
        self.assertNotEqual(a, b)


class TestMakeFinding(unittest.TestCase):
    def test_produces_every_required_key(self):
        f = make_finding(
            source='graph', category='broken_link', problem='p',
            evidence=[{'file': 'a.md', 'line': 1, 'quote': 'q'}],
            suggestion='s', severity='high',
        )
        self.assertEqual(set(f), {
            'id', 'source', 'category', 'problem', 'evidence', 'suggestion',
            'fix', 'blast_radius', 'severity', 'confidence', 'ssot_direction',
        })

    def test_phase_one_defaults(self):
        f = make_finding(
            source='graph', category='orphan', problem='p',
            evidence=[{'file': 'a.md', 'line': 1, 'quote': 'q'}],
            suggestion='s', severity='low',
        )
        self.assertEqual(f['fix'], {'kind': 'none'})
        self.assertEqual(f['confidence'], 'high')
        self.assertEqual(f['ssot_direction'], 'n/a')
        self.assertEqual(f['blast_radius'], [])

    def test_id_ignores_line_numbers(self):
        # THE load-bearing invariant: reformatting must not change identity.
        a = make_finding(
            source='graph', category='broken_link', problem='p',
            evidence=[{'file': 'a.md', 'line': 10, 'quote': 'q'}],
            suggestion='s', severity='high', claim='a.md -> gone.md',
        )
        b = make_finding(
            source='graph', category='broken_link', problem='p',
            evidence=[{'file': 'a.md', 'line': 250, 'quote': 'q'}],
            suggestion='s', severity='high', claim='a.md -> gone.md',
        )
        self.assertEqual(a['id'], b['id'])

    def test_substantive_change_gives_new_id(self):
        a = make_finding(
            source='graph', category='broken_link', problem='p',
            evidence=[{'file': 'a.md', 'line': 1, 'quote': 'q'}],
            suggestion='s', severity='high', claim='a.md -> gone.md',
        )
        b = make_finding(
            source='graph', category='broken_link', problem='p',
            evidence=[{'file': 'a.md', 'line': 1, 'quote': 'q'}],
            suggestion='s', severity='high', claim='a.md -> other.md',
        )
        self.assertNotEqual(a['id'], b['id'])

    def test_claim_defaults_to_problem(self):
        f = make_finding(
            source='graph', category='orphan', problem='unique problem text',
            evidence=[{'file': 'a.md', 'line': 1, 'quote': 'q'}],
            suggestion='s', severity='low',
        )
        self.assertEqual(f['id'], fingerprint('orphan', ['a.md'], 'unique problem text'))


class TestDecisions(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_missing_ledger_returns_empty(self):
        self.assertEqual(load_decisions(self.repo), {})

    def test_append_then_load_round_trip(self):
        append_decision(self.repo, {
            'id': 'sha256:abc', 'verdict': 'intentional',
            'reason': 'deliberate', 'at': '2026-07-28', 'summary': 's',
        })
        decisions = load_decisions(self.repo)
        self.assertEqual(decisions['sha256:abc']['verdict'], 'intentional')

    def test_ledger_is_append_only_one_line_per_decision(self):
        append_decision(self.repo, {'id': 'sha256:a', 'verdict': 'intentional'})
        append_decision(self.repo, {'id': 'sha256:b', 'verdict': 'wontfix'})
        path = os.path.join(self.repo, '.dovetail', 'decisions.jsonl')
        with open(path, encoding='utf-8') as fh:
            lines = [ln for ln in fh.read().split('\n') if ln]
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0])['id'], 'sha256:a')

    def test_later_decision_wins_for_same_id(self):
        append_decision(self.repo, {'id': 'sha256:a', 'verdict': 'intentional'})
        append_decision(self.repo, {'id': 'sha256:a', 'verdict': 'wontfix'})
        self.assertEqual(load_decisions(self.repo)['sha256:a']['verdict'], 'wontfix')

    def test_malformed_line_is_skipped_not_fatal(self):
        os.makedirs(os.path.join(self.repo, '.dovetail'), exist_ok=True)
        path = os.path.join(self.repo, '.dovetail', 'decisions.jsonl')
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('not json\n{"id":"sha256:ok","verdict":"intentional"}\n')
        self.assertEqual(set(load_decisions(self.repo)), {'sha256:ok'})


if __name__ == '__main__':
    unittest.main()
