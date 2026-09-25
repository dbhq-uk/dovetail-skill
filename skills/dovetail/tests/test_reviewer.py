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

import subprocess  # noqa: E402

from reviewer import (  # noqa: E402
    ROSTER, StaleEvidenceError, ValidationError, escalation_enabled,
    needs_escalation, resolve_roster, validate_findings,
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

    def test_line_beyond_end_of_file_with_no_such_quote_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.check([finding(evidence=[
                {'file': 'README.md', 'line': 9999, 'quote': 'nowhere at all'}])])

    def test_a_quote_that_moved_is_kept_and_the_line_corrected(self):
        # An edit above the cited line shifts it. The evidence is still sound,
        # so correct the coordinate rather than throwing the finding away.
        out = self.check([finding(evidence=[
            {'file': 'README.md', 'line': 1, 'quote': 'second line'}])])
        self.assertEqual(out[0]['evidence'][0]['line'], 2)

    def test_a_real_quote_cited_beyond_the_end_is_corrected(self):
        out = self.check([finding(evidence=[
            {'file': 'README.md', 'line': 9999, 'quote': 'hello'}])])
        self.assertEqual(out[0]['evidence'][0]['line'], 1)

    def test_a_blank_line_does_not_match_every_quote(self):
        # `actual in quote` matches trivially when actual is empty, so a
        # whole-file search must guard it or any quote "moves" to a blank line.
        with open(os.path.join(self.repo, 'gappy.md'), 'w', encoding='utf-8') as fh:
            fh.write('first\n\n\nlast\n')
        with self.assertRaises(ValidationError):
            self.check([finding(evidence=[
                {'file': 'gappy.md', 'line': 1, 'quote': 'absent text'}])])

    def test_partial_quote_of_a_real_line_is_accepted(self):
        self.assertEqual(len(self.check([finding(evidence=[
            {'file': 'README.md', 'line': 1, 'quote': 'hello'}])])), 1)

    def test_a_missing_file_is_treated_as_fabrication(self):
        # It used to pass: "unreadable here is not proof of fabrication". But a
        # reviewer could then get any invented quote through by citing a file
        # that does not exist. Unproven is fabricated.
        with self.assertRaises(ValidationError) as ctx:
            self.check([finding(evidence=[
                {'file': 'no/such/file.md', 'line': 1, 'quote': 'x'}])])
        self.assertIn('does not exist', str(ctx.exception))
        self.assertIn('fabricated', str(ctx.exception))

    def test_an_unreadable_file_is_treated_as_fabrication(self):
        with open(os.path.join(self.repo, 'blob.bin'), 'wb') as fh:
            fh.write(b'\xff\xfe\x00binary')
        with self.assertRaises(ValidationError) as ctx:
            self.check([finding(evidence=[
                {'file': 'blob.bin', 'line': 1, 'quote': 'binary'}])])
        self.assertIn('fabricated', str(ctx.exception))

    def test_an_invented_quote_cited_at_a_blank_line_is_rejected(self):
        # The cited line itself is blank, and the empty string is inside every
        # quote, so `actual in quote` vouched for anything cited there.
        with open(os.path.join(self.repo, 'gappy.md'), 'w', encoding='utf-8') as fh:
            fh.write('first\n\nlast\n')
        with self.assertRaises(ValidationError) as ctx:
            self.check([finding(evidence=[
                {'file': 'gappy.md', 'line': 2,
                 'quote': 'an invented sentence the file never held'}])])
        self.assertIn('fabricated', str(ctx.exception))

    def test_an_empty_quote_is_rejected(self):
        for quote in ('', '   \n  '):
            with self.subTest(quote=quote):
                with self.assertRaises(ValidationError) as ctx:
                    self.check([finding(evidence=[
                        {'file': 'README.md', 'line': 1, 'quote': quote}])])
                self.assertIn('no quote', str(ctx.exception))

    def test_evidence_with_no_quote_at_all_is_rejected(self):
        with self.assertRaises(ValidationError):
            self.check([finding(evidence=[{'file': 'README.md', 'line': 1}])])

    def test_a_path_outside_the_repository_is_rejected(self):
        # Even when the quote really is in that outside file: it is not
        # evidence about this repository.
        with tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, 'notes.md')
            with open(target, 'w', encoding='utf-8') as fh:
                fh.write('hello world\n')
            relative = os.path.relpath(target, self.repo)
            for cited in (relative, target):
                with self.subTest(cited=cited):
                    with self.assertRaises(ValidationError) as ctx:
                        self.check([finding(evidence=[
                            {'file': cited, 'line': 1, 'quote': 'hello world'}])])
                    self.assertIn('outside the repository', str(ctx.exception))

    @unittest.skipIf(os.name == 'nt', 'symlinks need privileges on Windows')
    def test_a_symlink_that_leaves_the_repository_is_rejected(self):
        with tempfile.TemporaryDirectory() as outside:
            target = os.path.join(outside, 'notes.md')
            with open(target, 'w', encoding='utf-8') as fh:
                fh.write('hello world\n')
            os.symlink(target, os.path.join(self.repo, 'link.md'))
            with self.assertRaises(ValidationError):
                self.check([finding(evidence=[
                    {'file': 'link.md', 'line': 1, 'quote': 'hello world'}])])

    def test_a_short_line_cannot_vouch_for_a_long_quote(self):
        # `---` is inside any quote that holds three hyphens, so the reverse
        # match must not accept a line that covers only a sliver of the quote.
        with open(os.path.join(self.repo, 'doc.md'), 'w', encoding='utf-8') as fh:
            fh.write('---\ntitle: x\n---\n')
        with self.assertRaises(ValidationError):
            self.check([finding(evidence=[
                {'file': 'doc.md', 'line': 1,
                 'quote': 'the timeout is 30 seconds --- set in config.toml'}])])

    def test_a_real_line_cannot_vouch_for_words_added_to_it(self):
        # The line is most of the quote, but the words on the end are invented
        # and turn the claim around. Punctuation may be added; words may not.
        with open(os.path.join(self.repo, 'doc.md'), 'w', encoding='utf-8') as fh:
            fh.write('Deploys run automatically on every merge to the default branch\n')
        with self.assertRaises(ValidationError):
            self.check([finding(evidence=[
                {'file': 'doc.md', 'line': 1,
                 'quote': 'Deploys run automatically on every merge to the '
                          'default branch except on Fridays'}])])

    def test_a_quote_with_a_little_extra_at_the_ends_is_still_accepted(self):
        # What the reverse match is for: a bullet or a closing full stop the
        # reviewer carried and the line does not have.
        out = self.check([finding(evidence=[
            {'file': 'README.md', 'line': 1, 'quote': '- hello world.'}])])
        self.assertEqual(out[0]['evidence'][0]['line'], 1)

    def test_a_quote_that_wraps_onto_the_next_line_is_accepted(self):
        # Hard-wrapped prose: the reviewer quotes the sentence, not the line.
        with open(os.path.join(self.repo, 'wrap.md'), 'w', encoding='utf-8') as fh:
            fh.write('intro\nrequests time out after\n30 seconds by default\n')
        out = self.check([finding(evidence=[
            {'file': 'wrap.md', 'line': 2,
             'quote': 'requests time out after 30 seconds'}])])
        self.assertEqual(out[0]['evidence'][0]['line'], 2)

    def test_a_wrapped_quote_does_not_cross_a_blank_line(self):
        with open(os.path.join(self.repo, 'wrap.md'), 'w', encoding='utf-8') as fh:
            fh.write('requests time out after\n\n30 seconds by default\n')
        with self.assertRaises(ValidationError):
            self.check([finding(evidence=[
                {'file': 'wrap.md', 'line': 1,
                 'quote': 'requests time out after 30 seconds'}])])

    def test_a_wrapped_quote_cited_at_the_wrong_line_is_corrected(self):
        with open(os.path.join(self.repo, 'wrap.md'), 'w', encoding='utf-8') as fh:
            fh.write('intro\nrequests time out after\n30 seconds by default\n')
        out = self.check([finding(evidence=[
            {'file': 'wrap.md', 'line': 1,
             'quote': 'requests time out after 30 seconds'}])])
        self.assertEqual(out[0]['evidence'][0]['line'], 2)

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




