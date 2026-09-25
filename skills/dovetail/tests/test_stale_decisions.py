#!/usr/bin/env python3
"""Decisions that stop matching, and ids that must not carry a line number.

A finding's id hashes its category, its files and its claim. Moving a file
therefore changes the id, and a decision about the old path stops matching.
The scan cannot keep that decision alive, but it must not let the finding come
back in silence either: the old row is listed under `stale_decisions`.

The other half is ids that did carry a line number, so any edit above the
spot re-opened a decision the user had already made.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
REPO_CHECKS = os.path.join(HERE, '..', '..', '..', '.dovetail', 'checks')
sys.path.insert(0, SCRIPTS)

import plugins as plugin_runner  # noqa: E402
from discover import discover  # noqa: E402
from refgraph import build_graph  # noqa: E402
from scan import run_scan  # noqa: E402
from store import append_decision, stale_decisions  # noqa: E402

GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e'}


def git(repo: str, *args: str) -> None:
    subprocess.run(['git', *args], cwd=repo, env=dict(os.environ, **GIT_ENV),
                   check=True, capture_output=True)


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


def commit(repo: str, message: str = 'change') -> None:
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', message)


def decide(repo: str, finding: dict, **extra: str) -> None:
    append_decision(repo, {'id': finding['id'], 'verdict': 'intentional',
                           'reason': 'on purpose', 'at': '2026-09-25',
                           'summary': finding['problem'][:80], **extra})


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp()
        git(self.repo, 'init', '-q', '-b', 'main')
        write(self.repo, 'README.md', '# Project\n\nNothing links anywhere.\n')
        write(self.repo, 'docs/lonely.md', '# Lonely\n\nNobody points here.\n')
        commit(self.repo, 'fixture')

    def tearDown(self) -> None:
        shutil.rmtree(self.repo, ignore_errors=True)

    def only(self, result: dict, category: str) -> dict:
        (finding,) = [f for f in result['findings'] if f['category'] == category]
        return finding


class TestStaleDecisions(Case):
    def test_a_moved_file_lists_its_old_decision_as_stale(self):
        # The reproduction from the review: suppress an orphan, move the
        # file, scan again.
        orphan = self.only(run_scan(self.repo), 'orphan')
        decide(self.repo, orphan)
        before = run_scan(self.repo)
        self.assertEqual(before['suppressed'], 1)
        self.assertEqual(before['stale_decisions'], [])

        os.makedirs(os.path.join(self.repo, 'guides'))
        git(self.repo, 'mv', 'docs/lonely.md', 'guides/lonely.md')
        commit(self.repo, 'move')
        after = run_scan(self.repo)

        self.assertEqual(after['suppressed'], 0)
        self.assertEqual(self.only(after, 'orphan')['evidence'][0]['file'],
                         'guides/lonely.md')
        self.assertEqual([row['id'] for row in after['stale_decisions']], [orphan['id']])
        self.assertEqual(after['stale_decisions'][0]['summary'], orphan['problem'][:80])

    def test_a_row_that_still_matches_is_not_stale(self):
        decide(self.repo, self.only(run_scan(self.repo), 'orphan'))
        self.assertEqual(run_scan(self.repo)['stale_decisions'], [])

    def test_since_does_not_make_a_live_decision_look_stale(self):
        decide(self.repo, self.only(run_scan(self.repo), 'orphan'))
        commit(self.repo, 'ledger')
        write(self.repo, 'README.md', '# Project\n\nEdited, still links nowhere.\n')
        commit(self.repo, 'edit')
        result = run_scan(self.repo, since='HEAD~1')
        self.assertEqual(result['stale_decisions'], [])

    def test_a_judged_row_is_never_reported_stale(self):
        # A scan cannot reproduce a reviewer's finding, so it cannot say one
        # has gone. `decide` marks those rows judged.
        append_decision(self.repo, {'id': 'sha256:judged', 'verdict': 'intentional',
                                    'reason': 'r', 'at': '2026-09-25', 'summary': 's',
                                    'layer': 'judged'})
        append_decision(self.repo, {'id': 'sha256:gone', 'verdict': 'wontfix',
                                    'reason': 'r', 'at': '2026-09-25', 'summary': 's'})
        stale = run_scan(self.repo)['stale_decisions']
        self.assertEqual([row['id'] for row in stale], ['sha256:gone'])

    def test_stale_rows_are_in_a_stable_order(self):
        rows = {'sha256:b': {'id': 'sha256:b', 'at': '2026-09-02'},
                'sha256:a': {'id': 'sha256:a', 'at': '2026-09-02'},
                'sha256:c': {'id': 'sha256:c', 'at': '2026-08-01'}}
        self.assertEqual([r['id'] for r in stale_decisions(rows, set())],
                         ['sha256:c', 'sha256:a', 'sha256:b'])


BROKEN_JSON = '```json\n{"name": "dovetail",\n```\n'


class TestLineFreeIds(Case):
    def test_a_parse_error_decision_survives_lines_added_above(self):
        write(self.repo, 'docs/example.md', f'# Example\n\n{BROKEN_JSON}')
        commit(self.repo)
        decide(self.repo, self.only(run_scan(self.repo), 'parse_error'))
        self.assertEqual(run_scan(self.repo)['suppressed'], 1)

        write(self.repo, 'docs/example.md',
              f'# Example\n\nA new paragraph.\n\nAnd another.\n\n{BROKEN_JSON}')
        commit(self.repo)
        result = run_scan(self.repo)
        self.assertEqual([f for f in result['findings'] if f['category'] == 'parse_error'], [])
        self.assertEqual(result['stale_decisions'], [])

    def test_two_identical_broken_blocks_keep_distinct_ids(self):
        write(self.repo, 'docs/example.md', f'# Example\n\n{BROKEN_JSON}\n{BROKEN_JSON}')
        commit(self.repo)
        ids = [f['id'] for f in run_scan(self.repo)['findings']
               if f['category'] == 'parse_error']
        self.assertEqual(len(ids), 2)
        self.assertEqual(len(set(ids)), 2)

    def test_a_changed_block_is_a_new_finding(self):
        write(self.repo, 'docs/example.md', f'# Example\n\n{BROKEN_JSON}')
        commit(self.repo)
        decide(self.repo, self.only(run_scan(self.repo), 'parse_error'))
        write(self.repo, 'docs/example.md', '# Example\n\n```json\n{"name": 1,\n```\n')
        commit(self.repo)
        self.assertEqual(run_scan(self.repo)['suppressed'], 0)


class TestPluginIds(Case):
    """The worked-example plugins build ids with make_finding, never a line."""

    def plugin_ids(self, name: str) -> list[str]:
        checks = os.path.join(self.repo, '.dovetail', 'checks')
        os.makedirs(checks, exist_ok=True)
        shutil.copy(os.path.join(REPO_CHECKS, f'{name}.py'), checks)
        inventory = discover(self.repo)
        (result,) = plugin_runner.run_plugins(self.repo, inventory,
                                              build_graph(self.repo, inventory))
        self.assertIsNone(result.error)
        return [f['id'] for f in result.findings]

    def test_the_dash_plugin_id_survives_lines_added_above(self):
        write(self.repo, 'docs/style.md', '# Style\n\nOne \u2014 two.\n')
        before = self.plugin_ids('house_style_dashes')
        write(self.repo, 'docs/style.md', '# Style\n\nNew line.\n\nMore.\n\nOne \u2014 two.\n')
        after = self.plugin_ids('house_style_dashes')
        self.assertEqual(len(before), 1)
        self.assertEqual(before, after)
        self.assertTrue(before[0].startswith('sha256:'))

    def test_the_test_count_plugin_id_survives_lines_added_above(self):
        tests = ''.join(f'    def test_{i}(self):\n        pass\n' for i in range(25))
        write(self.repo, 'tests/test_a.py', f'import unittest\n\nclass T(unittest.TestCase):\n{tests}')
        write(self.repo, 'docs/count.md', '# Count\n\nThe suite has 99 tests.\n')
        before = self.plugin_ids('documented_test_count')
        write(self.repo, 'docs/count.md', '# Count\n\nIntro.\n\nThe suite has 99 tests.\n')
        after = self.plugin_ids('documented_test_count')
        self.assertEqual(len(before), 1)
        self.assertEqual(before, after)

    def test_make_finding_imports_even_without_the_scripts_dir_on_the_path(self):
        write(self.repo, '.dovetail/checks/uses_store.py',
              'from store import make_finding\n\n'
              'def check(inventory, graph):\n'
              '    return [make_finding(source="plugin:x", category="convention",\n'
              '        problem="p", evidence=[{"file": "README.md", "line": 1}],\n'
              '        suggestion="s", severity="low", claim="c")]\n')
        saved = list(sys.path)
        store_module = sys.modules.pop('store')
        try:
            sys.path[:] = [p for p in sys.path
                           if os.path.realpath(p) != os.path.realpath(SCRIPTS)]
            inventory = discover(self.repo)
            (result,) = plugin_runner.run_plugins(self.repo, inventory,
                                                  build_graph(self.repo, inventory))
        finally:
            sys.path[:] = saved
            sys.modules['store'] = store_module
        self.assertIsNone(result.error)
        self.assertEqual(len(result.findings), 1)


if __name__ == '__main__':
    unittest.main()
