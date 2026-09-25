#!/usr/bin/env python3
"""Plugins that can fail a build, and a scan that can leave them out.

A repository's own rules live in `.dovetail/checks/`, but their findings could
never fail `--fail-on`, so this repository's house rules never failed its CI.
Plugins are also code from the scanned repository, run on every scan, with no
way to switch that off and nothing saying it happens.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
ROOT = os.path.join(HERE, '..', '..', '..')
SCAN = os.path.join(SCRIPTS, 'scan.py')
DRIVER = os.path.join(SCRIPTS, 'dovetail.py')
sys.path.insert(0, SCRIPTS)

from config import ConfigError, load_config  # noqa: E402
from scan import exit_code, format_github, run_scan  # noqa: E402
from store import append_decision  # noqa: E402

GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e'}

LOUD = '''
def check(inventory, graph):
    return [{"id": "sha256:loud", "source": "plugin:loud", "category": "convention",
             "problem": "A house rule is broken.",
             "evidence": [{"file": "README.md", "line": 1}],
             "suggestion": "Fix it.", "severity": "high"}]
'''


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
        return fh.read()


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp()
        subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=self.repo, check=True)
        write(self.repo, 'README.md', '# Project\n')
        write(self.repo, '.dovetail/checks/loud.py', LOUD)
        subprocess.run(['git', 'add', '-A'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-qm', 'x'], cwd=self.repo, check=True,
                       env=dict(os.environ, **GIT_ENV))

    def tearDown(self) -> None:
        shutil.rmtree(self.repo, ignore_errors=True)

    def scan(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, SCAN, self.repo, *args],
                              capture_output=True, text=True)


class TestPluginGate(Case):
    def test_an_opted_in_plugin_fails_the_build(self):
        write(self.repo, '.dovetail/config.toml', '[plugins.loud]\ngate = true\n')
        result = self.scan('--format', 'github', '--fail-on', 'high')
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('::error file=README.md', result.stdout)

    def test_without_the_opt_in_it_only_warns(self):
        result = self.scan('--format', 'github', '--fail-on', 'high')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        # An error annotation on a green build says two things at once.
        self.assertIn('::warning file=README.md', result.stdout)

    def test_gate_false_is_the_same_as_no_opt_in(self):
        write(self.repo, '.dovetail/config.toml', '[plugins.loud]\ngate = false\n')
        self.assertEqual(exit_code(run_scan(self.repo), 'high'), 0)

    def test_the_result_names_the_gated_plugin(self):
        write(self.repo, '.dovetail/config.toml', '[plugins.loud]\ngate = true\n')
        result = run_scan(self.repo)
        self.assertIn('plugin:loud', result['gate'])
        self.assertIn('::error', format_github(result))

    def test_an_unknown_plugin_name_stops_the_run(self):
        write(self.repo, '.dovetail/config.toml', '[plugins.lod]\ngate = true\n')
        with self.assertRaises(ConfigError) as caught:
            load_config(self.repo)
        self.assertIn('Did you mean `loud`?', str(caught.exception))

    def test_gate_must_be_a_boolean_and_the_only_key(self):
        for body in ('[plugins.loud]\ngate = "yes"\n', '[plugins.loud]\ngates = true\n'):
            write(self.repo, '.dovetail/config.toml', body)
            with self.subTest(body=body), self.assertRaises(ConfigError):
                load_config(self.repo)


class TestNoPlugins(Case):
    def test_no_plugins_skips_the_checks_directory(self):
        # A plugin that would fail the run shows it did not run at all.
        write(self.repo, '.dovetail/checks/broken.py', 'raise RuntimeError("ran")\n')
        result = json.loads(self.scan('--no-plugins').stdout)
        self.assertEqual(result['failed_checks'], [])
        self.assertEqual([f for f in result['findings'] if f['source'].startswith('plugin:')], [])
        self.assertEqual(result['plugins_skipped'], 2)

    def test_a_full_scan_reports_none_skipped(self):
        self.assertEqual(run_scan(self.repo)['plugins_skipped'], 0)

    def test_the_run_header_says_plugins_were_skipped(self):
        home = tempfile.mkdtemp()
        try:
            out = subprocess.run([sys.executable, DRIVER, 'scan', '--repo', self.repo,
                                  '--no-plugins'], capture_output=True, text=True,
                                 env=dict(os.environ, DOVETAIL_HOME=home))
        finally:
            shutil.rmtree(home, ignore_errors=True)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn('plugins     1 in .dovetail/checks/ skipped (--no-plugins)', out.stdout)

    def test_a_skipped_plugins_decisions_are_not_called_stale(self):
        append_decision(self.repo, {'id': 'sha256:loud-old', 'verdict': 'intentional',
                                    'reason': 'r', 'at': '2026-09-25', 'summary': 's',
                                    'layer': 'exact', 'check': 'plugin:loud'})
        self.assertEqual(run_scan(self.repo, plugins=False)['stale_decisions'], [])
        # When the plugin did run and the row matches nothing, it is stale.
        self.assertEqual([r['id'] for r in run_scan(self.repo)['stale_decisions']],
                         ['sha256:loud-old'])


class TestEntryPoint(Case):
    def test_a_plugins_check_function_is_not_dead_code(self):
        dead = [f for f in run_scan(self.repo)['findings'] if f['category'] == 'dead_code']
        self.assertEqual(dead, [])


class TestDisclosure(unittest.TestCase):
    def test_security_says_plugins_run_code_from_a_pull_request(self):
        page = read('SECURITY.md')
        self.assertIn("including code from a pull request's branch", page)
        self.assertIn('--no-plugins', page)

    def test_the_pr_template_says_so_too(self):
        text = read('skills/dovetail/ci/dovetail-pr.yml')
        self.assertIn("including code from the pull request's branch", text)
        self.assertIn('--no-plugins', text)


if __name__ == '__main__':
    unittest.main()
