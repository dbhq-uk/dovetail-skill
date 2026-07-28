#!/usr/bin/env python3
"""Tests for globmatch.py."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from globmatch import glob_to_regex, matches_any  # noqa: E402


class TestGlobToRegex(unittest.TestCase):
    def test_single_star_does_not_cross_slash(self):
        pat = glob_to_regex('*.py')
        self.assertTrue(pat.match('a.py'))
        self.assertFalse(pat.match('src/a.py'))

    def test_double_star_slash_matches_zero_segments(self):
        pat = glob_to_regex('**/README.md')
        self.assertTrue(pat.match('README.md'))
        self.assertTrue(pat.match('docs/README.md'))
        self.assertTrue(pat.match('a/b/c/README.md'))

    def test_double_star_slash_anchors_on_segment_boundary(self):
        # The bug this guards: **/README.md must not match xREADME.md
        pat = glob_to_regex('**/README.md')
        self.assertFalse(pat.match('xREADME.md'))
        self.assertFalse(pat.match('docs/xREADME.md'))

    def test_double_star_in_middle(self):
        pat = glob_to_regex('docs/*/plans/**')
        self.assertTrue(pat.match('docs/en/plans/a.md'))
        self.assertTrue(pat.match('docs/zh-TW/plans/deep/b.md'))
        self.assertFalse(pat.match('docs/plans/a.md'))

    def test_question_mark_matches_one_non_slash(self):
        pat = glob_to_regex('a?.py')
        self.assertTrue(pat.match('ab.py'))
        self.assertFalse(pat.match('a/.py'))

    def test_dots_are_literal(self):
        pat = glob_to_regex('a.py')
        self.assertTrue(pat.match('a.py'))
        self.assertFalse(pat.match('axpy'))

    def test_full_match_only(self):
        pat = glob_to_regex('*.py')
        self.assertFalse(pat.match('a.py.bak'))

    def test_trailing_newline_does_not_match(self):
        # Python's `$` also matches before a trailing newline; `\Z` does not.
        # git ls-files can emit paths containing newlines, so this is reachable.
        pat = glob_to_regex('*.py')
        self.assertFalse(pat.match('a.py\n'))


class TestMatchesAny(unittest.TestCase):
    def test_empty_globs_never_match(self):
        self.assertFalse(matches_any('a.py', []))

    def test_matches_if_any_glob_matches(self):
        self.assertTrue(matches_any('docs/en/plans/x.md', ['*.py', 'docs/*/plans/**']))

    def test_no_match(self):
        self.assertFalse(matches_any('src/a.ts', ['*.py', 'docs/**']))


if __name__ == '__main__':
    unittest.main()
