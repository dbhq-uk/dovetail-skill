#!/usr/bin/env python3
"""Tests for discover.py."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from discover import discover  # noqa: E402


def git(repo: str, *args: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@e',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@e')
    subprocess.run(['git', *args], cwd=repo, env=env, check=True, capture_output=True)


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


class TestDiscover(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()
        git(self.repo, 'init', '-q', '-b', 'main')
        write(self.repo, 'README.md', '# Hello\n')
        write(self.repo, 'src/app.py', 'x = 1\n')
        write(self.repo, 'docs/en/plans/old.md', 'archived\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'init')

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_lists_every_file(self):
        inv = discover(self.repo)
        self.assertEqual(
            sorted(f['path'] for f in inv['files']),
            ['README.md', 'docs/en/plans/old.md', 'src/app.py'],
        )

    def test_entry_has_every_required_key(self):
        inv = discover(self.repo)
        entry = next(f for f in inv['files'] if f['path'] == 'README.md')
        self.assertEqual(
            set(entry),
            {'path', 'modality', 'category', 'size_bytes', 'sha256', 'last_commit_iso'},
        )

    def test_sha256_matches_content(self):
        inv = discover(self.repo)
        entry = next(f for f in inv['files'] if f['path'] == 'README.md')
        self.assertEqual(entry['sha256'], hashlib.sha256(b'# Hello\n').hexdigest())

    def test_size_bytes_matches_content(self):
        inv = discover(self.repo)
        entry = next(f for f in inv['files'] if f['path'] == 'README.md')
        self.assertEqual(entry['size_bytes'], len(b'# Hello\n'))

    def test_classification_is_applied(self):
        inv = discover(self.repo)
        by_path = {f['path']: f for f in inv['files']}
        self.assertEqual(by_path['README.md']['category'], 'doc')
        self.assertEqual(by_path['src/app.py']['category'], 'code')
        self.assertEqual(by_path['README.md']['modality'], 'text')

    def test_last_commit_iso_is_populated(self):
        inv = discover(self.repo)
        entry = next(f for f in inv['files'] if f['path'] == 'README.md')
        self.assertRegex(entry['last_commit_iso'], r'^\d{4}-\d{2}-\d{2}T')

    def test_ignore_globs_drop_files(self):
        inv = discover(self.repo, ignore=['docs/*/plans/**'])
        self.assertNotIn('docs/en/plans/old.md', [f['path'] for f in inv['files']])
        self.assertIn('README.md', [f['path'] for f in inv['files']])

    def test_all_paths_includes_ignored_files(self):
        inv = discover(self.repo, ignore=['docs/*/plans/**'])
        self.assertNotIn('docs/en/plans/old.md', [f['path'] for f in inv['files']])
        self.assertIn('docs/en/plans/old.md', inv['all_paths'])

    def test_files_are_sorted_by_path(self):
        inv = discover(self.repo)
        paths = [f['path'] for f in inv['files']]
        self.assertEqual(paths, sorted(paths))

    def test_repo_root_is_absolute(self):
        inv = discover(self.repo)
        self.assertTrue(os.path.isabs(inv['repo_root']))

    def test_unreadable_entries_are_skipped_not_fatal(self):
        # A broken symlink cannot be read; discovery must not crash.
        os.symlink(os.path.join(self.repo, 'nope'), os.path.join(self.repo, 'dangling'))
        inv = discover(self.repo)
        self.assertNotIn('dangling', [f['path'] for f in inv['files']])


if __name__ == '__main__':
    unittest.main()
