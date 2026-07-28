"""Tests for candidate clustering.

Clustering decides nothing - it narrows. So the tests are mostly about what it
declines to cluster: a cluster that is too loose costs the adjudicating model
real money to read and conclude nothing about.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from claimscan import build_clusters  # noqa: E402
from discover import discover  # noqa: E402
from refgraph import build_graph  # noqa: E402


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

    def write(self, rel: str, body: str) -> None:
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(body)

    def clusters(self):
        inventory = discover(self.repo)
        return build_clusters(inventory, build_graph(self.repo, inventory))

    def entities(self):
        return {(c['entity_kind'], c['entity']) for c in self.clusters()}


class Clustering(Base):
    def test_a_quantity_across_two_files_clusters(self):
        self.write('README.md', 'Requests time out after 30 seconds.\n')
        self.write('docs/config.md', 'The default timeout is 60 seconds.\n')
        kinds = {c['entity_kind'] for c in self.clusters()}
        self.assertIn('quantity', kinds)

    def test_a_flag_across_two_files_clusters(self):
        self.write('README.md', 'Pass --fail-on high to gate the build.\n')
        self.write('docs/ci.md', 'We set --fail-on medium in CI.\n')
        self.assertIn(('flag', '--fail-on'), self.entities())

    def test_an_env_var_across_two_files_clusters(self):
        self.write('README.md', 'Set OUTLOOK_TZ to your zone.\n')
        self.write('docs/setup.md', 'OUTLOOK_TZ defaults to UTC.\n')
        self.assertIn(('env', 'outlook_tz'), self.entities())

    def test_one_file_alone_does_not_cluster(self):
        # A document repeating its own flag name is not a candidate
        # contradiction with itself.
        self.write('README.md', 'Use --fail-on high. Really, --fail-on high.\n')
        self.assertEqual(self.clusters(), [])

    def test_code_blocks_are_excluded(self):
        self.write('README.md', '```bash\nrun --fail-on high\n```\n')
        self.write('docs/ci.md', '```bash\nrun --fail-on low\n```\n')
        self.assertEqual(self.clusters(), [])

    def test_ubiquitous_entities_are_dropped(self):
        # Structural vocabulary, not a disputed fact. A cluster of forty spans
        # is one nobody can read and the model cannot conclude from.
        for i in range(20):
            self.write(f'docs/d{i}.md', 'Everything uses --verbose here.\n')
        self.assertNotIn('--verbose', {c['entity'] for c in self.clusters()})

    def test_clusters_carry_file_and_line(self):
        self.write('README.md', 'Requests time out after 30 seconds.\n')
        self.write('docs/config.md', 'The default timeout is 60 seconds.\n')
        spans = self.clusters()[0]['spans']
        self.assertTrue(all('file' in s and 'line' in s and 'quote' in s
                            for s in spans))

    def test_smallest_clusters_come_first(self):
        self.write('README.md', 'Use --alpha and --beta.\n')
        self.write('docs/a.md', 'Use --alpha.\n')
        self.write('docs/b.md', 'Use --alpha and --beta.\n')
        sizes = [len(c['spans']) for c in self.clusters()]
        self.assertEqual(sizes, sorted(sizes))

    def test_non_markdown_is_ignored(self):
        self.write('a.py', '# --fail-on high\n')
        self.write('b.py', '# --fail-on low\n')
        self.assertEqual(self.clusters(), [])


if __name__ == '__main__':
    unittest.main()
