#!/usr/bin/env python3
"""The opt-in external URL check, through lychee.

By default nothing leaves the machine. `--external-links` runs lychee and
folds what it could not reach in as heuristic findings, which never gate.
These tests hold all of that without the network: lychee is a stand-in
script that prints a report shaped like lychee 0.24's JSON output.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
SCAN = os.path.join(SCRIPTS, 'scan.py')
DRIVER = os.path.join(SCRIPTS, 'dovetail.py')
sys.path.insert(0, SCRIPTS)

from config import ConfigError  # noqa: E402
from scan import exit_code, run_scan  # noqa: E402

GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e'}

# The parts of a lychee 0.24 `--format json` report that dovetail reads.
REPORT = {
    'total': 4, 'errors': 2, 'timeouts': 1,
    'error_map': {
        'README.md': [
            {'url': 'https://example.com/gone',
             'status': {'text': 'Rejected status code: 404 Not Found', 'code': 404},
             'span': {'line': 3, 'column': 5}},
            {'url': 'https://example.com/page#nowhere',
             'status': {'text': 'Cannot find fragment', 'details': 'Cannot find fragment'},
             'span': {'line': 5, 'column': 5}},
        ],
    },
    'timeout_map': {
        'docs/guide.md': [
            {'url': 'https://slow.example.com/', 'status': {'text': 'Timeout'},
             'span': {'line': 1, 'column': 1}},
        ],
    },
    'excluded_map': {}, 'success_map': {},
}

FAKE_LYCHEE = '''#!{python}
import json, os, sys
with open(os.environ['FAKE_LYCHEE_LOG'], 'w') as fh:
    json.dump({{'argv': sys.argv[1:], 'stdin': sys.stdin.read()}}, fh)
with open(os.environ['FAKE_LYCHEE_REPORT']) as fh:
    sys.stdout.write(fh.read())
sys.stderr.write('lychee stand-in\\n')
sys.exit(int(os.environ.get('FAKE_LYCHEE_EXIT', '2')))
'''


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.mkdtemp()
        self.repo = os.path.join(self.tmp, 'repo')
        os.makedirs(self.repo)
        subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=self.repo, check=True)
        write(self.repo, 'README.md', textwrap.dedent('''\
            # Project

            See [the site](https://example.com/gone).

            And [a section](https://example.com/page#nowhere), and the [guide](docs/guide.md).
            '''))
        write(self.repo, 'docs/guide.md', 'Slow: <https://slow.example.com/>\n')
        write(self.repo, 'notes.txt', 'https://example.com/not-markdown\n')
        subprocess.run(['git', 'add', '-A'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-qm', 'x'], cwd=self.repo, check=True,
                       env=dict(os.environ, **GIT_ENV))

        # A PATH holding git and the lychee stand-in, and nothing else that
        # could be a real lychee.
        self.bin = os.path.join(self.tmp, 'bin')
        os.makedirs(self.bin)
        os.symlink(shutil.which('git'), os.path.join(self.bin, 'git'))
        self.no_lychee = os.path.join(self.tmp, 'bin-without-lychee')
        os.makedirs(self.no_lychee)
        os.symlink(shutil.which('git'), os.path.join(self.no_lychee, 'git'))
        lychee = os.path.join(self.bin, 'lychee')
        with open(lychee, 'w', encoding='utf-8') as fh:
            fh.write(FAKE_LYCHEE.format(python=sys.executable))
        os.chmod(lychee, os.stat(lychee).st_mode | stat.S_IXUSR)

        self.log = os.path.join(self.tmp, 'lychee.log')
        self.report = os.path.join(self.tmp, 'report.json')
        self.set_report(REPORT)
        self.env = {'PATH': self.bin, 'FAKE_LYCHEE_LOG': self.log,
                    'FAKE_LYCHEE_REPORT': self.report, 'FAKE_LYCHEE_EXIT': '2'}

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_report(self, report) -> None:
        with open(self.report, 'w', encoding='utf-8') as fh:
            fh.write(report if isinstance(report, str) else json.dumps(report))

    def scan(self, **kwargs) -> dict:
        with mock.patch.dict(os.environ, self.env):
            return run_scan(self.repo, **kwargs)

    def cli(self, *args: str, env: dict | None = None) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, SCAN, self.repo, *args], capture_output=True,
                              text=True, env=dict(os.environ, **(env or self.env)))

    def external(self, result: dict) -> list[dict]:
        return [f for f in result['findings'] if f.get('check') == 'external_links']

    def lychee_ran(self) -> bool:
        return os.path.exists(self.log)


class TestOffByDefault(Case):
    def test_without_the_flag_lychee_never_runs(self):
        result = self.scan()
        self.assertFalse(self.lychee_ran())
        self.assertEqual(self.external(result), [])
        self.assertNotIn('external_links', result['timings'])


class TestWithLychee(Case):
    def test_each_unreachable_url_is_a_heuristic_broken_link(self):
        found = {f['evidence'][0]['quote']: f for f in self.external(
            self.scan(external_links=True))}
        self.assertEqual(sorted(found), ['link target: https://example.com/gone',
                                         'link target: https://example.com/page#nowhere',
                                         'link target: https://slow.example.com/'])
        gone = found['link target: https://example.com/gone']
        self.assertEqual(gone['category'], 'broken_link')
        self.assertEqual(gone['source'], 'check:external')
        self.assertEqual(gone['tier'], 'heuristic')
        self.assertEqual((gone['evidence'][0]['file'], gone['evidence'][0]['line']),
                         ('README.md', 3))
        self.assertEqual(gone['severity'], 'medium')
        self.assertIn('404', gone['problem'])
        self.assertEqual(found['link target: https://example.com/page#nowhere']['severity'],
                         'low')
        slow = found['link target: https://slow.example.com/']
        self.assertEqual((slow['evidence'][0]['file'], slow['severity']),
                         ('docs/guide.md', 'low'))

    def test_lychee_gets_the_markdown_on_stdin_and_only_web_urls(self):
        self.scan(external_links=True)
        with open(self.log, encoding='utf-8') as fh:
            call = json.load(fh)
        self.assertEqual(call['stdin'].split(), ['README.md', 'docs/guide.md'])
        argv = call['argv']
        self.assertIn('--files-from', argv)
        self.assertEqual(argv[argv.index('--format') + 1], 'json')
        schemes = [argv[i + 1] for i, arg in enumerate(argv) if arg == '--scheme']
        self.assertEqual(sorted(schemes), ['http', 'https'])

    def test_its_findings_never_gate(self):
        result = self.scan(external_links=True)
        self.assertTrue(self.external(result))
        self.assertEqual(exit_code(result, 'low'), 0)
        proc = self.cli('--external-links', '--format', 'github', '--fail-on', 'low')
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn('::warning file=README.md,line=3', proc.stdout)

    def test_the_gate_cannot_opt_it_in(self):
        write(self.repo, '.dovetail/config.toml', '[gate]\nexternal_links = true\n')
        with self.assertRaises(ConfigError):
            self.scan(external_links=True)

    def test_a_clean_report_is_no_findings(self):
        self.set_report({'error_map': {}, 'timeout_map': {}})
        self.env['FAKE_LYCHEE_EXIT'] = '0'
        result = self.scan(external_links=True)
        self.assertEqual(self.external(result), [])
        self.assertEqual(result['failed_checks'], [])


class TestFailsLoudly(Case):
    def test_asking_without_lychee_installed_exits_2(self):
        with mock.patch.dict(os.environ, dict(self.env, PATH=self.no_lychee)):
            with self.assertRaises(ValueError) as caught:
                run_scan(self.repo, external_links=True)
        self.assertIn('lychee', str(caught.exception))
        proc = self.cli('--external-links', env=dict(self.env, PATH=self.no_lychee))
        self.assertEqual(proc.returncode, 2)
        self.assertIn('--external-links needs lychee on PATH', proc.stderr)

    def test_lychee_failing_is_a_failed_check_that_fails_the_gate(self):
        self.env['FAKE_LYCHEE_EXIT'] = '1'
        result = self.scan(external_links=True)
        self.assertEqual(len(result['failed_checks']), 1)
        self.assertTrue(result['failed_checks'][0].startswith('external_links (lychee exited 1'))
        self.assertEqual(exit_code(result, 'low'), 1)

    def test_a_report_that_is_not_json_is_a_failed_check(self):
        self.set_report('Error: something went wrong')
        result = self.scan(external_links=True)
        self.assertTrue(result['failed_checks'][0].startswith('external_links (lychee printed no'))


class TestTheRunDriver(Case):
    def test_rescan_checks_the_urls_again(self):
        # rescan must scan the way scan did. Dropping the URLs on rescan would
        # report every one of them as resolved by whatever fix came first.
        env = dict(os.environ, DOVETAIL_HOME=os.path.join(self.tmp, 'runs'), **self.env)

        def run(*args: str) -> subprocess.CompletedProcess:
            return subprocess.run([sys.executable, DRIVER, *args, '--repo', self.repo],
                                  capture_output=True, text=True, env=env)

        first = run('scan', '--external-links')
        self.assertEqual(first.returncode, 0, first.stdout + first.stderr)
        self.assertIn('broken_link 3', first.stdout)
        os.remove(self.log)
        again = run('rescan')
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertTrue(self.lychee_ran())
        self.assertIn('No other queued finding resolved', again.stdout)

if __name__ == '__main__':
    unittest.main()
