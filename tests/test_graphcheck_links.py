#!/usr/bin/env python3
"""Tests for the link and anchor checks in graphcheck.py."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from graphcheck import broken_links, dangling_anchors  # noqa: E402


def inv(paths):
    return {
        'repo_root': '/tmp/r', 'generated_at_iso': '',
        'files': [{'path': p, 'modality': 'text', 'category': 'doc',
                   'size_bytes': 1, 'sha256': 'x', 'last_commit_iso': None}
                  for p in paths],
    }


def edge(src, line, raw, dst, anchor=None, kind='md_link'):
    return {'src': src, 'line': line, 'kind': kind, 'raw': raw,
            'dst': dst, 'anchor': anchor}


class TestBrokenLinks(unittest.TestCase):
    def test_reports_unresolvable_target(self):
        graph = {'edges': [edge('README.md', 5, 'docs/gone.md', None)],
                 'inbound': {}, 'headings': {}}
        found = broken_links(inv(['README.md']), graph)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'broken_link')
        self.assertEqual(found[0]['severity'], 'high')
        self.assertEqual(found[0]['evidence'][0]['file'], 'README.md')
        self.assertEqual(found[0]['evidence'][0]['line'], 5)
        self.assertIn('docs/gone.md', found[0]['evidence'][0]['quote'])

    def test_resolved_targets_are_not_reported(self):
        graph = {'edges': [edge('README.md', 1, 'a.md', 'a.md')],
                 'inbound': {}, 'headings': {}}
        self.assertEqual(broken_links(inv(['README.md', 'a.md']), graph), [])

    def test_path_literals_are_not_reported(self):
        # Prose mentioning a plausible path is too weak to call broken.
        graph = {'edges': [edge('README.md', 1, 'some/thing.md', None,
                                kind='path_literal')],
                 'inbound': {}, 'headings': {}}
        self.assertEqual(broken_links(inv(['README.md']), graph), [])

    def test_source_is_graph(self):
        graph = {'edges': [edge('README.md', 1, 'gone.md', None)],
                 'inbound': {}, 'headings': {}}
        self.assertEqual(broken_links(inv(['README.md']), graph)[0]['source'], 'graph')

    def test_identical_broken_link_on_two_lines_is_one_finding(self):
        graph = {'edges': [edge('README.md', 1, 'gone.md', None),
                           edge('README.md', 9, 'gone.md', None)],
                 'inbound': {}, 'headings': {}}
        found = broken_links(inv(['README.md']), graph)
        self.assertEqual(len(found), 1)
        self.assertEqual(len(found[0]['evidence']), 2)


class TestDanglingAnchors(unittest.TestCase):
    def test_reports_missing_anchor(self):
        graph = {
            'edges': [edge('README.md', 3, 'docs/a.md#nope', 'docs/a.md', 'nope')],
            'inbound': {},
            'headings': {'docs/a.md': ['install', 'usage']},
        }
        found = dangling_anchors(inv(['README.md', 'docs/a.md']), graph)
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'dangling_anchor')
        self.assertEqual(found[0]['severity'], 'medium')

    def test_existing_anchor_is_not_reported(self):
        graph = {
            'edges': [edge('README.md', 3, 'docs/a.md#install', 'docs/a.md', 'install')],
            'inbound': {},
            'headings': {'docs/a.md': ['install']},
        }
        self.assertEqual(dangling_anchors(inv(['README.md', 'docs/a.md']), graph), [])

    def test_anchor_into_file_with_no_heading_index_is_skipped(self):
        # Non-markdown targets have no headings; we cannot judge, so we do not.
        graph = {
            'edges': [edge('README.md', 3, 'src/a.py#L10', 'src/a.py', 'L10')],
            'inbound': {}, 'headings': {},
        }
        self.assertEqual(dangling_anchors(inv(['README.md', 'src/a.py']), graph), [])

    def test_unresolved_target_is_left_to_broken_links(self):
        graph = {'edges': [edge('README.md', 3, 'gone.md#x', None, 'x')],
                 'inbound': {}, 'headings': {}}
        self.assertEqual(dangling_anchors(inv(['README.md']), graph), [])

    def test_suggestion_names_the_available_anchors(self):
        graph = {
            'edges': [edge('README.md', 3, 'docs/a.md#nope', 'docs/a.md', 'nope')],
            'inbound': {}, 'headings': {'docs/a.md': ['install']},
        }
        found = dangling_anchors(inv(['README.md', 'docs/a.md']), graph)
        self.assertIn('install', found[0]['suggestion'])


if __name__ == '__main__':
    unittest.main()
