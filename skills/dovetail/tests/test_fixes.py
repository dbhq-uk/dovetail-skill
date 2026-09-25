"""Fixes, batch eligibility and blast radius, computed by the scan.

SKILL.md orders the queue by blast radius, offers a fix when one is
mechanical, and batch-approves a class whose findings each have exactly one.
Every finding used to leave the scan with no fix and an empty blast radius, so
the agent made those up, and two runs on one repository triaged differently.
"""

from __future__ import annotations

import contextlib
import io
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
SKILL = os.path.join(HERE, '..', 'SKILL.md')
sys.path.insert(0, SCRIPTS)

import dovetail  # noqa: E402
from reviewer import validate_findings  # noqa: E402
from scan import run_scan  # noqa: E402

GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e'}


def git(repo: str, *args: str, stdin: str | None = None) -> str:
    return subprocess.run(['git', *args], cwd=repo, env=dict(os.environ, **GIT_ENV),
                          input=stdin, check=True, capture_output=True, text=True).stdout


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


class Repo(unittest.TestCase):
    FILES: dict[str, str] = {}

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = self._tmp.name
        git(self.repo, 'init', '-q', '-b', 'main')
        for rel, text in self.FILES.items():
            write(self.repo, rel, text)
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'fixture')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def finding(self, category: str, needle: str = '') -> dict:
        found = [f for f in run_scan(self.repo)['findings']
                 if f['category'] == category and needle in f['problem']]
        self.assertEqual(len(found), 1, [f['problem'] for f in found])
        return found[0]

    def apply(self, diff: str) -> None:
        git(self.repo, 'apply', '-', stdin=diff)


