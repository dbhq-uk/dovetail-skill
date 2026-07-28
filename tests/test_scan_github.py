#!/usr/bin/env python3
"""Tests for GitHub annotation output and exit codes."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from scan import exit_code, format_github  # noqa: E402


def finding(severity='high', source='graph', category='broken_link',
            problem='p', file='README.md', line=4):
    return {
        'id': 'sha256:x', 'source': source, 'category': category,
        'problem': problem,
        'evidence': [{'file': file, 'line': line, 'quote': 'q'}],
        'suggestion': 's', 'fix': {'kind': 'none'}, 'blast_radius': [],
        'severity': severity, 'confidence': 'high', 'ssot_direction': 'n/a',
    }


def result(findings, suppressed=0, failed=None):
    counts = {'high': 0, 'medium': 0, 'low': 0}
    for f in findings:
        counts[f['severity']] += 1
    return {'findings': findings, 'suppressed': suppressed,
            'counts': counts, 'failed_checks': failed or []}


class TestFormatGithub(unittest.TestCase):
    def test_high_severity_becomes_an_error_annotation(self):
        out = format_github(result([finding(severity='high')]))
        self.assertIn('::error file=README.md,line=4', out)

    def test_medium_and_low_become_warnings(self):
        out = format_github(result([finding(severity='medium'),
                                    finding(severity='low')]))
        self.assertEqual(out.count('::warning '), 2)
        self.assertNotIn('::error ', out)

    def test_annotation_includes_the_category_as_title(self):
        out = format_github(result([finding(category='orphan')]))
        self.assertIn('title=orphan', out)

    def test_newlines_in_the_message_are_escaped(self):
        out = format_github(result([finding(problem='line one\nline two')]))
        self.assertIn('%0A', out)
        self.assertNotIn('line one\nline two', out)

    def test_commas_in_the_message_are_not_escaped(self):
        # GitHub decodes %2C only in property values, not in the message, so
        # escaping it here would render literally in the Actions UI.
        out = format_github(result([finding(problem='a, b')]))
        self.assertIn('a, b', out)
        self.assertNotIn('%2C', out)

    def test_commas_in_a_property_value_are_escaped(self):
        out = format_github(result([finding(file='we,ird.md')]))
        self.assertIn('file=we%2Cird.md', out)

    def test_colons_in_a_property_value_are_escaped(self):
        out = format_github(result([finding(category='a:b')]))
        self.assertIn('title=a%3Ab', out)

    def test_colons_in_the_message_are_not_escaped(self):
        out = format_github(result([finding(problem='see this: thing')]))
        self.assertIn('see this: thing', out)
        self.assertNotIn('%3A', out)

    def test_percent_is_escaped_first_in_both(self):
        out = format_github(result([finding(problem='100%', file='a%b.md')]))
        self.assertIn('100%25', out)
        self.assertIn('file=a%25b.md', out)

    def test_no_findings_emits_no_annotations(self):
        self.assertEqual(format_github(result([])).strip(), '')

    def test_failed_checks_emit_an_error(self):
        out = format_github(result([], failed=['orphans']))
        self.assertIn('::error ', out)
        self.assertIn('orphans', out)


class TestExitCode(unittest.TestCase):
    def test_none_never_fails(self):
        self.assertEqual(exit_code(result([finding(severity='high')]), 'none'), 0)

    def test_high_fails_on_high(self):
        self.assertEqual(exit_code(result([finding(severity='high')]), 'high'), 1)

    def test_medium_does_not_fail_on_high(self):
        self.assertEqual(exit_code(result([finding(severity='medium')]), 'high'), 0)

    def test_medium_fails_on_medium(self):
        self.assertEqual(exit_code(result([finding(severity='medium')]), 'medium'), 1)

    def test_high_fails_on_medium_threshold(self):
        self.assertEqual(exit_code(result([finding(severity='high')]), 'medium'), 1)

    def test_low_fails_on_low(self):
        self.assertEqual(exit_code(result([finding(severity='low')]), 'low'), 1)

    def test_empty_result_passes(self):
        self.assertEqual(exit_code(result([]), 'low'), 0)

    def test_judgement_findings_never_fail_the_build(self):
        judged = finding(severity='high', source='reviewer:contradiction')
        self.assertEqual(exit_code(result([judged]), 'high'), 0)

    def test_a_failed_check_fails_the_build(self):
        self.assertEqual(exit_code(result([], failed=['broken_links']), 'high'), 1)

    def test_a_failed_check_does_not_fail_when_fail_on_is_none(self):
        self.assertEqual(exit_code(result([], failed=['broken_links']), 'none'), 0)

    def test_failed_checks_are_error_annotations(self):
        out = format_github(result([], failed=['broken_links']))
        self.assertIn('::error ', out)
        self.assertNotIn('::warning ', out)


if __name__ == '__main__':
    unittest.main()
