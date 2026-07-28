#!/usr/bin/env python3
"""Tests for gitmeta.py. Builds real git repos in temp dirs."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from gitmeta import (  # noqa: E402
    changed_since, is_git_repo, last_commit_times, list_files, rev_exists,
)


def git(repo: str, *args: str) -> str:
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@e',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@e')
    return subprocess.run(['git', *args], cwd=repo, env=env, check=True,
                          capture_output=True, text=True).stdout


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


class GitRepoCase(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, 'init', '-q', '-b', 'main')

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)


class TestIsGitRepo(GitRepoCase):
    def test_true_for_repo(self):
        self.assertTrue(is_git_repo(self.repo))

    def test_false_for_plain_dir(self):
        plain = tempfile.mkdtemp()
        try:
            self.assertFalse(is_git_repo(plain))
        finally:
            shutil.rmtree(plain, ignore_errors=True)


class TestListFiles(GitRepoCase):
    def test_lists_tracked_and_untracked(self):
        write(self.repo, 'a.md', 'a')
        git(self.repo, 'add', 'a.md')
        git(self.repo, 'commit', '-qm', 'a')
        write(self.repo, 'b.md', 'b')
        self.assertEqual(sorted(list_files(self.repo)), ['a.md', 'b.md'])

    def test_respects_gitignore(self):
        write(self.repo, '.gitignore', 'ignored.md\n')
        write(self.repo, 'ignored.md', 'x')
        write(self.repo, 'kept.md', 'y')
        self.assertNotIn('ignored.md', list_files(self.repo))
        self.assertIn('kept.md', list_files(self.repo))

    def test_handles_paths_with_spaces(self):
        write(self.repo, 'has space.md', 'x')
        self.assertIn('has space.md', list_files(self.repo))

    def test_non_repo_directory_returns_empty_not_raises(self):
        plain = tempfile.mkdtemp()
        try:
            self.assertEqual(list_files(plain), [])
        finally:
            shutil.rmtree(plain, ignore_errors=True)


class TestLastCommitTimes(GitRepoCase):
    def test_returns_iso_time_for_committed_file(self):
        write(self.repo, 'a.md', 'a')
        git(self.repo, 'add', 'a.md')
        git(self.repo, 'commit', '-qm', 'a')
        times = last_commit_times(self.repo, ['a.md'])
        self.assertIsNotNone(times['a.md'])
        self.assertRegex(times['a.md'], r'^\d{4}-\d{2}-\d{2}T')

    def test_returns_none_for_uncommitted_file(self):
        write(self.repo, 'new.md', 'x')
        self.assertIsNone(last_commit_times(self.repo, ['new.md'])['new.md'])

    def test_first_occurrence_wins_is_most_recent(self):
        write(self.repo, 'a.md', 'v1')
        git(self.repo, 'add', 'a.md')
        git(self.repo, 'commit', '-qm', 'first')
        first = last_commit_times(self.repo, ['a.md'])['a.md']
        write(self.repo, 'a.md', 'v2')
        git(self.repo, 'add', 'a.md')
        git(self.repo, 'commit', '-qm', 'second')
        second = last_commit_times(self.repo, ['a.md'])['a.md']
        self.assertGreaterEqual(second, first)

    def test_every_requested_path_is_a_key(self):
        write(self.repo, 'a.md', 'a')
        git(self.repo, 'add', 'a.md')
        git(self.repo, 'commit', '-qm', 'a')
        times = last_commit_times(self.repo, ['a.md', 'missing.md'])
        self.assertEqual(set(times), {'a.md', 'missing.md'})

    def test_empty_paths_returns_empty_dict(self):
        self.assertEqual(last_commit_times(self.repo, []), {})

    def test_missing_directory_returns_all_none_not_raises(self):
        # A nonexistent cwd raises FileNotFoundError, not CalledProcessError.
        with tempfile.TemporaryDirectory() as parent:
            missing = os.path.join(parent, 'nope', 'deeper')
            self.assertEqual(last_commit_times(missing, ['a.md']), {'a.md': None})

    def test_file_as_repo_root_returns_all_none_not_raises(self):
        # A cwd that is a file raises NotADirectoryError.
        handle, path = tempfile.mkstemp()
        os.close(handle)
        try:
            self.assertEqual(last_commit_times(path, ['a.md']), {'a.md': None})
        finally:
            os.unlink(path)

    def test_many_paths_are_chunked_without_overflowing(self):
        for i in range(120):
            write(self.repo, f'f{i:04d}.md', f'content {i}\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'many')
        paths = [f'f{i:04d}.md' for i in range(120)]
        times = last_commit_times(self.repo, paths)
        self.assertEqual(len(times), 120)
        self.assertTrue(all(v is not None for v in times.values()))


class TestChangedSince(GitRepoCase):
    def test_lists_files_changed_since_ref(self):
        write(self.repo, 'a.md', 'a')
        git(self.repo, 'add', 'a.md')
        git(self.repo, 'commit', '-qm', 'base')
        base = git(self.repo, 'rev-parse', 'HEAD').strip()
        write(self.repo, 'b.md', 'b')
        git(self.repo, 'add', 'b.md')
        git(self.repo, 'commit', '-qm', 'next')
        self.assertEqual(changed_since(self.repo, base), {'b.md'})

    def test_unknown_ref_returns_empty_set(self):
        self.assertEqual(changed_since(self.repo, 'no-such-ref'), set())

    def test_missing_directory_returns_empty_not_raises(self):
        with tempfile.TemporaryDirectory() as parent:
            missing = os.path.join(parent, 'nope', 'deeper')
            self.assertEqual(changed_since(missing, 'main'), set())

    def test_file_as_repo_root_returns_empty_not_raises(self):
        handle, path = tempfile.mkstemp()
        os.close(handle)
        try:
            self.assertEqual(changed_since(path, 'main'), set())
        finally:
            os.unlink(path)


class TestRevExists(GitRepoCase):
    def test_true_for_a_real_ref(self):
        write(self.repo, 'a.md', 'a')
        git(self.repo, 'add', 'a.md')
        git(self.repo, 'commit', '-qm', 'a')
        self.assertTrue(rev_exists(self.repo, 'HEAD'))

    def test_false_for_an_unknown_ref(self):
        write(self.repo, 'a.md', 'a')
        git(self.repo, 'add', 'a.md')
        git(self.repo, 'commit', '-qm', 'a')
        self.assertFalse(rev_exists(self.repo, 'no-such-ref'))

    def test_false_in_a_repo_with_no_commits(self):
        self.assertFalse(rev_exists(self.repo, 'HEAD'))


if __name__ == '__main__':
    unittest.main()