class TestBrokenLinkFix(Repo):
    FILES = {
        'README.md': ('# Project\n\nRead [the guide](old/guide.md#setup), '
                      'the [notes](/old/notes.md) and [the api](api.md).\n'),
        'docs/guide.md': '# Guide\n\n## Setup\n',
        'docs/notes.md': '# Notes\n',
        'docs/a/api.md': '# API A\n',
        'docs/b/api.md': '# API B\n',
    }

    def test_one_file_with_the_name_gives_a_fix_that_resolves_it(self):
        finding = self.finding('broken_link', 'old/guide.md')
        self.assertEqual(finding['fix']['kind'], 'edit')
        self.assertEqual(finding['fix']['files'], ['README.md'])
        self.assertIn('+Read [the guide](docs/guide.md#setup)', finding['fix']['diff'])
        self.assertTrue(finding['batch_eligible'])
        self.apply(finding['fix']['diff'])
        self.assertEqual([f for f in run_scan(self.repo)['findings']
                          if 'old/guide.md' in f['problem']], [])

    def test_the_diff_applies_to_crlf_and_to_a_file_without_a_final_newline(self):
        for body in ('# Notes\r\n\r\n[g](old/guide.md)\r\n', '# Notes\n\n[g](old/guide.md)'):
            with open(os.path.join(self.repo, 'notes.md'), 'w', encoding='utf-8',
                      newline='') as fh:
                fh.write(body)
            finding = self.finding('broken_link', 'notes.md links to old/guide.md')
            self.apply(finding['fix']['diff'])
            with open(os.path.join(self.repo, 'notes.md'), encoding='utf-8', newline='') as fh:
                self.assertEqual(fh.read(), body.replace('old/guide.md', 'docs/guide.md'))

    def test_a_link_from_the_root_stays_rooted(self):
        finding = self.finding('broken_link', '/old/notes.md')
        self.assertIn('[notes](/docs/notes.md)', finding['fix']['diff'])

    def test_two_files_with_the_name_is_a_choice_and_gets_no_fix(self):
        finding = self.finding('broken_link', 'api.md')
        self.assertEqual(finding['fix'], {'kind': 'none'})
        self.assertFalse(finding['batch_eligible'])

    def test_an_import_is_never_relinked(self):
        write(self.repo, 'src/app.ts', 'import { x } from "./lib/util.js";\n')
        write(self.repo, 'src/other/util.ts', 'export const x = 1;\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'ts')
        finding = self.finding('broken_link', './lib/util.js')
        self.assertEqual(finding['fix'], {'kind': 'none'})


class TestAnchorFix(Repo):
    FILES = {
        'README.md': ('# Project\n\nSee [install](docs/guide.md#instalation), '
                      '[configure](docs/guide.md#configure) and [here](#usge).\n\n## Usage\n'),
        'docs/guide.md': '# Guide\n\n## Installation\n\n## Configure A\n\n## Configure B\n',
    }

    def test_one_close_heading_gives_a_fix(self):
        finding = self.finding('dangling_anchor', '#instalation')
        self.assertIn('[install](docs/guide.md#installation)', finding['fix']['diff'])
        self.apply(finding['fix']['diff'])
        self.assertEqual([f for f in run_scan(self.repo)['findings']
                          if '#instalation' in f['problem']], [])

    def test_a_same_page_anchor_gets_a_fix(self):
        finding = self.finding('dangling_anchor', '#usge')
        self.assertIn('[here](#usage)', finding['fix']['diff'])

    def test_two_close_headings_is_a_choice_and_gets_no_fix(self):
        finding = self.finding('dangling_anchor', '#configure')
        self.assertEqual(finding['fix'], {'kind': 'none'})
        self.assertFalse(finding['batch_eligible'])


class TestFlagFix(Repo):
    FILES = {
        'run.py': ('import argparse\n'
                   'p = argparse.ArgumentParser()\n'
                   'p.add_argument("--verbose")\n'
                   'p.add_argument("--output")\n'
                   'p.add_argument("--outputs")\n'),
        'README.md': ('# Project\n\n```bash\npython3 run.py --verbos\n'
                      'python3 run.py --outpt x\n```\n'),
    }

    def test_one_close_flag_gives_a_fix(self):
        finding = self.finding('flag_drift', '--verbos`')
        self.assertIn('+python3 run.py --verbose', finding['fix']['diff'])
        self.apply(finding['fix']['diff'])
        self.assertEqual([f for f in run_scan(self.repo)['findings']
                          if '--verbos`' in f['problem']], [])

    def test_two_close_flags_is_a_choice_and_gets_no_fix(self):
        finding = self.finding('flag_drift', '--outpt')
        self.assertEqual(finding['fix'], {'kind': 'none'})


class TestBlastRadius(Repo):
    FILES = {
        'README.md': '# Project\n\n[a](docs/a.md)\n',
        'docs/b.md': '# B\n\n[a](a.md) and [b](b.md)\n',
        'docs/a.md': '# A\n\n[gone](gone.md)\n',
    }

    def test_it_is_the_files_that_cite_the_one_the_finding_is_in(self):
        finding = self.finding('broken_link', 'gone.md')
        self.assertEqual(finding['blast_radius'], ['README.md', 'docs/b.md'])

    def test_a_plugin_finding_is_never_batch_eligible(self):
        write(self.repo, '.dovetail/checks/local.py', (
            'def check(inventory, graph):\n'
            '    return [{"id": "local:x", "source": "plugin:local", "category": "convention", "problem": "p",\n'
            '             "evidence": [{"file": "docs/a.md", "line": 1, "quote": "# A"}],\n'
            '             "suggestion": "s", "severity": "low", "batch_eligible": True,\n'
            '             "fix": {"kind": "edit", "files": ["docs/a.md"], "diff": "x"}}]\n'))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'plugin')
        (finding,) = [f for f in run_scan(self.repo)['findings']
                      if f['source'].startswith('plugin:')]
        self.assertFalse(finding['batch_eligible'])

    def test_a_reviewer_finding_is_never_batch_eligible(self):
        raw = ('[{"category": "staleness", "problem": "p", "evidence": '
               '[{"file": "docs/a.md", "line": 1, "quote": "# A"}], '
               '"suggestion": "s", "severity": "low", "confidence": "high"}]')
        (finding,) = validate_findings(raw, 'staleness', self.repo)
        self.assertFalse(finding['batch_eligible'])


