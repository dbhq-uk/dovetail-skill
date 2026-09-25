#!/usr/bin/env python3
"""The CI templates users copy into their own repositories.

Three faults, each of which ran silently in someone else's build:

- the scheduled job always passed `--profile default`, so `profile` in
  `.dovetail/config.toml` never applied on the cron
- `run_claude` never passed `--effort`, so every reviewer ran at the CLI's
  default whatever the roster said
- both templates used actions by tag and checked out dovetail's moving
  `main`, so a user's CI ran whatever dovetail's `main` held that day

The judgement step is run here for real, with bash, against a stand-in
`claude` that records its arguments. No model is called.
"""

from __future__ import annotations

import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.dirname(HERE)
REPO_ROOT = os.path.dirname(os.path.dirname(SKILL_DIR))
CI_DIR = os.path.join(SKILL_DIR, 'ci')
TEMPLATES = ('dovetail-pr.yml', 'dovetail-scheduled.yml')
sys.path.insert(0, os.path.join(SKILL_DIR, 'scripts'))

import ci_dispatch  # noqa: E402

GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e'}

FAKE_CLAUDE = '''#!{python}
import json, os, sys
sys.stdin.read()
with open(os.environ['CLAUDE_LOG'], 'a') as fh:
    fh.write(json.dumps(sys.argv[1:]) + '\\n')
print('[]')
'''


def template(name: str) -> str:
    with open(os.path.join(CI_DIR, name), encoding='utf-8') as fh:
        return fh.read()


def step(text: str, name: str) -> tuple[dict[str, str], str]:
    """The `env:` mapping and the `run:` script of one named step.

    A plain reading of the indentation, because PyYAML is not installed where
    the tests run.
    """
    lines = text.split('\n')
    start = next(i for i, line in enumerate(lines) if line.strip() == f'- name: {name}')
    indent = len(lines[start]) - len(lines[start].lstrip())
    body = []
    for line in lines[start + 1:]:
        if line.strip().startswith('- ') and len(line) - len(line.lstrip()) == indent:
            break
        body.append(line)
    env: dict[str, str] = {}
    script: list[str] = []
    mode = None
    for line in body:
        stripped = line.strip()
        depth = len(line) - len(line.lstrip())
        if depth == indent + 2 and stripped:
            mode = {'env:': 'env', 'run: |': 'run'}.get(stripped)
            continue
        if mode == 'env' and stripped and not stripped.startswith('#'):
            key, _, value = stripped.partition(':')
            env[key] = value.strip()
        elif mode == 'run':
            script.append(line[indent + 4:])
    return env, '\n'.join(script).strip() + '\n'


def evaluate(value: str, inputs: dict[str, str]) -> str:
    """The two expressions the judgement step's env uses."""
    match = re.fullmatch(r'\$\{\{\s*(\w+)\.(\w+)\s*\}\}', value)
    if not match:
        return value
    kind, key = match.groups()
    return inputs.get(key, '') if kind == 'inputs' else 'a-token'


