"""Tests for the git-history signals: co-change coupling and TODO age.

These build real repositories with scripted histories, because the signal being
tested *is* the history - a mocked git log would only assert that the parsing
works, which is the part least likely to be wrong.
"""

from __future__ import annotations

import datetime as _dt
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from cochange import commit_file_sets, decoupled_pairs, stale_todos  # noqa: E402
from discover import discover  # noqa: E402
from refgraph import build_graph  # noqa: E402


class Base(unittest.TestCase):
    def setUp(self) -> None:
        # ignore_cleanup_errors: belt and braces for the same race the gc
        # settings below prevent - teardown must never fail a passing test.
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = self._tmp.name
        self.git('init', '-q')
        self.git('config', 'user.email', 't@example.com')
        self.git('config', 'user.name', 'T')
        # These suites commit repeatedly to build a history. That can trip
        # git's auto-gc, whose background process is still writing into
        # .git/objects when TemporaryDirectory tears the tree down - an
        # OSError that fails a test which actually passed. It reproduced only
        # on CI, and only sometimes, which is the worst kind.
        self.git('config', 'gc.auto', '0')
        self.git('config', 'maintenance.auto', 'false')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def git(self, *args: str) -> None:
        subprocess.run(['git', '-C', self.repo, *args], check=True,
                       capture_output=True)

    def write(self, rel: str, body: str) -> None:
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(body)

    def commit(self, message: str, *, when: str | None = None) -> None:
        self.git('add', '-A')
        env = dict(os.environ)
        if when:
            env['GIT_AUTHOR_DATE'] = when
            env['GIT_COMMITTER_DATE'] = when
        subprocess.run(['git', '-C', self.repo, 'commit', '-q', '-m', message],
                       check=True, capture_output=True, env=env)

    def scan(self):
        inventory = discover(self.repo)
        return inventory, build_graph(self.repo, inventory)


class CommitFileSets(Base):
    def test_reads_one_set_per_commit(self):
        self.write('a.md', '1')
        self.commit('one')
        self.write('b.md', '1')
        self.commit('two')
        sets = commit_file_sets(self.repo)
        self.assertEqual(len(sets), 2)
        self.assertEqual(sets[0], {'b.md'})  # newest first

    def test_no_history_is_empty_not_an_error(self):
        self.assertEqual(commit_file_sets(self.repo), [])


class Decoupling(Base):
    def _coupled_history(self, pairs: int) -> None:
        for i in range(pairs):
            self.write('a.md', f'a{i}')
            self.write('b.md', f'b{i}')
            self.commit(f'together {i}')

    def test_a_long_pairing_then_solo_changes_is_found(self):
        self._coupled_history(6)
        for i in range(4):
            self.write('a.md', f'solo{i}')
            self.commit(f'solo {i}')
        found = decoupled_pairs(*self.scan())
        self.assertEqual([f['category'] for f in found], ['decoupled'])

    def test_still_moving_together_is_silent(self):
        self._coupled_history(8)
        self.assertEqual(decoupled_pairs(*self.scan()), [])

    def test_short_history_is_silent(self):
        self._coupled_history(2)
        for i in range(4):
            self.write('a.md', f'solo{i}')
            self.commit(f'solo {i}')
        self.assertEqual(decoupled_pairs(*self.scan()), [])

    def test_sweeping_commits_do_not_couple_everything(self):
        # A commit touching a large number of files is a refactor, not evidence
        # that all of them belong together.
        for i in range(8):
            for n in range(45):
                self.write(f'f{n}.md', f'{i}')
            self.commit(f'sweep {i}')
        for i in range(4):
            self.write('f0.md', f'solo{i}')
            self.commit(f'solo {i}')
        self.assertEqual(decoupled_pairs(*self.scan()), [])


class StaleTodos(Base):
    def test_old_todo_is_found(self):
        old = (_dt.datetime.now(_dt.timezone.utc)
               - _dt.timedelta(days=400)).strftime('%Y-%m-%dT%H:%M:%S+0000')
        self.write('a.py', '# TODO: sort this out\nx = 1\n')
        self.commit('old', when=old)
        found = stale_todos(*self.scan())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'stale_todo')
        self.assertIn('months', found[0]['problem'])

    def test_recent_todo_is_silent(self):
        self.write('a.py', '# TODO: just added\nx = 1\n')
        self.commit('new')
        self.assertEqual(stale_todos(*self.scan()), [])

    def test_no_marker_is_silent(self):
        self.write('a.py', 'x = 1\n')
        self.commit('clean')
        self.assertEqual(stale_todos(*self.scan()), [])


if __name__ == '__main__':
    unittest.main()