class TestBatchInTheDriver(Repo):
    FILES = {
        'README.md': '# Project\n\n[one](old/one.md) and [two](old/two.md)\n',
        'docs/one.md': '# One\n',
        'docs/two.md': '# Two\n',
    }

    def setUp(self) -> None:
        super().setUp()
        self._home = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)

    def tearDown(self) -> None:
        self._home.cleanup()
        super().tearDown()

    def run_driver(self, *args: str) -> tuple[int, str]:
        out = io.StringIO()
        with mock.patch.dict(os.environ, {'DOVETAIL_HOME': self._home.name}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = dovetail.main([*args, '--repo', self.repo])
        return code, out.getvalue()

    def test_next_says_the_class_can_be_batched_and_shows_it_in_one_box(self):
        self.run_driver('scan')
        _, out = self.run_driver('next')
        self.assertIn('batch      2 queued broken_link findings are batch_eligible', out)
        self.assertIn('fix        the scan computed one fix', out)
        self.assertIn(' fix --files README.md |', out)  # the record line names the file
        code, batch = self.run_driver('next', '--batch')
        self.assertEqual(code, 0)
        shown, agent = batch.split(dovetail.AGENT_LINE)
        # One diff per file: both edits land on the same line, together.
        self.assertEqual(shown.count('+++ b/README.md'), 1)
        self.assertIn('+[one](docs/one.md) and [two](docs/two.md)', shown)
        record = [line for line in agent.splitlines() if line.startswith('record')][0]
        self.assertIn(' fix --files README.md', record)
        diff = shown.split('```diff\n')[1].split('```')[0]
        git(self.repo, 'apply', '-', stdin=diff)
        self.assertEqual([f for f in run_scan(self.repo)['findings']
                          if f['category'] == 'broken_link'], [])

    def test_one_decide_records_the_whole_batch(self):
        self.run_driver('scan')
        _, batch = self.run_driver('next', '--batch')
        record = [line for line in batch.splitlines() if line.startswith('record')][0]
        ids = record.split(' decide --repo ')[1].split(' fix ')[0].split()[1:]
        self.assertEqual(len(ids), 2)
        write(self.repo, 'README.md', '# Project\n\n[one](docs/one.md) and [two](docs/two.md)\n')
        code, out = self.run_driver('decide', *ids, 'fix', '--files', 'README.md')
        self.assertEqual(code, 0, out)
        _, rescan = self.run_driver('rescan')
        self.assertNotIn('did not resolve', rescan)
        _, after = self.run_driver('next')
        self.assertIn('Queue empty', after)


class TestCombine(Repo):
    FILES = {'README.md': '# P\n\n[a](x.md) [b](y.md)\n'}

    def fix(self, old: str, new: str) -> dict:
        import fixes
        return fixes.edit_fix(self.repo, 'README.md', {3: (old, new)})

    def test_edits_to_one_line_combine_into_one_diff(self):
        import fixes
        combined = fixes.combine(self.repo, [self.fix('x.md', 'docs/x.md'),
                                             self.fix('y.md', 'docs/y.md')])
        self.assertEqual(combined['files'], ['README.md'])
        self.assertIn('+[a](docs/x.md) [b](docs/y.md)', combined['diff'])
        git(self.repo, 'apply', '-', stdin=combined['diff'])

    def test_overlapping_edits_are_not_combined(self):
        import fixes
        combined = fixes.combine(self.repo, [self.fix('x.md', 'one.md'),
                                             self.fix('x.md', 'two.md')])
        self.assertEqual(combined, {'kind': 'none'})


class TestSkillReadsTheFields(unittest.TestCase):
    def test_ordering_and_batching_name_the_fields_the_scan_fills(self):
        with open(SKILL, encoding='utf-8') as fh:
            text = fh.read()
        self.assertIn('`blast_radius`', text)
        self.assertIn('`batch_eligible`', text)
        self.assertIn('next --batch', text)


if __name__ == '__main__':
    unittest.main()
