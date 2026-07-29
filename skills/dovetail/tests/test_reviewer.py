"""Tests for the judgement layer's shared parts.

No LLM calls: everything here exercises the roster arithmetic and the
validator, which is the boundary that decides whether a model's output is
allowed anywhere near a user.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from reviewer import (  # noqa: E402
    ROSTER, ValidationError, escalation_enabled, needs_escalation,
    resolve_roster, validate_findings,
)

REFERENCES = os.path.join(os.path.dirname(__file__), '..', 'references')


def finding(**overrides) -> dict:
    base = {
        'category': 'staleness',
        'problem': 'The doc describes behaviour the code no longer has.',
        'evidence': [{'file': 'README.md', 'line': 1, 'quote': 'hello'}],
        'suggestion': 'Update the doc.',
        'severity': 'medium',
    }
    base.update(overrides)
    return base


class Roster(unittest.TestCase):
    def test_default_tiering(self):
        roster = resolve_roster('default')
        self.assertEqual(roster['contradiction']['model'], 'opus')
        self.assertEqual(roster['xref']['model'], 'haiku')

    def test_cheap_drops_one_tier(self):
        roster = resolve_roster('cheap')
        self.assertEqual(roster['contradiction']['model'], 'sonnet')
        self.assertEqual(roster['convention']['model'], 'haiku')

    def test_cheap_does_not_drop_below_the_floor(self):
        self.assertEqual(resolve_roster('cheap')['xref']['model'], 'haiku')

    def test_thorough_raises_everything(self):
        roster = resolve_roster('thorough')
        self.assertEqual({e['model'] for e in roster.values()}, {'opus'})

    def test_config_override_beats_the_profile(self):
        roster = resolve_roster('cheap', {'contradiction': {'model': 'opus'}})
        self.assertEqual(roster['contradiction']['model'], 'opus')

    def test_a_reviewer_can_be_disabled(self):
        roster = resolve_roster('default', {'spec-flow': {'enabled': False}})
        self.assertFalse(roster['spec-flow']['enabled'])

    def test_escalation_is_off_under_cheap(self):
        # A profile chosen to save money must not reintroduce Opus spend.
        self.assertFalse(escalation_enabled('cheap'))
        self.assertTrue(escalation_enabled('default'))

    def test_only_low_confidence_below_opus_escalates(self):
        self.assertTrue(needs_escalation({'confidence': 'low'}, 'haiku'))
        self.assertFalse(needs_escalation({'confidence': 'low'}, 'opus'))
        self.assertFalse(needs_escalation({'confidence': 'high'}, 'haiku'))

    def test_every_reviewer_has_a_rubric(self):
        # A reviewer without a rubric would be dispatched with no instructions.
        for name in ROSTER:
            path = os.path.join(REFERENCES, 'reviewers', f'{name}.md')
            self.assertTrue(os.path.exists(path), f'no rubric for {name}')


class Validation(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        with open(os.path.join(self.repo, 'README.md'), 'w', encoding='utf-8') as fh:
            fh.write('hello world\nsecond line\n')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def check(self, payload, reviewer='staleness'):
        return validate_findings(payload, reviewer, self.repo)

    def test_valid_finding_passes_and_gains_an_id(self):
        out = self.check([finding()])
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0]['id'].startswith('sha256:'))
        self.assertEqual(out[0]['source'], 'reviewer:staleness')

    def test_json_string_is_parsed(self):
        self.assertEqual(len(self.check(json.dumps([finding()]))), 1)

    def test_fenced_json_is_parsed(self):
        # Models wrap output in a fence more often than not.
        self.assertEqual(
            len(self.check('```json\n' + json.dumps([finding()]) + '\n```')), 1)

    def test_empty_array_is_valid(self):
        self.assertEqual(self.check([]), [])

    def test_non_json_raises(self):
        with self.assertRaises(ValidationError):
            self.check('I looked at the repo and it seems fine!')

    def test_object_instead_of_array_raises(self):
        with self.assertRaises(ValidationError):
            self.check({'findings': []})

    def test_deterministic_category_is_rejected(self):
        # A reviewer's version of a check Python does exactly is a guess.
        with self.assertRaises(ValidationError):
            self.check([finding(category='broken_link')])

    def test_unknown_category_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.check([finding(category='vibes')])

    def test_bad_severity_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.check([finding(severity='critical')])

    def test_missing_evidence_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.check([finding(evidence=[])])

    def test_contradiction_needs_two_sides(self):
        with self.assertRaises(ValidationError):
            self.check([finding(category='contradiction')])

    def test_fabricated_quote_is_rejected(self):
        # The most damaging failure available: a plausible quote at a plausible
        # line reads exactly like a true finding.
        with self.assertRaises(ValidationError) as ctx:
            self.check([finding(evidence=[
                {'file': 'README.md', 'line': 1, 'quote': 'text that is not there'}])])
        self.assertIn('fabricated', str(ctx.exception))

    def test_line_beyond_end_of_file_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.check([finding(evidence=[
                {'file': 'README.md', 'line': 9999, 'quote': 'hello'}])])

    def test_partial_quote_of_a_real_line_is_accepted(self):
        self.assertEqual(len(self.check([finding(evidence=[
            {'file': 'README.md', 'line': 1, 'quote': 'hello'}])])), 1)

    def test_unreadable_file_is_not_treated_as_fabrication(self):
        self.assertEqual(len(self.check([finding(evidence=[
            {'file': 'not-here.md', 'line': 1, 'quote': 'x'}])])), 1)

    def test_one_bad_finding_rejects_the_whole_batch(self):
        # A reviewer emitting malformed output is one whose valid-looking
        # findings should also be distrusted.
        with self.assertRaises(ValidationError):
            self.check([finding(), finding(severity='enormous')])

    def test_defaults_are_applied(self):
        out = self.check([finding()])[0]
        self.assertEqual(out['confidence'], 'medium')
        self.assertEqual(out['ssot_direction'], 'n/a')
        self.assertEqual(out['fix'], {'kind': 'none'})
        self.assertEqual(out['blast_radius'], [])




class LenientValidation(Validation):
    """Quarantining bad findings instead of losing good ones.

    The original design discarded a reviewer's whole batch on any invalid
    finding. On the first live run that cost every spec-flow finding because
    one of them quoted a line that did not exist - and the rest were sound.
    """

    def test_unsound_finding_is_dropped_and_the_rest_kept(self):
        rejected = []
        out = validate_findings(
            [finding(problem='a'),
             finding(problem='b', evidence=[
                 {'file': 'README.md', 'line': 1, 'quote': 'not in the file'}]),
             finding(problem='c')],
            'spec-flow', self.repo, rejected=rejected)
        self.assertEqual([f['problem'] for f in out], ['a', 'c'])
        self.assertEqual(len(rejected), 1)
        self.assertIn('fabricated', rejected[0])

    def test_what_was_dropped_is_reported_not_hidden(self):
        rejected = []
        validate_findings([finding(category='broken_link')], 'staleness',
                          self.repo, rejected=rejected)
        self.assertTrue(rejected[0])

    def test_transport_failure_still_raises(self):
        # Nothing can be salvaged from unparseable output; the caller retries.
        with self.assertRaises(ValidationError):
            validate_findings('not json at all', 'staleness', self.repo,
                              rejected=[])

    def test_strict_mode_is_unchanged_without_a_rejected_list(self):
        with self.assertRaises(ValidationError):
            validate_findings([finding(), finding(category='broken_link')],
                              'staleness', self.repo)

if __name__ == '__main__':
    unittest.main()
