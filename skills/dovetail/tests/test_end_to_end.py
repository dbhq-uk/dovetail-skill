#!/usr/bin/env python3
"""
End-to-end scan against a synthetic repository with planted defects.

Two properties matter equally: every planted defect is found exactly once, and
the clean files produce nothing. A checker that cries wolf gets switched off.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from scan import run_scan  # noqa: E402


def git(repo: str, *args: str) -> None:
    env = dict(os.environ, GIT_AUTHOR_NAME='t', GIT_AUTHOR_EMAIL='t@e',
               GIT_COMMITTER_NAME='t', GIT_COMMITTER_EMAIL='t@e')
    subprocess.run(['git', *args], cwd=repo, env=env, check=True, capture_output=True)


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


class TestPlantedDefects(unittest.TestCase):
    """One planted defect of each kind, plus clean content that must stay quiet."""

    @classmethod
    def setUpClass(cls):
        cls.repo = tempfile.mkdtemp()
        git(cls.repo, 'init', '-q', '-b', 'main')

        # Clean, correctly cross-linked content — must produce no findings.
        write(cls.repo, 'README.md',
              '# Project\n\n'
              'See [setup](docs/setup.md) and [usage](docs/setup.md#usage).\n')
        write(cls.repo, 'docs/setup.md', '# Setup\n\n## Usage\n\nRun it.\n')

        # DEFECT 1: broken link
        write(cls.repo, 'docs/broken.md', 'See [gone](../nowhere/file.md).\n')

        # DEFECT 2: dangling anchor
        write(cls.repo, 'docs/anchor.md', 'Jump to [nope](setup.md#does-not-exist).\n')

        # DEFECT 3: orphan (referenced by nothing, not an entry point)
        write(cls.repo, 'docs/stray.md', '# Stray\n\nNothing links here.\n')

        # DEFECT 4: exact duplicate pair
        duplicated = '# Shared\n\nIdentical content in two places.\n'
        write(cls.repo, 'docs/dup-a.md', duplicated)
        write(cls.repo, 'docs/dup-b.md', duplicated)

        # DEFECT 5: near-duplicate pair
        body = 'This paragraph is repeated almost verbatim across two files. ' * 12
        write(cls.repo, 'docs/near-a.md', body + 'Ending one.\n')
        write(cls.repo, 'docs/near-b.md', body + 'Ending two.\n')

        # DEFECT 6: a Python module imported by another. Proves import
        # resolution survives the whole pipeline -- pkg/core.py must NOT be
        # an orphan, because pkg/helper.py imports it.
        write(cls.repo, 'pkg/core.py', 'def run():\n    return 1\n')
        write(cls.repo, 'pkg/helper.py',
              'from core import run\n\n\ndef go():\n    return run()\n')

        # DEFECT 7: a near-duplicate pair at a length ratio between the sound
        # cutoff (0.905) and the unsound one this project once used (0.95).
        # An unsound gate would silently drop this pair.
        edge_body = 'This paragraph appears in both documents nearly verbatim. ' * 11
        write(cls.repo, 'docs/edge-a.md', edge_body)
        write(cls.repo, 'docs/edge-b.md', edge_body + ('Tail padding. ' * 4))

        # Link the non-orphan defect files so only docs/stray.md is an orphan.
        write(cls.repo, 'docs/index.md',
              '- [broken](broken.md)\n- [anchor](anchor.md)\n'
              '- [dup-a](dup-a.md)\n- [dup-b](dup-b.md)\n'
              '- [near-a](near-a.md)\n- [near-b](near-b.md)\n'
              '- [edge-a](edge-a.md)\n- [edge-b](edge-b.md)\n'
              '- [helper](../pkg/helper.py)\n')
        with open(os.path.join(cls.repo, 'README.md'), 'a', encoding='utf-8') as fh:
            fh.write('\n- [index](docs/index.md)\n')

        git(cls.repo, 'add', '-A')
        git(cls.repo, 'commit', '-qm', 'fixture')
        cls.result = run_scan(cls.repo)
        cls.by_category = {}
        for finding in cls.result['findings']:
            cls.by_category.setdefault(finding['category'], []).append(finding)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)

    def files_for(self, category):
        return {e['file'] for f in self.by_category.get(category, [])
                for e in f['evidence']}

    def test_broken_link_found_exactly_once(self):
        self.assertEqual(len(self.by_category.get('broken_link', [])), 1)
        self.assertIn('docs/broken.md', self.files_for('broken_link'))

    def test_dangling_anchor_found_exactly_once(self):
        self.assertEqual(len(self.by_category.get('dangling_anchor', [])), 1)
        self.assertIn('docs/anchor.md', self.files_for('dangling_anchor'))

    def test_orphan_found_and_only_the_stray_file(self):
        self.assertEqual(self.files_for('orphan'), {'docs/stray.md'})

    def test_exact_duplicate_found_exactly_once(self):
        self.assertEqual(len(self.by_category.get('duplicate', [])), 1)
        self.assertEqual(self.files_for('duplicate'), {'docs/dup-a.md', 'docs/dup-b.md'})

    def test_near_duplicate_found(self):
        self.assertIn('near_duplicate', self.by_category)
        self.assertEqual(
            {'docs/near-a.md', 'docs/near-b.md'} & self.files_for('near_duplicate'),
            {'docs/near-a.md', 'docs/near-b.md'})

    def test_python_import_resolves_end_to_end(self):
        """pkg/core.py's only inbound edge is an import, so if import
        resolution breaks it becomes a false orphan."""
        self.assertNotIn('pkg/core.py', self.files_for('orphan'))

    def test_near_duplicate_found_at_a_boundary_length_ratio(self):
        """The pair sits between the sound cutoff (0.905) and the unsound
        0.95 an earlier version used, so an unsound gate would drop it."""
        self.assertEqual(
            {'docs/edge-a.md', 'docs/edge-b.md'} & self.files_for('near_duplicate'),
            {'docs/edge-a.md', 'docs/edge-b.md'})

    def test_clean_files_produce_no_findings(self):
        flagged = {e['file'] for f in self.result['findings'] for e in f['evidence']}
        self.assertNotIn('README.md', flagged)
        self.assertNotIn('docs/setup.md', flagged)

    def test_no_check_failed(self):
        self.assertEqual(self.result['failed_checks'], [])

    def test_every_finding_validates_against_the_contract(self):
        # A scan finding also names the check that made it and its tier.
        required = {'id', 'source', 'category', 'problem', 'evidence', 'suggestion',
                    'fix', 'blast_radius', 'severity', 'confidence', 'ssot_direction',
                    'check', 'tier', 'batch_eligible'}
        for finding in self.result['findings']:
            self.assertEqual(set(finding), required, finding['category'])
            self.assertIn(finding['tier'], {'proven', 'heuristic'})
            self.assertTrue(finding['id'].startswith('sha256:'))
            self.assertIn(finding['severity'], {'low', 'medium', 'high'})
            self.assertTrue(finding['evidence'])
            for item in finding['evidence']:
                self.assertEqual(set(item), {'file', 'line', 'quote'})

    def test_finding_ids_are_unique(self):
        ids = [f['id'] for f in self.result['findings']]
        self.assertEqual(len(ids), len(set(ids)))

    def test_ids_survive_reformatting(self):
        """The load-bearing invariant, end to end."""
        before = {f['id'] for f in self.result['findings']
                  if f['category'] == 'broken_link'}
        path = os.path.join(self.repo, 'docs/broken.md')
        with open(path, encoding='utf-8') as fh:
            body = fh.read()
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write('\n\n<!-- padding -->\n\n' + body)  # shift the line number
        try:
            after = {f['id'] for f in run_scan(self.repo)['findings']
                     if f['category'] == 'broken_link'}
            self.assertEqual(before, after)
        finally:
            with open(path, 'w', encoding='utf-8') as fh:
                fh.write(body)


class TestTypeScriptImports(unittest.TestCase):
    """Extensionless and directory imports, the normal way to write TS.

    Each was reported as a high broken link, and its target as an orphan, so
    with `--fail-on high` nearly every pull request in a TypeScript project
    failed.
    """

    @classmethod
    def setUpClass(cls):
        cls.repo = tempfile.mkdtemp()
        git(cls.repo, 'init', '-q', '-b', 'main')
        write(cls.repo, 'README.md', '# App\n\nThe entry point is [a](src/a.ts).\n')
        write(cls.repo, 'src/a.ts',
              'import { b } from "./b";\nimport { h } from "./lib/helpers";\n')
        write(cls.repo, 'src/b.ts', 'export const b = 1;\n')
        write(cls.repo, 'src/lib/helpers/index.ts', 'export const h = 1;\n')
        git(cls.repo, 'add', '-A')
        git(cls.repo, 'commit', '-qm', 'init')

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)

    def test_the_scan_passes_a_high_gate(self):
        scan = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'scan.py')
        proc = subprocess.run(
            [sys.executable, scan, '.', '--format', 'github', '--fail-on', 'high'],
            cwd=self.repo, capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertNotIn('broken_link', proc.stdout)

    def test_neither_import_target_is_an_orphan(self):
        orphans = {e['file'] for f in run_scan(self.repo)['findings']
                   if f['category'] == 'orphan' for e in f['evidence']}
        self.assertNotIn('src/b.ts', orphans)
        self.assertNotIn('src/lib/helpers/index.ts', orphans)


class TestGitHubAnchors(unittest.TestCase):
    """Links that work on GitHub must not be reported as dangling."""

    @classmethod
    def setUpClass(cls):
        cls.repo = tempfile.mkdtemp()
        git(cls.repo, 'init', '-q', '-b', 'main')
        write(cls.repo, 'guide.md',
              '# Guide\n\n## Café\n\n## `--offline` flag\n\n'
              '<a id="custom"></a>\n\nText.\n')
        write(cls.repo, 'README.md',
              '# Readme\n\n'
              'See [café](guide.md#caf%C3%A9), [offline](guide.md#--offline-flag), '
              '[custom](guide.md#custom) and [gone](guide.md#not-there).\n')
        git(cls.repo, 'add', '-A')
        git(cls.repo, 'commit', '-qm', 'init')
        cls.result = run_scan(cls.repo)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)

    def test_only_the_broken_anchor_is_reported(self):
        dangling = [f['problem'] for f in self.result['findings']
                    if f['category'] == 'dangling_anchor']
        self.assertEqual(len(dangling), 1, dangling)
        self.assertIn('#not-there', dangling[0])


class TestStdlibOnly(unittest.TestCase):
    def test_no_third_party_imports(self):
        """Every dovetail module must import stdlib or a sibling module only."""
        import ast

        scripts = os.path.join(os.path.dirname(__file__), '..', 'scripts')
        siblings = {os.path.splitext(f)[0] for f in os.listdir(scripts)
                    if f.endswith('.py')}
        allowed = siblings | set(sys.stdlib_module_names)

        for name in sorted(os.listdir(scripts)):
            if not name.endswith('.py'):
                continue
            with open(os.path.join(scripts, name), encoding='utf-8') as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        root = alias.name.split('.')[0]
                        self.assertIn(root, allowed, f'{name} imports {root}')
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    root = node.module.split('.')[0]
                    self.assertIn(root, allowed, f'{name} imports from {root}')


if __name__ == '__main__':
    unittest.main()
