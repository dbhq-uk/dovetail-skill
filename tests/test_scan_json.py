#!/usr/bin/env python3
"""Tests for scan.py JSON output and decision suppression."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from scan import run_scan  # noqa: E402
from store import append_decision  # noqa: E402

SCRIPT = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'scan.py')


def git(repo: str, *args: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@e',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@e')
    subprocess.run(['git', *args], cwd=repo, env=env, check=True, capture_output=True)


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


class ScanCase(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, 'init', '-q', '-b', 'main')
        write(self.repo, 'README.md', 'See [missing](docs/gone.md)\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'init')

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)


class TestRunScan(ScanCase):
    def test_finds_the_broken_link(self):
        result = run_scan(self.repo)
        categories = [f['category'] for f in result['findings']]
        self.assertIn('broken_link', categories)

    def test_result_has_every_required_key(self):
        result = run_scan(self.repo)
        self.assertEqual(set(result),
                         {'findings', 'suppressed', 'counts', 'failed_checks'})

    def test_counts_by_severity(self):
        result = run_scan(self.repo)
        self.assertEqual(set(result['counts']), {'high', 'medium', 'low'})
        self.assertGreaterEqual(result['counts']['high'], 1)

    def test_findings_are_sorted_high_severity_first(self):
        write(self.repo, 'docs/stray.md', 'orphan\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'more')
        order = {'high': 0, 'medium': 1, 'low': 2}
        ranks = [order[f['severity']] for f in run_scan(self.repo)['findings']]
        self.assertEqual(ranks, sorted(ranks))

    def test_decision_suppresses_a_finding(self):
        first = run_scan(self.repo)
        target = next(f for f in first['findings'] if f['category'] == 'broken_link')
        append_decision(self.repo, {
            'id': target['id'], 'verdict': 'intentional',
            'reason': 'placeholder link', 'at': '2026-07-28', 'summary': 'x',
        })
        second = run_scan(self.repo)
        self.assertNotIn(target['id'], [f['id'] for f in second['findings']])
        self.assertEqual(second['suppressed'], 1)

    def test_a_failing_check_does_not_stop_the_run(self):
        import graphcheck

        def exploding(inventory, graph):
            raise RuntimeError('boom')

        original = list(graphcheck.ALL_CHECKS)
        graphcheck.ALL_CHECKS.append(exploding)
        try:
            result = run_scan(self.repo)
        finally:
            graphcheck.ALL_CHECKS[:] = original
        self.assertIn('exploding', result['failed_checks'])
        self.assertTrue(result['findings'])

    def test_non_git_directory_is_rejected(self):
        plain = tempfile.mkdtemp()
        try:
            with self.assertRaises(ValueError):
                run_scan(plain)
        finally:
            shutil.rmtree(plain, ignore_errors=True)


class TestSinceFiltering(ScanCase):
    def test_since_keeps_only_findings_touching_changed_files(self):
        base = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=self.repo,
                              capture_output=True, text=True).stdout.strip()
        write(self.repo, 'docs/new.md', 'See [also-missing](nope.md)\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'add new')

        scoped = run_scan(self.repo, since=base)
        files = {e['file'] for f in scoped['findings'] for e in f['evidence']}
        self.assertIn('docs/new.md', files)
        self.assertNotIn('README.md', files)

    def test_without_since_everything_is_reported(self):
        write(self.repo, 'docs/new.md', 'See [also-missing](nope.md)\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'add new')
        files = {e['file'] for f in run_scan(self.repo)['findings']
                 for e in f['evidence']}
        self.assertIn('README.md', files)


class TestCli(ScanCase):
    def test_json_format_emits_parseable_output(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, self.repo, '--format', 'json'],
            capture_output=True, text=True,
        )
        payload = json.loads(proc.stdout)
        self.assertIn('findings', payload)

    def test_missing_path_exits_two(self):
        proc = subprocess.run(
            [sys.executable, SCRIPT, '/no/such/path', '--format', 'json'],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2)


if __name__ == '__main__':
    unittest.main()
