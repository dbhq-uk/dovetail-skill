"""Tests for the convention checks."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from convcheck import (  # noqa: E402
    scripts_are_executable, shell_scripts_exit_on_error, skill_frontmatter,
)
from discover import discover  # noqa: E402
from refgraph import build_graph  # noqa: E402


def write(repo: str, rel: str, body: str, executable: bool = False) -> None:
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(body)
    if executable:
        os.chmod(path, 0o755)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = self._tmp.name
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True)
        for key, value in (('gc.auto', '0'), ('maintenance.auto', 'false')):
            subprocess.run(['git', '-C', self.repo, 'config', key, value],
                           check=True, capture_output=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def scan(self):
        inventory = discover(self.repo)
        return inventory, build_graph(self.repo, inventory)


class SetE(Base):
    def test_missing_set_e_is_found(self):
        write(self.repo, 'run.sh', '#!/bin/bash\necho hi\n', executable=True)
        found = shell_scripts_exit_on_error(*self.scan())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'convention')

    def test_set_e_is_silent(self):
        write(self.repo, 'run.sh', '#!/bin/bash\nset -e\necho hi\n', executable=True)
        self.assertEqual(shell_scripts_exit_on_error(*self.scan()), [])

    def test_set_euo_pipefail_counts(self):
        write(self.repo, 'run.sh', '#!/bin/bash\nset -euo pipefail\n', executable=True)
        self.assertEqual(shell_scripts_exit_on_error(*self.scan()), [])

    def test_set_o_errexit_counts(self):
        write(self.repo, 'run.sh', '#!/bin/bash\nset -o errexit\n', executable=True)
        self.assertEqual(shell_scripts_exit_on_error(*self.scan()), [])

    def test_sourced_fragment_is_skipped(self):
        # No shebang and not executable: meant to be sourced, and `set -e`
        # would wrongly change the caller's shell.
        write(self.repo, 'lib.sh', 'export FOO=1\n')
        self.assertEqual(shell_scripts_exit_on_error(*self.scan()), [])


class Executable(Base):
    def test_shebang_without_bit_is_found(self):
        write(self.repo, 'tool.py', '#!/usr/bin/env python3\nprint(1)\n')
        found = scripts_are_executable(*self.scan())
        self.assertEqual(len(found), 1)

    def test_executable_is_silent(self):
        write(self.repo, 'tool.py', '#!/usr/bin/env python3\n', executable=True)
        self.assertEqual(scripts_are_executable(*self.scan()), [])

    def test_no_shebang_is_silent(self):
        write(self.repo, 'lib.py', 'x = 1\n')
        self.assertEqual(scripts_are_executable(*self.scan()), [])

    def test_test_modules_are_skipped(self):
        # Regression: a test module's shebang is vestigial - the runner imports
        # it. Twelve of fifteen findings on the first real run were test files.
        write(self.repo, 'tests/test_thing.py', '#!/usr/bin/env python3\n')
        write(self.repo, 'test_other.py', '#!/usr/bin/env python3\n')
        self.assertEqual(scripts_are_executable(*self.scan()), [])


class SkillFrontmatter(Base):
    GOOD = '---\nname: thing\ndescription: Does the thing.\n---\n\n# thing\n'

    def test_valid_frontmatter_is_silent(self):
        write(self.repo, 'skills/thing/SKILL.md', self.GOOD)
        self.assertEqual(skill_frontmatter(*self.scan()), [])

    def test_missing_frontmatter_is_high(self):
        write(self.repo, 'skills/thing/SKILL.md', '# thing\n')
        found = skill_frontmatter(*self.scan())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['severity'], 'high')

    def test_missing_description_is_found(self):
        write(self.repo, 'skills/thing/SKILL.md', '---\nname: thing\n---\n')
        found = skill_frontmatter(*self.scan())
        self.assertEqual(len(found), 1)
        self.assertIn('description', found[0]['problem'])

    def test_name_must_match_directory(self):
        write(self.repo, 'skills/thing/SKILL.md',
              '---\nname: other\ndescription: x\n---\n')
        found = skill_frontmatter(*self.scan())
        self.assertEqual(len(found), 1)
        self.assertIn('directory', found[0]['problem'])


if __name__ == '__main__':
    unittest.main()
