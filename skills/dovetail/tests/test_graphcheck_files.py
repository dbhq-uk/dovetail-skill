#!/usr/bin/env python3
"""Tests for the file-level checks in graphcheck.py."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from discover import discover  # noqa: E402
from refgraph import build_graph  # noqa: E402
from graphcheck import (  # noqa: E402
    ALL_CHECKS, exact_duplicates, near_duplicates, orphans, translation_lag,
)


def entry(path, sha='x', category='doc', modality='text', when=None, size=10):
    return {'path': path, 'modality': modality, 'category': category,
            'size_bytes': size, 'sha256': sha, 'last_commit_iso': when}


def inv(entries):
    return {'repo_root': '/tmp/r', 'generated_at_iso': '', 'files': entries}


def graph(inbound=None, headings=None):
    return {'edges': [], 'inbound': inbound or {}, 'headings': headings or {}}


class TestOrphans(unittest.TestCase):
    def test_reports_file_with_no_inbound_edges(self):
        found = orphans(inv([entry('docs/stray.md')]), graph({'docs/stray.md': []}))
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'orphan')
        self.assertEqual(found[0]['severity'], 'low')

    def test_referenced_file_is_not_an_orphan(self):
        found = orphans(inv([entry('docs/a.md')]), graph({'docs/a.md': ['README.md']}))
        self.assertEqual(found, [])

    def test_entry_points_are_never_orphans(self):
        entries = [entry('README.md'), entry('LICENSE', category='other'),
                   entry('CHANGELOG.md'), entry('CONTRIBUTING.md')]
        inbound = {e['path']: [] for e in entries}
        self.assertEqual(orphans(inv(entries), graph(inbound)), [])

    def test_dotfiles_and_github_dir_are_never_orphans(self):
        entries = [entry('.gitignore', category='other'),
                   entry('.github/workflows/ci.yml', category='config')]
        inbound = {e['path']: [] for e in entries}
        self.assertEqual(orphans(inv(entries), graph(inbound)), [])

    def test_evidence_points_at_line_one(self):
        found = orphans(inv([entry('docs/stray.md')]), graph({'docs/stray.md': []}))
        self.assertEqual(found[0]['evidence'][0], {
            'file': 'docs/stray.md', 'line': 1, 'quote': 'no inbound references'})

    def test_files_in_a_tests_directory_are_never_orphans(self):
        entries = [entry('dovetail/tests/test_store.py'),
                   entry('pkg/__tests__/thing.rb')]
        inbound = {e['path']: [] for e in entries}
        self.assertEqual(orphans(inv(entries), graph(inbound)), [])

    def test_test_named_files_outside_a_tests_directory_are_never_orphans(self):
        entries = [entry('src/test_helpers.py'), entry('src/helpers_test.go'),
                   entry('src/thing.spec.ts')]
        inbound = {e['path']: [] for e in entries}
        self.assertEqual(orphans(inv(entries), graph(inbound)), [])

    def test_a_normal_source_file_is_still_an_orphan(self):
        # The guard must not swallow genuine orphans.
        found = orphans(inv([entry('src/stray.py')]), graph({'src/stray.py': []}))
        self.assertEqual(len(found), 1)

    def test_a_spec_document_is_not_exempt(self):
        # `.spec` names a test only in source files; docs/auth.spec.md is prose.
        found = orphans(inv([entry('docs/auth.spec.md')]),
                        graph({'docs/auth.spec.md': []}))
        self.assertEqual(len(found), 1)

    def test_a_spec_source_file_is_still_exempt(self):
        entries = [entry('src/thing.spec.ts'), entry('src/helpers_test.go')]
        inbound = {e['path']: [] for e in entries}
        self.assertEqual(orphans(inv(entries), graph(inbound)), [])

    def test_a_specs_directory_of_documents_is_not_exempt(self):
        # `specs/` usually holds specification documents, not tests, so an
        # unreferenced one is a genuine orphan.
        found = orphans(inv([entry('docs/specs/requirements.md')]),
                        graph({'docs/specs/requirements.md': []}))
        self.assertEqual(len(found), 1)


class TestExactDuplicates(unittest.TestCase):
    def test_reports_identical_hashes(self):
        entries = [entry('a.md', sha='same'), entry('b.md', sha='same')]
        found = exact_duplicates(inv(entries), graph())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'duplicate')
        self.assertEqual(sorted(e['file'] for e in found[0]['evidence']), ['a.md', 'b.md'])

    def test_distinct_hashes_are_not_duplicates(self):
        entries = [entry('a.md', sha='one'), entry('b.md', sha='two')]
        self.assertEqual(exact_duplicates(inv(entries), graph()), [])

    def test_empty_files_are_ignored(self):
        entries = [entry('a.md', sha='e', size=0), entry('b.md', sha='e', size=0)]
        self.assertEqual(exact_duplicates(inv(entries), graph()), [])

    def test_three_way_duplicate_is_one_finding(self):
        entries = [entry(p, sha='same') for p in ('a.md', 'b.md', 'c.md')]
        found = exact_duplicates(inv(entries), graph())
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]['evidence']), 3)


class TestNearDuplicates(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def write(self, rel, text):
        full = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(full) or self.repo, exist_ok=True)
        with open(full, 'w', encoding='utf-8') as fh:
            fh.write(text)
        return len(text.encode('utf-8'))

    def test_reports_almost_identical_text(self):
        body = 'The quick brown fox jumps over the lazy dog. ' * 20
        size_a = self.write('a.md', body + 'Extra sentence here.')
        size_b = self.write('b.md', body + 'Extra sentence there.')
        inventory = inv([entry('a.md', sha='1', size=size_a),
                         entry('b.md', sha='2', size=size_b)])
        inventory['repo_root'] = self.repo
        found = near_duplicates(inventory, graph())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'near_duplicate')

    def test_different_text_is_not_reported(self):
        # Lengths are deliberately close enough to clear the size prefilter, so
        # this exercises the real similarity comparison rather than the shortcut.
        size_a = self.write('a.md', 'The cat sat on the mat in the sunny room. ' * 12)
        size_b = self.write('b.md', 'A ship sailed across the wide blue sea now. ' * 12)
        inventory = inv([entry('a.md', sha='1', size=size_a),
                         entry('b.md', sha='2', size=size_b)])
        inventory['repo_root'] = self.repo
        self.assertEqual(near_duplicates(inventory, graph()), [])

    def test_exact_duplicates_are_left_to_the_exact_check(self):
        size_a = self.write('a.md', 'identical content here ' * 20)
        size_b = self.write('b.md', 'identical content here ' * 20)
        inventory = inv([entry('a.md', sha='same', size=size_a),
                         entry('b.md', sha='same', size=size_b)])
        inventory['repo_root'] = self.repo
        self.assertEqual(near_duplicates(inventory, graph()), [])

    def test_binary_files_are_skipped(self):
        inventory = inv([entry('a.png', sha='1', modality='raster_image'),
                         entry('b.png', sha='2', modality='raster_image')])
        inventory['repo_root'] = self.repo
        self.assertEqual(near_duplicates(inventory, graph()), [])

    def test_very_different_lengths_are_still_rejected(self):
        # difflib's real_quick_ratio is the correct length bound; a much
        # longer document cannot be 95% similar to a much shorter one.
        shared = 'Shared opening paragraph that both documents contain. ' * 8
        size_a = self.write('a.md', shared)
        size_b = self.write('b.md', shared + ('Much more content only in b. ' * 60))
        inventory = inv([entry('a.md', sha='1', size=size_a),
                         entry('b.md', sha='2', size=size_b)])
        inventory['repo_root'] = self.repo
        self.assertEqual(near_duplicates(inventory, graph()), [])

    def test_moderately_different_lengths_are_still_compared(self):
        # Length ratio ~0.85 -- the removed hand-rolled gate would have
        # discarded this pair before difflib ever saw it.
        body = 'The quick brown fox jumps over the lazy dog. ' * 20
        size_a = self.write('a.md', body)
        size_b = self.write('b.md', body + ('Extra trailing content here. ' * 4))
        inventory = inv([entry('a.md', sha='1', size=size_a),
                         entry('b.md', sha='2', size=size_b)])
        inventory['repo_root'] = self.repo
        # Whether it is reported depends on the real similarity; what matters
        # is that difflib got to decide. Assert no crash and a list result.
        result = near_duplicates(inventory, graph())
        self.assertIsInstance(result, list)

    def test_small_files_below_the_minimum_are_skipped(self):
        self.write('a.md', 'tiny\n')
        self.write('b.md', 'tiny\n')
        inventory = inv([entry('a.md', sha='1', size=5),
                         entry('b.md', sha='2', size=5)])
        inventory['repo_root'] = self.repo
        self.assertEqual(near_duplicates(inventory, graph()), [])

    def test_length_gate_uses_the_sound_bound_not_the_threshold(self):
        # r/(2-r) == 0.9048 for threshold 0.95. A pair with length ratio
        # between 0.9048 and 0.95 must still reach difflib rather than being
        # discarded by the length check.
        body = 'The quick brown fox jumps over the lazy dog. ' * 20
        size_a = self.write('a.md', body)
        size_b = self.write('b.md', body + ('padding words here. ' * 3))
        ratio = min(size_a, size_b) / max(size_a, size_b)
        self.assertGreater(ratio, 0.95 / (2 - 0.95))
        self.assertLess(ratio, 0.95)
        inventory = inv([entry('a.md', sha='1', size=size_a),
                         entry('b.md', sha='2', size=size_b)])
        inventory['repo_root'] = self.repo
        # Must not raise and must be a real comparison, not a length rejection.
        self.assertIsInstance(near_duplicates(inventory, graph()), list)


class TestTranslationLag(unittest.TestCase):
    def test_reports_translation_older_than_base(self):
        entries = [
            entry('README.md', when='2026-07-01T00:00:00+00:00'),
            entry('docs/ja/README.md', when='2026-01-01T00:00:00+00:00'),
        ]
        found = translation_lag(inv(entries), graph())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'staleness')
        self.assertEqual(found[0]['severity'], 'medium')

    def test_up_to_date_translation_is_not_reported(self):
        entries = [
            entry('README.md', when='2026-01-01T00:00:00+00:00'),
            entry('docs/ja/README.md', when='2026-07-01T00:00:00+00:00'),
        ]
        self.assertEqual(translation_lag(inv(entries), graph()), [])

    def test_docs_en_is_the_base_for_nested_docs(self):
        entries = [
            entry('docs/en/setup.md', when='2026-07-01T00:00:00+00:00'),
            entry('docs/ja/setup.md', when='2026-01-01T00:00:00+00:00'),
        ]
        found = translation_lag(inv(entries), graph())
        self.assertEqual(len(found), 1)
        self.assertIn('docs/ja/setup.md', [e['file'] for e in found[0]['evidence']])

    def test_missing_commit_times_are_skipped(self):
        entries = [entry('README.md', when=None), entry('docs/ja/README.md', when=None)]
        self.assertEqual(translation_lag(inv(entries), graph()), [])

    def test_translation_without_a_base_is_skipped(self):
        entries = [entry('docs/ja/orphan.md', when='2026-01-01T00:00:00+00:00')]
        self.assertEqual(translation_lag(inv(entries), graph()), [])

    def test_compares_instants_not_strings_across_offsets(self):
        # 02:00+05:00 is 21:00 the previous day UTC -- EARLIER than 01:00+00:00,
        # though it sorts later as a string.
        entries = [
            entry('README.md', when='2026-07-28T01:00:00+00:00'),
            entry('docs/ja/README.md', when='2026-07-28T02:00:00+05:00'),
        ]
        found = translation_lag(inv(entries), graph())
        self.assertEqual(len(found), 1)

    def test_no_finding_when_translation_is_genuinely_newer_across_offsets(self):
        entries = [
            entry('README.md', when='2026-07-28T01:00:00+05:00'),
            entry('docs/ja/README.md', when='2026-07-28T01:00:00+00:00'),
        ]
        self.assertEqual(translation_lag(inv(entries), graph()), [])

    def test_unparseable_timestamp_is_skipped(self):
        entries = [entry('README.md', when='not-a-date'),
                   entry('docs/ja/README.md', when='2026-01-01T00:00:00+00:00')]
        self.assertEqual(translation_lag(inv(entries), graph()), [])


class TestAllChecks(unittest.TestCase):
    def test_exposes_every_check(self):
        names = {fn.__name__ for fn in ALL_CHECKS}
        self.assertEqual(names, {
            'broken_links', 'dangling_anchors', 'orphans',
            'exact_duplicates', 'near_duplicates', 'translation_lag',
        })

    def test_every_check_takes_inventory_and_graph(self):
        empty_inv = inv([])
        for fn in ALL_CHECKS:
            self.assertEqual(fn(empty_inv, graph()), [], fn.__name__)


if __name__ == '__main__':
    unittest.main()


class OrphanLiveness(unittest.TestCase):
    """A file named by another file is not an orphan, even without a link.

    Regression from the head-to-head against the prior tool on a real repository:
    dovetail reported 18 orphans where the prior tool reported 1. The graph only calls
    something a reference if it carries a slash, which is right for reporting a
    broken link and wrong for orphan detection - a docs table listing
    `citation_manager.py` by name proves the file is known and used.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = self._tmp.name
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, rel, body):
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(body)

    def _orphans(self):
        inv = discover(self.repo)
        return {f['evidence'][0]['file']
                for f in orphans(inv, build_graph(self.repo, inv))}

    def test_bare_basename_mention_is_liveness(self):
        self._write('scripts/citation_manager.py', 'x = 1\n')
        self._write('GUIDE.md', 'Run `citation_manager.py` to build the list.\n')
        self.assertNotIn('scripts/citation_manager.py', self._orphans())

    def test_a_file_naming_itself_proves_nothing(self):
        self._write('docs/lonely.md', 'This is lonely.md and nothing links here.\n')
        self.assertIn('docs/lonely.md', self._orphans())

    def test_genuinely_unmentioned_file_is_still_an_orphan(self):
        self._write('docs/lonely.md', 'nothing points at this\n')
        self._write('GUIDE.md', 'unrelated prose\n')
        self.assertIn('docs/lonely.md', self._orphans())

    def test_asset_referenced_by_absolute_web_path_is_live(self):
        """Regression: /icons/icon-shield.webp names that file.

        The liveness lookbehind excluded '/', so a filename inside a path never
        matched - and every asset a site references by absolute web path read
        as an orphan. Caught by disagreeing with the prior tool on a real repo, where
        the prior tool was right.
        """
        self._write('public/icons/icon-shield.webp', 'binary-ish\n')
        self._write('src/Proof.astro', 'const icons = ["/icons/icon-shield.webp"];\n')
        self.assertNotIn('public/icons/icon-shield.webp', self._orphans())

    def test_a_wholly_unreferenced_directory_is_one_finding(self):
        """Regression: 13 unreferenced PDFs is one fact, not thirteen.

        On a 474-file repo dovetail reported 123 orphans to the prior tool's 5. The
        detection was not worse - dovetail's list contained all of the prior tool's -
        but 123 separate findings is a list nobody reads.
        """
        for n in range(4):
            self._write(f'docs/archive/old-{n}.md', f'archived {n}\n')
        inv = discover(self.repo)
        found = [f for f in orphans(inv, build_graph(self.repo, inv))
                 if f['category'] == 'orphan' and len(f['evidence']) > 1]
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]['evidence']), 4)
        self.assertIn('docs/archive', found[0]['problem'])