class EditedUnderTheReviewer(unittest.TestCase):
    """A file edited mid-run is not a reviewer fabricating evidence.

    Observed live: during a triage run the orchestrator applied a fix the user
    had approved, and the next reviewer to return had its entire output
    rejected because one finding quoted the line that fix had rewritten. Ten
    sound findings were nearly lost because dovetail edited the file itself.

    The committed blob separates the cases exactly - no timestamp heuristics.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = self._tmp.name
        self._git('init', '-q')
        self._git('config', 'user.email', 'test@example.com')
        self._git('config', 'user.name', 'Test')
        self._write('README.md', 'the maintainer is Alex Smith\nsecond line\n')
        self._git('add', '-A')
        self._git('commit', '-qm', 'initial')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _git(self, *args):
        subprocess.run(['git', '-C', self.repo, *args], check=True,
                       capture_output=True)

    def _write(self, name, body):
        with open(os.path.join(self.repo, name), 'w', encoding='utf-8') as fh:
            fh.write(body)

    def _check(self, quote, **kw):
        return validate_findings(
            [finding(evidence=[{'file': 'README.md', 'line': 1, 'quote': quote}])],
            'staleness', self.repo, **kw)

    def test_a_quote_edited_away_is_stale_not_fabricated(self):
        self._write('README.md', 'the maintainer is Sam Jones\nsecond line\n')
        with self.assertRaises(StaleEvidenceError) as ctx:
            self._check('the maintainer is Alex Smith')
        message = str(ctx.exception)
        self.assertIn('edited after', message)
        self.assertNotIn('fabricated evidence', message)

    def test_a_stale_finding_is_dropped_without_losing_the_others(self):
        self._write('README.md', 'the maintainer is Sam Jones\nsecond line\n')
        rejected = []
        out = validate_findings(
            [finding(problem='a', evidence=[
                {'file': 'README.md', 'line': 2, 'quote': 'second line'}]),
             finding(problem='b', evidence=[
                 {'file': 'README.md', 'line': 1,
                  'quote': 'the maintainer is Alex Smith'}]),
             finding(problem='c', evidence=[
                 {'file': 'README.md', 'line': 1, 'quote': 'Sam Jones'}])],
            'contradiction', self.repo, rejected=rejected)
        self.assertEqual([f['problem'] for f in out], ['a', 'c'])
        self.assertEqual(len(rejected), 1)
        self.assertIn('not fabricated', rejected[0])

    def test_a_quote_in_neither_version_is_still_fabrication(self):
        self._write('README.md', 'the maintainer is Sam Jones\nsecond line\n')
        with self.assertRaises(ValidationError) as ctx:
            self._check('a quote that was never anywhere')
        self.assertIn('fabricated', str(ctx.exception))

    def test_stale_is_a_validation_error_so_existing_handlers_still_drop_it(self):
        self._write('README.md', 'rewritten\n')
        self.assertTrue(issubclass(StaleEvidenceError, ValidationError))

    def test_an_unchanged_file_still_rejects_a_fabricated_quote(self):
        with self.assertRaises(ValidationError) as ctx:
            self._check('invented text')
        self.assertIn('fabricated', str(ctx.exception))

    def test_a_file_deleted_mid_run_is_stale_not_fabricated(self):
        os.remove(os.path.join(self.repo, 'README.md'))
        with self.assertRaises(StaleEvidenceError):
            self._check('the maintainer is Alex Smith')

    def test_a_deleted_file_does_not_vouch_for_an_invented_quote(self):
        os.remove(os.path.join(self.repo, 'README.md'))
        with self.assertRaises(ValidationError) as ctx:
            self._check('a quote that was never anywhere')
        self.assertIn('fabricated', str(ctx.exception))

    def test_stale_is_detected_when_the_scan_root_is_a_subdirectory(self):
        # `git show HEAD:<path>` reads from the top of the repository, so a
        # repo_root below it found no committed file and called every edited
        # line fabricated.
        os.makedirs(os.path.join(self.repo, 'docs'))
        self._write('docs/guide.md', 'the port is 8080\n')
        self._git('add', '-A')
        self._git('commit', '-qm', 'guide')
        self._write('docs/guide.md', 'the port is 9090\n')
        with self.assertRaises(StaleEvidenceError):
            validate_findings(
                [finding(evidence=[
                    {'file': 'guide.md', 'line': 1, 'quote': 'the port is 8080'}])],
                'staleness', os.path.join(self.repo, 'docs'))


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
