#!/usr/bin/env python3
"""Tests for slugify.py — GitHub-compatible heading anchors."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from slugify import heading_slugs, slugify  # noqa: E402


class TestSlugify(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(slugify('Getting Started'), 'getting-started')

    def test_spaces_become_hyphens(self):
        self.assertEqual(slugify('a b c'), 'a-b-c')

    def test_drops_punctuation(self):
        self.assertEqual(slugify('What is it?'), 'what-is-it')
        self.assertEqual(slugify('Setup (advanced)'), 'setup-advanced')

    def test_keeps_existing_hyphens(self):
        self.assertEqual(slugify('well-known values'), 'well-known-values')

    def test_strips_inline_code_markers(self):
        self.assertEqual(slugify('the `--out` flag'), 'the---out-flag')

    def test_strips_leading_and_trailing_hyphens(self):
        self.assertEqual(slugify('  hello  '), 'hello')

    def test_keeps_underscores(self):
        self.assertEqual(slugify('snake_case name'), 'snake_case-name')


class TestHeadingSlugs(unittest.TestCase):
    def test_extracts_atx_headings(self):
        md = '# One\n\ntext\n\n## Two\n'
        self.assertEqual(heading_slugs(md), ['one', 'two'])

    def test_ignores_hashes_inside_fenced_code(self):
        md = '# Real\n\n```\n# Not a heading\n```\n\n## Also real\n'
        self.assertEqual(heading_slugs(md), ['real', 'also-real'])

    def test_ignores_tilde_fenced_code(self):
        md = '# Real\n\n~~~\n# Nope\n~~~\n'
        self.assertEqual(heading_slugs(md), ['real'])

    def test_duplicate_headings_get_numeric_suffixes(self):
        md = '# Setup\n## Setup\n### Setup\n'
        self.assertEqual(heading_slugs(md), ['setup', 'setup-1', 'setup-2'])

    def test_requires_space_after_hashes(self):
        md = '#NotAHeading\n# Yes\n'
        self.assertEqual(heading_slugs(md), ['yes'])

    def test_empty_document(self):
        self.assertEqual(heading_slugs(''), [])


if __name__ == '__main__':
    unittest.main()