class JudgementStep(unittest.TestCase):
    """The scheduled job's judgement step, run by bash as Actions runs it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        base = self._tmp.name
        self.repo = os.path.join(base, 'repo')
        os.makedirs(os.path.join(self.repo, 'docs'))
        subprocess.run(['git', 'init', '-q', '-b', 'main'], cwd=self.repo, check=True)
        for rel, text in (('README.md', '# Project\n\nSee [the guide](docs/guide.md).\n'),
                          ('docs/guide.md', '# Guide\n\nRequests time out after 30s.\n'),
                          ('.dovetail/config.toml', 'profile = "cheap"\n')):
            os.makedirs(os.path.dirname(os.path.join(self.repo, rel)), exist_ok=True)
            with open(os.path.join(self.repo, rel), 'w', encoding='utf-8') as fh:
                fh.write(text)
        subprocess.run(['git', 'add', '-A'], cwd=self.repo, check=True)
        subprocess.run(['git', 'commit', '-qm', 'fixture'], cwd=self.repo, check=True,
                       env=dict(os.environ, **GIT_ENV))
        # Where the template checks dovetail out, kept out of the scan.
        os.symlink(REPO_ROOT, os.path.join(self.repo, '.dovetail-skill'))
        with open(os.path.join(self.repo, '.git', 'info', 'exclude'), 'a') as fh:
            fh.write('.dovetail-skill\njudged.json\n')

        self.bin = os.path.join(base, 'bin')
        os.makedirs(self.bin)
        claude = os.path.join(self.bin, 'claude')
        with open(claude, 'w', encoding='utf-8') as fh:
            fh.write(FAKE_CLAUDE.format(python=sys.executable))
        os.chmod(claude, os.stat(claude).st_mode | stat.S_IEXEC)
        os.symlink(sys.executable, os.path.join(self.bin, 'python'))
        self.log = os.path.join(base, 'claude.log')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def run_step(self, **inputs: str) -> tuple[dict, list[list[str]]]:
        env_map, script = step(template('dovetail-scheduled.yml'), 'Judgement layer')
        env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ['PATH'],
                   CLAUDE_LOG=self.log)
        env.update({key: evaluate(value, inputs) for key, value in env_map.items()})
        result = subprocess.run(['bash', '--noprofile', '--norc', '-eo', 'pipefail', '-c',
                                 script], cwd=self.repo, env=env,
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        with open(os.path.join(self.repo, 'judged.json'), encoding='utf-8') as fh:
            judged = json.load(fh)
        with open(self.log, encoding='utf-8') as fh:
            calls = [json.loads(line) for line in fh]
        return judged, calls

    @staticmethod
    def flag(call: list[str], name: str) -> str:
        return call[call.index(name) + 1]

    def test_the_run_script_interpolates_no_expression(self):
        # An expression inside `run:` is pasted into the script before bash
        # sees it. Everything the step needs arrives through env instead.
        _, script = step(template('dovetail-scheduled.yml'), 'Judgement layer')
        self.assertNotIn('${{', script)

    def test_a_scheduled_run_uses_the_profile_in_config(self):
        judged, calls = self.run_step()  # a cron run has no inputs
        self.assertEqual(judged['profile'], 'cheap')
        self.assertTrue(calls)
        # cheap drops every reviewer a tier, so nothing runs on opus.
        self.assertNotIn('opus', {self.flag(call, '--model') for call in calls})

    def test_choosing_config_by_hand_also_uses_the_profile_in_config(self):
        judged, _ = self.run_step(profile='config')
        self.assertEqual(judged['profile'], 'cheap')

    def test_a_profile_picked_by_hand_wins(self):
        judged, calls = self.run_step(profile='thorough')
        self.assertEqual(judged['profile'], 'thorough')
        self.assertEqual({self.flag(call, '--model') for call in calls}, {'opus'})

    def test_every_call_passes_the_reviewers_effort(self):
        _, calls = self.run_step()
        self.assertEqual({self.flag(call, '--effort') for call in calls}, {'low', 'medium'})


class RunClaude(unittest.TestCase):
    def test_run_claude_passes_effort(self):
        with mock.patch.object(ci_dispatch.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 0, '[]', '')
            ci_dispatch.run_claude('prompt', 'sonnet', '/tmp', effort='medium')
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index('--effort') + 1], 'medium')
        self.assertEqual(argv[argv.index('--model') + 1], 'sonnet')

    def test_dispatch_hands_each_shard_its_roster_effort(self):
        with tempfile.TemporaryDirectory() as repo:
            subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
            with open(os.path.join(repo, 'README.md'), 'w', encoding='utf-8') as fh:
                fh.write('# Project\n')
            sent = []

            def fake(prompt, model, repo_root, timeout=0, effort=None):
                sent.append((model, effort))
                return '[]'

            with mock.patch.object(ci_dispatch, 'run_claude', fake):
                ci_dispatch.dispatch(repo, only=['xref', 'staleness'])
        self.assertEqual(sorted(sent), [('haiku', 'low'), ('opus', 'high')])


class Pinning(unittest.TestCase):
    def test_no_template_uses_an_action_by_tag(self):
        for name in TEMPLATES:
            for use in re.findall(r'uses:\s*(\S+)', template(name)):
                with self.subTest(template=name, uses=use):
                    self.assertRegex(use, r'@[0-9a-f]{40}$')

    def test_both_templates_check_out_dovetail_at_a_fixed_commit(self):
        for name in TEMPLATES:
            text = template(name)
            block = text[text.index('repository: dbhq-uk/dovetail-skill'):]
            block = block[:block.index('path:')]
            with self.subTest(template=name):
                self.assertRegex(block, r'ref: [0-9a-f]{40}\n')

    def test_the_pr_template_can_only_read(self):
        self.assertIn('\npermissions:\n  contents: read\n', template('dovetail-pr.yml'))


if __name__ == '__main__':
    unittest.main()
