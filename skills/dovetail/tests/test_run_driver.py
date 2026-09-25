"""The run driver: the verbs SKILL.md calls during an interactive run.

The driver exists so an interactive run never reads the whole scan, or the
whole cluster dump, into the agent's context. These tests hold the three
things that made it necessary:

- every verb prints a bounded amount, however big the repository
- a ledger reason reaches the file intact, whatever quote marks it holds
- the write-safety check sees a second edit to a file that was already
  modified, which `git status --porcelain` cannot

No model is called. Reviewer output is written by hand, as a subagent would.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
DRIVER = os.path.join(SCRIPTS, 'dovetail.py')
SKILL = os.path.join(HERE, '..', 'SKILL.md')
sys.path.insert(0, SCRIPTS)

import ci_dispatch  # noqa: E402
import dovetail  # noqa: E402
from discover import discover  # noqa: E402
from refgraph import build_graph  # noqa: E402

GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e'}

# About 10k tokens, at the usual estimate of 4 bytes per token.
CONTEXT_BUDGET_BYTES = 40_000


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


def git(repo: str, *args: str) -> str:
    return subprocess.run(['git', *args], cwd=repo, env=dict(os.environ, **GIT_ENV),
                          check=True, capture_output=True, text=True).stdout


def make_repo(files: dict[str, str]) -> tempfile.TemporaryDirectory:
    tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
    git(tmp.name, 'init', '-q', '-b', 'main')
    for rel, text in files.items():
        write(tmp.name, rel, text)
    git(tmp.name, 'add', '-A')
    git(tmp.name, 'commit', '-qm', 'fixture')
    return tmp


def build_large_repo(root: str) -> None:
    """About 900 files, with the mix of defects a real repository carries."""
    git(root, 'init', '-q', '-b', 'main')
    docs = 450
    for i in range(docs):
        area = f'area{i % 15}'
        following = f'../area{(i + 1) % 15}/page{(i + 1) % docs}.md'
        broken = f'[old notes](missing/page{i}.md)' if i % 3 == 0 else ''
        anchor = f'[usage](page{i}.md#no-such-heading)' if i % 7 == 0 else ''
        write(root, f'docs/{area}/page{i}.md',
              f'# Page {i}\n\n## Usage\n\nThis page covers part {i} of the system. '
              f'Requests time out after {30 + (i % 4) * 15} seconds, and the job '
              f'retries {3 + i % 3} times.\n\nSee [the next page]({following}). '
              f'{broken} {anchor}\n\nSet `SERVICE_{i // 2}_TOKEN` before starting, '
              f'and pass `--mode-{i % 9}`. The cache holds {100 + i} entries.\n')
    for i in range(300):
        write(root, f'src/pkg{i % 10}/mod{i}.py',
              f'"""Module {i}."""\n\nimport os\n\n\ndef handler_{i}(value):\n'
              f'    return value + {i}\n\n\ndef helper_{i}():\n    return os.getcwd()\n')
    for i in range(150):
        write(root, f'config/settings{i}.json',
              '{"timeout": %d, "name": "s%d"}\n' % (30 + i % 3, i))
    write(root, 'README.md', '# Large fixture\n\nStart with [the docs](docs/area0/page0.md).\n')
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'fixture')


class Base(unittest.TestCase):
    FILES = {
        'README.md': '# Project\n\nSee [setup](docs/setup.md) and [gone](docs/gone.md).\n',
        'docs/setup.md': '# Setup\n\nRun it.\n\nAlso see [gone](gone.md).\n',
    }

    def setUp(self) -> None:
        self._repo = make_repo(self.FILES)
        self.repo = self._repo.name
        self._home = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.env = dict(os.environ, DOVETAIL_HOME=self._home.name, **GIT_ENV)

    def tearDown(self) -> None:
        self._repo.cleanup()
        self._home.cleanup()

    def run_driver(self, *args: str) -> subprocess.CompletedProcess:
        """Run one verb in-process: the same `main`, without a Python start-up each time."""
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.dict(os.environ, {'DOVETAIL_HOME': self._home.name}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = dovetail.main([*args, '--repo', self.repo])
            except SystemExit as exc:  # argparse refusing an argument
                code = exc.code
        return subprocess.CompletedProcess(args, code, out.getvalue(), err.getvalue())

    def ok(self, *args: str) -> str:
        result = self.run_driver(*args)
        self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
        return result.stdout

    def next_id(self) -> str:
        for line in self.ok('next').splitlines():
            if line.startswith('id '):
                return line.split()[1]
        self.fail('next printed no id')

    def run_state(self) -> dict:
        runs = os.path.join(self._home.name, 'runs')
        (name,) = os.listdir(runs)
        with open(os.path.join(runs, name, 'run.json'), encoding='utf-8') as fh:
            return json.load(fh)

    def ledger_rows(self) -> list[dict]:
        path = os.path.join(self.repo, '.dovetail', 'decisions.jsonl')
        with open(path, encoding='utf-8') as fh:
            return [json.loads(line) for line in fh if line.strip()]


class TestScanAndNext(Base):
    def test_scan_prints_a_short_summary_and_keeps_the_rest_on_disk(self):
        out = self.ok('scan')
        self.assertLessEqual(len(out.splitlines()), 10, out)
        self.assertIn('2 findings', out)
        self.assertIn('tiers       2 proven, 0 heuristic', out)
        self.assertEqual(len(self.run_state()['entries']), 2)

    def test_scan_writes_nothing_into_the_repository(self):
        self.ok('scan')
        self.assertEqual(git(self.repo, 'status', '--porcelain', '--ignored'), '')

    def test_scan_outside_a_git_repository_exits_2(self):
        with tempfile.TemporaryDirectory() as plain:
            result = subprocess.run([sys.executable, DRIVER, 'scan', '--repo', plain],
                                    capture_output=True, text=True, env=self.env)
        self.assertEqual(result.returncode, 2)
        self.assertIn('not a git repository', result.stderr)

    def test_next_renders_one_finding_as_clean_markdown(self):
        self.ok('scan')
        out = self.ok('next')
        shown, agent = out.split('=== for the agent, not the user ===')
        self.assertIn('**[1/2] broken_link · high** · exact · proven', shown)
        self.assertIn('**Evidence**', shown)
        self.assertNotIn('```', shown)  # fenced blocks are for diffs only
        self.assertIn('option     Skip', agent)
        self.assertIn('recommend  none', agent)

    def test_next_shows_the_same_finding_until_it_is_decided(self):
        self.ok('scan')
        first = self.next_id()
        self.assertEqual(self.next_id(), first)
        self.ok('decide', first, 'skip')
        self.assertNotEqual(self.next_id(), first)

    def test_next_json_prints_the_raw_finding(self):
        self.ok('scan')
        finding = json.loads(self.ok('next', '--json'))
        self.assertEqual(finding['category'], 'broken_link')

    def test_next_before_scan_exits_2(self):
        result = self.run_driver('next')
        self.assertEqual(result.returncode, 2)
        self.assertIn('Run `scan` first', result.stderr)


class TestDecide(Base):
    REASON = 'the owner\'s call, "by design" - see $HOME and `ls`'

    def test_a_reason_with_both_quote_marks_reaches_the_ledger_intact(self):
        self.ok('scan')
        # Through a real shell, the way an agent runs it.
        command = ' '.join(shlex.quote(part) for part in (
            sys.executable, DRIVER, 'decide', self.next_id(), 'intentional',
            '--reason', self.REASON, '--repo', self.repo))
        result = subprocess.run(['bash', '-c', command], capture_output=True,
                                text=True, env=self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        (row,) = self.ledger_rows()
        self.assertEqual(row['reason'], self.REASON)
        self.assertEqual(row['verdict'], 'intentional')
        self.assertTrue(row['id'].startswith('sha256:'))
        self.assertTrue(row['summary'])  # never an unreadable list of hashes

    def test_the_old_python_c_template_broke_on_the_same_reason(self):
        # The command SKILL.md used to give. The reason sat inside single
        # quotes in Python source, so an apostrophe ended the string early.
        template = ("python3 -c \"import sys; sys.path.insert(0, '{scripts}'); "
                    "from store import append_decision; "
                    "append_decision('{repo}', {{'id':'sha256:x','verdict':'intentional',"
                    "'reason':'{reason}','at':'2026-09-25','summary':'s'}})\"")
        command = template.format(scripts=SCRIPTS, repo=self.repo,
                                  reason="the owner's call")
        result = subprocess.run(['bash', '-c', command], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_intentional_without_a_reason_is_refused(self):
        self.ok('scan')
        result = self.run_driver('decide', self.next_id(), 'intentional')
        self.assertEqual(result.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(self.repo, '.dovetail')))

    def test_a_recorded_decision_suppresses_the_finding_next_run(self):
        self.ok('scan')
        self.ok('decide', self.next_id(), 'wontfix', '--reason', 'kept on purpose')
        self.assertIn('suppressed  1 by prior decisions', self.ok('scan'))

    def test_a_decision_records_its_layer(self):
        # The scan re-derives exact findings, never a reviewer's, so it needs
        # to know which rows it can report stale.
        self.ok('scan')
        self.ok('decide', self.next_id(), 'wontfix', '--reason', 'kept on purpose')
        (row,) = self.ledger_rows()
        self.assertEqual(row['layer'], 'exact')
        self.assertEqual(row['check'], 'broken_links')

    def test_the_summary_names_a_decision_that_matches_nothing(self):
        self.ok('scan')
        moved = json.loads(self.ok('next', '--json'))['evidence'][0]['file']
        self.ok('decide', self.next_id(), 'wontfix', '--reason', 'kept on purpose')
        self.assertNotIn('stale', self.ok('scan'))
        # Moving the file changes the finding's id.
        os.makedirs(os.path.join(self.repo, 'moved'))
        git(self.repo, 'mv', moved, 'moved/' + os.path.basename(moved))
        git(self.repo, 'commit', '-qm', 'move')
        out = self.ok('scan')
        self.assertIn('stale       1 decision(s) match no current finding', out)
        self.assertIn('broken', out.split('stale', 1)[1])  # the row's summary

    def test_an_unknown_id_exits_2(self):
        self.ok('scan')
        result = self.run_driver('decide', 'ffffffffffff', 'skip')
        self.assertEqual(result.returncode, 2)


class TestWriteSafety(Base):
    def test_a_second_edit_to_an_already_modified_file_is_seen(self):
        write(self.repo, 'docs/setup.md', '# Setup\n\nRun it twice.\n')
        self.ok('scan')
        before = git(self.repo, 'status', '--porcelain')
        write(self.repo, 'docs/setup.md', '# Setup\n\nRun it three times.\n')
        # The old check compared this, and it cannot tell the two apart.
        self.assertEqual(git(self.repo, 'status', '--porcelain'), before)
        result = self.run_driver('check')
        self.assertEqual(result.returncode, 1)
        self.assertIn('docs/setup.md', result.stdout)

    def test_check_is_clean_straight_after_scan(self):
        self.ok('scan')
        self.assertIn('clean', self.ok('check'))

    def test_new_and_deleted_files_are_seen(self):
        self.ok('scan')
        write(self.repo, 'new.md', 'x\n')
        os.remove(os.path.join(self.repo, 'docs', 'setup.md'))
        out = self.run_driver('check').stdout
        self.assertIn('new.md', out)
        self.assertIn('docs/setup.md', out)

    def test_a_fix_records_its_own_files_and_check_stays_clean(self):
        self.ok('scan')
        write(self.repo, 'docs/gone.md', '# Gone\n')
        self.ok('decide', self.next_id(), 'fix', '--files', 'docs/gone.md')
        self.assertIn('clean', self.ok('check'))

    def test_a_fix_that_misses_a_changed_file_stops_the_run(self):
        self.ok('scan')
        write(self.repo, 'docs/gone.md', '# Gone\n')
        write(self.repo, 'docs/setup.md', '# Setup\n\nSomeone else wrote this.\n')
        result = self.run_driver('decide', self.next_id(), 'fix', '--files', 'docs/gone.md')
        self.assertEqual(result.returncode, 3)
        self.assertIn('STOP', result.stdout)
        self.assertIn('docs/setup.md', result.stdout)

    def test_fix_without_files_is_refused(self):
        self.ok('scan')
        self.assertEqual(self.run_driver('decide', self.next_id(), 'fix').returncode, 2)

    def test_a_file_outside_the_repository_is_refused(self):
        self.ok('scan')
        result = self.run_driver('decide', self.next_id(), 'fix', '--files', '../elsewhere.md')
        self.assertEqual(result.returncode, 2)
        self.assertIn('outside the repository', result.stderr)

    def test_the_ledger_write_is_dovetails_own(self):
        self.ok('scan')
        self.ok('decide', self.next_id(), 'intentional', '--reason', 'kept')
        self.assertIn('clean', self.ok('check'))


class TestRescan(Base):
    def test_a_fix_that_resolves_two_findings_says_so(self):
        self.ok('scan')
        write(self.repo, 'docs/gone.md', '# Gone\n')
        self.ok('decide', self.next_id(), 'fix', '--files', 'docs/gone.md')
        out = self.ok('rescan')
        self.assertIn('1 queued finding(s) resolved by this fix', out)
        self.assertIn('0 remaining', out)

    def test_a_fix_that_resolved_nothing_is_named(self):
        self.ok('scan')
        write(self.repo, 'docs/other.md', '# Other\n\n[setup](setup.md)\n')
        self.ok('decide', self.next_id(), 'fix', '--files', 'docs/other.md')
        out = self.ok('rescan')
        self.assertIn('did not resolve it', out)

    def test_a_finding_the_fix_introduced_is_queued(self):
        self.ok('scan')
        write(self.repo, 'docs/gone.md', '# Gone\n\n[broken](nowhere.md)\n')
        self.ok('decide', self.next_id(), 'fix', '--files', 'docs/gone.md')
        out = self.ok('rescan')
        self.assertIn('1 new finding(s)', out)
        self.assertIn('docs/gone.md', self.ok('next'))


REVIEW_FILES = {f'docs/page{i}.md': f'# Page {i}\n\nPart {i}.\n' for i in range(45)}
REVIEW_FILES['README.md'] = ('# Project\n\nrequests time out after 30 seconds\n'
                             + ''.join(f'[p{i}](docs/page{i}.md)\n' for i in range(45)))
REVIEW_FILES['docs/config.md'] = '# Config\n\nthe default timeout is 60s\n'


def judged(confidence: str = 'high', quote: str = 'the default timeout is 60s') -> dict:
    return {'category': 'contradiction',
            'problem': 'Two documents disagree about the request timeout.',
            'evidence': [{'file': 'README.md', 'line': 3,
                          'quote': 'requests time out after 30 seconds'},
                         {'file': 'docs/config.md', 'line': 3, 'quote': quote}],
            'suggestion': 'Decide which is right.', 'severity': 'high',
            'confidence': confidence, 'ssot_direction': 'uncertain'}


class TestReview(Base):
    FILES = REVIEW_FILES

    def manifest(self) -> dict:
        runs = os.path.join(self._home.name, 'runs')
        (name,) = os.listdir(runs)
        with open(os.path.join(runs, name, 'review', 'manifest.json'),
                  encoding='utf-8') as fh:
            return json.load(fh)

    def shard(self, shard_id: str) -> dict:
        (found,) = [s for s in self.manifest()['shards'] if s['id'] == shard_id]
        return found

    def skip_to_judged(self) -> str:
        """Skip the exact findings, which come first, and return the judged one."""
        for _ in range(len(self.run_state()['entries'])):
            out = self.ok('next')
            if '· judged ·' in out:
                return out
            self.ok('decide', self.next_id(), 'skip')
        self.fail('no judged finding in the queue')

    def answer(self, shard_id: str, findings: object) -> None:
        with open(self.shard(shard_id)['result'], 'w', encoding='utf-8') as fh:
            fh.write(findings if isinstance(findings, str) else json.dumps(findings))

    def interactive_prompts(self) -> list[tuple[str, str]]:
        """(model, prompt) per shard, without the interactive output note."""
        pairs = []
        for shard in self.manifest()['shards']:
            with open(shard['prompt'], encoding='utf-8') as fh:
                prompt = fh.read()
            note = dovetail._OUTPUT_NOTE.format(result=shard['result'])
            self.assertTrue(prompt.endswith(note), shard['id'])
            pairs.append((shard['model'], prompt[:-len(note)]))
        return sorted(pairs)

    def ci_prompts(self, **kwargs) -> tuple[list[tuple[str, str]], dict]:
        """(model, prompt) per `claude -p` call the scheduled job would make."""
        sent: list[tuple[str, str]] = []

        def fake_claude(prompt, model, repo_root, timeout=0, effort=None):
            sent.append((model, prompt))
            return '[]'

        with mock.patch.object(ci_dispatch, 'run_claude', fake_claude):
            result = ci_dispatch.dispatch(os.path.realpath(self.repo), **kwargs)
        return sorted(sent), result

    def test_both_paths_send_the_same_prompts_to_the_same_models(self):
        # The contract #18 asked for: one repository, both dispatch paths, and
        # every shard identical - the same files in the same batch, the same
        # prompt, the same model. Counting shards is not enough; a path that
        # batched different files, or tiered a reviewer differently, would
        # still have the right count.
        self.ok('scan')
        self.ok('prepare-review')
        ci, result = self.ci_prompts(profile=None)
        interactive = self.interactive_prompts()
        self.assertGreater(len(interactive), len({m for m, _ in interactive}))
        self.assertEqual(interactive, ci)
        self.assertEqual(result['batches_run'], len(interactive))
        staleness = [s for s in self.manifest()['shards'] if s['reviewer'] == 'staleness']
        self.assertGreater(len(staleness), 1)  # the fixture is big enough to shard
        self.assertTrue(all(s['items'] <= ci_dispatch.FILES_PER_BATCH for s in staleness))

    def test_both_paths_honour_the_config_the_same_way(self):
        write(self.repo, '.dovetail/config.toml',
              'ignore = ["docs/page1*.md"]\n\n'
              '[reviewers.xref]\nenabled = false\n\n'
              '[reviewers.staleness]\nmodel = "sonnet"\neffort = "medium"\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'config')
        self.ok('scan')
        self.ok('prepare-review')
        ci, _ = self.ci_prompts(profile=None)
        self.assertEqual(self.interactive_prompts(), ci)
        reviewers = {s['reviewer']: s['model'] for s in self.manifest()['shards']}
        self.assertNotIn('xref', reviewers)
        self.assertEqual(reviewers['staleness'], 'sonnet')
        self.assertFalse(any('docs/page12.md' in prompt for _, prompt in ci))

    def test_both_paths_refuse_an_unknown_reviewer(self):
        self.ok('scan')
        self.assertEqual(self.run_driver('prepare-review', '--reviewer', 'nope').returncode, 2)
        # The scheduled job used to drop an unknown name and run nothing, which
        # reads exactly like a reviewer that found nothing.
        with self.assertRaises(ValueError):
            ci_dispatch.dispatch(self.repo, only=['nope'])

    def test_a_shard_prompt_is_the_ci_prompt_plus_where_to_write(self):
        self.ok('scan')
        self.ok('prepare-review', '--reviewer', 'staleness')
        shard = self.shard('staleness-01')
        with open(shard['prompt'], encoding='utf-8') as fh:
            prompt = fh.read()
        self.assertIn('# Your rubric', prompt)
        self.assertIn(shard['result'], prompt)
        self.assertIn('Do not edit any file in the repository', prompt)

    def test_prepare_review_prints_one_line_per_reviewer(self):
        self.ok('scan')
        out = self.ok('prepare-review')
        self.assertLessEqual(len(out.splitlines()), 10, out)
        self.assertNotIn('"spans"', out)  # no cluster ever reaches the context

    def test_a_wave_is_capped(self):
        self.ok('scan')
        self.ok('prepare-review')
        self.assertEqual(self.ok('wave').count('  prompt '), 4)
        self.assertEqual(self.ok('wave', '--size', '50').count('  prompt '), 5)

    def test_import_queues_sound_findings_and_names_fabricated_ones(self):
        self.ok('scan')
        self.ok('prepare-review', '--reviewer', 'contradiction')
        invented = judged(quote='the default timeout is 90s')
        invented['problem'] = 'Invented.'
        self.answer('contradiction-01', [judged(), invented])
        out = self.ok('import-review')
        self.assertIn('1 finding(s) queued', out)
        self.assertIn('contradiction-01: 1 fabricated', out)
        rendered = self.skip_to_judged()
        self.assertIn('· judged · opus · high confidence', rendered)

    def test_output_that_is_not_an_array_goes_out_once_more(self):
        # The scheduled job retries unparseable output once, with the contract
        # restated. The interactive path does the same, through the next wave.
        self.ok('scan')
        self.ok('prepare-review', '--reviewer', 'contradiction')
        self.ok('wave')
        self.answer('contradiction-01', 'I found nothing worth reporting.')
        out = self.ok('import-review')
        self.assertIn('retry       1 shard(s)', out)
        self.assertIn('contradiction-01', out)
        self.assertNotIn('failed', out)
        shard = self.shard('contradiction-01')
        self.assertEqual((shard['status'], shard['attempt']), ('pending', 2))
        self.assertFalse(os.path.exists(shard['result']))
        with open(shard['prompt'], encoding='utf-8') as fh:
            self.assertTrue(fh.read().endswith(dovetail._RETRY_NOTE))
        self.assertIn('contradiction-01', self.ok('wave'))
        self.answer('contradiction-01', [judged()])
        self.assertIn('1 finding(s) queued', self.ok('import-review'))

    def test_a_shard_that_fails_twice_is_named_as_failed(self):
        self.ok('scan')
        self.ok('prepare-review', '--reviewer', 'contradiction')
        self.answer('contradiction-01', 'I found nothing worth reporting.')
        self.ok('import-review')
        self.answer('contradiction-01', 'Still nothing.')
        out = self.ok('import-review')
        self.assertIn('failed      1 shard(s)', out)
        self.assertIn('contradiction-01', out)
        self.assertEqual(self.shard('contradiction-01')['status'], 'failed')

    def test_the_same_finding_from_two_shards_is_queued_once(self):
        self.ok('scan')
        self.ok('prepare-review', '--reviewer', 'staleness')
        self.answer('staleness-01', [judged()])
        self.answer('staleness-02', [judged()])
        out = self.ok('import-review')
        self.assertIn('1 finding(s) queued, 1 already known', out)

    def test_a_finding_already_in_the_ledger_is_suppressed(self):
        self.ok('scan')
        self.ok('prepare-review', '--reviewer', 'staleness')
        self.answer('staleness-01', [judged()])
        self.ok('import-review')
        judged_id = [e for e in self.run_state()['entries'].values()
                     if e['layer'] == 'judged'][0]['short']
        self.ok('decide', judged_id, 'intentional', '--reason', 'both are right')
        self.ok('scan')
        self.ok('prepare-review', '--reviewer', 'staleness')
        self.answer('staleness-01', [judged()])
        self.assertIn('1 suppressed by prior decisions', self.ok('import-review'))

    def test_low_confidence_from_a_cheaper_model_is_held_for_opus(self):
        self.ok('scan')
        self.ok('prepare-review', '--reviewer', 'convention')  # sonnet
        self.answer('convention-01', [judged(confidence='low')])
        self.assertIn('held for opus', self.ok('import-review'))
        wave = self.ok('wave', '--size', '5')
        escalations = [s for s in self.manifest()['shards'] if s['kind'] == 'escalate']
        self.assertEqual(len(escalations), 1)
        self.assertIn(f"{escalations[0]['id']:<18} model opus", wave)
        self.answer(escalations[0]['id'], [judged(confidence='high')])
        self.assertIn('1 finding(s) queued', self.ok('import-review'))
        statuses = sorted(e['status'] for e in self.run_state()['entries'].values()
                          if e['layer'] == 'judged')
        self.assertEqual(statuses, ['queued'])

    def test_a_failed_escalation_does_not_lose_the_finding(self):
        # The scheduled job keeps a finding whose escalation failed, at the
        # confidence its reviewer gave. Held forever, it would never surface.
        self.ok('scan')
        self.ok('prepare-review', '--reviewer', 'convention')  # sonnet
        self.answer('convention-01', [judged(confidence='low')])
        self.ok('import-review')
        (escalation,) = [s for s in self.manifest()['shards'] if s['kind'] == 'escalate']
        self.assertIn(escalation['prompt'], self.ok('wave', '--size', '5'))
        with open(escalation['prompt'], encoding='utf-8') as fh:
            self.assertIn(ci_dispatch.escalation_prompt(judged(confidence='low'))[:80],
                          fh.read())
        for reply in ('not json', 'still not json'):
            self.answer(escalation['id'], reply)
            out = self.ok('import-review')
        self.assertIn('released    1 held finding(s)', out)
        (entry,) = [e for e in self.run_state()['entries'].values() if e['layer'] == 'judged']
        self.assertEqual((entry['status'], entry['finding']['confidence']), ('queued', 'low'))

    def test_the_cheap_profile_never_escalates(self):
        self.ok('scan')
        self.ok('prepare-review', '--profile', 'cheap', '--reviewer', 'convention')
        self.answer('convention-01', [judged(confidence='low')])
        out = self.ok('import-review')
        self.assertNotIn('held for opus', out)
        self.assertIn('1 finding(s) queued', out)


class TestOptions(unittest.TestCase):
    """The recommendation rules a program can apply, applied by one."""

    def advice(self, **changes) -> str:
        finding = dict(judged(), **changes)
        finding['id'] = 'sha256:' + '0' * 64
        finding['source'] = 'reviewer:contradiction'
        return dovetail.options_for(dovetail._entry(finding, model='opus'))[1]

    def test_a_named_side_at_high_confidence_may_be_recommended(self):
        advice = self.advice(ssot_direction='b')
        self.assertTrue(advice.startswith('allowed'))
        self.assertIn('docs/config.md is authoritative and README.md changes', advice)

    def test_an_uncertain_side_or_a_doubtful_reviewer_may_not(self):
        self.assertTrue(self.advice(ssot_direction='uncertain').startswith('none'))
        self.assertTrue(self.advice(ssot_direction='a', confidence='medium').startswith('none'))

    def test_an_exact_finding_without_a_computed_fix_is_not_mechanical(self):
        finding = {'id': 'sha256:' + '1' * 64, 'source': 'graph', 'category': 'orphan',
                   'problem': 'p', 'evidence': [], 'suggestion': 's', 'severity': 'low',
                   'fix': {'kind': 'none'}}
        options, advice = dovetail.options_for(dovetail._entry(finding))
        self.assertTrue(advice.startswith('none'))
        self.assertTrue(options[0].startswith('Fix it'))


class TestContextBudget(unittest.TestCase):
    """Everything a run puts into context before the first question box."""

    @classmethod
    def setUpClass(cls):
        cls._repo = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        cls._home = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        build_large_repo(cls._repo.name)
        env = dict(os.environ, DOVETAIL_HOME=cls._home.name, **GIT_ENV)
        cls.outputs = {}
        for verb in ('scan', 'prepare-review', 'wave', 'next'):
            result = subprocess.run(
                [sys.executable, DRIVER, verb, '--repo', cls._repo.name],
                capture_output=True, text=True, env=env)
            assert result.returncode == 0, result.stderr
            cls.outputs[verb] = result.stdout
        runs = os.path.join(cls._home.name, 'runs')
        (name,) = os.listdir(runs)
        cls.state_bytes = os.path.getsize(os.path.join(runs, name, 'run.json'))

    @classmethod
    def tearDownClass(cls):
        cls._repo.cleanup()
        cls._home.cleanup()

    def test_the_fixture_is_the_size_the_claim_is_about(self):
        files = git(self._repo.name, 'ls-files').splitlines()
        self.assertGreaterEqual(len(files), 880)
        self.assertIn('contradiction', self.outputs['prepare-review'])

    def test_the_first_question_costs_under_ten_thousand_tokens(self):
        with open(SKILL, encoding='utf-8') as fh:
            skill = fh.read()
        spent = len(skill.encode('utf-8')) + sum(
            len(text.encode('utf-8')) for text in self.outputs.values())
        self.assertLess(spent, CONTEXT_BUDGET_BYTES)

    def test_the_findings_stay_on_disk(self):
        printed = sum(len(text) for text in self.outputs.values())
        # What reading the scan would have cost, against what was printed.
        self.assertGreater(self.state_bytes, 50 * printed)


class TestSkillInstructions(unittest.TestCase):
    """SKILL.md must never send the agent to read the full scan or clusters."""

    def setUp(self):
        with open(SKILL, encoding='utf-8') as fh:
            self.text = fh.read()

    def test_no_full_scan_or_cluster_dump(self):
        self.assertNotIn('--format json', self.text)
        self.assertNotIn('build_clusters', self.text)
        self.assertNotIn('Read the JSON', self.text)

    def test_no_python_c_with_values_pasted_into_code(self):
        self.assertNotIn('python3 -c', self.text)

    def test_every_verb_it_calls_exists(self):
        verbs = set(re.findall(r'dovetail\.py ([a-z-]+)', self.text))
        self.assertTrue(verbs)
        help_text = subprocess.run([sys.executable, DRIVER, '--help'],
                                   capture_output=True, text=True).stdout
        for verb in verbs:
            self.assertIn(verb, help_text)


if __name__ == '__main__':
    unittest.main()
