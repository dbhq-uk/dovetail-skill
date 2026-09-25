"""What this repository says about dovetail, held to what the code does.

The docs went on describing the first version of dovetail - deterministic
only, report-only, never touching the repository - long after it gained a
judgement layer that calls models and a triage loop that edits files. The
security page said it ran no command but git. The README showed text output
under a flag that prints JSON. These tests hold the claims that drifted.
"""

from __future__ import annotations

import ast
import glob
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, '..', '..', '..'))
SCRIPTS = os.path.join(HERE, '..', 'scripts')

# Sentences from the first version. Each was true of a scanner that never
# called a model and never wrote a file, and each is false of the skill now.
RETIRED = (
    'Deterministic only.',
    'Deterministic first, and only deterministic',
    'Deterministic: no model calls',
    'Everything here is deterministic',
    'reports; it does not fix',
    'it never modifies the repository',
    'Never modifies the target repository',
    'Fixes are printed as diffs',
    'consistent with the tool never writing to your repository',
)

# One GitHub workflow annotation, as scan.py --format github prints it.
ANNOTATION = re.compile(r'^::(warning|error|notice) file=[^,]+,line=\d+,title=[a-z_:-]+::\S')

NUMBER_WORDS = {1: 'one', 2: 'two', 3: 'three', 4: 'four', 5: 'five', 6: 'six'}


def read(rel: str) -> str:
    with open(os.path.join(ROOT, rel), encoding='utf-8') as fh:
        return fh.read()


def published_docs() -> list[str]:
    """Every document or manifest a user reads to learn what dovetail does."""
    paths = ['README.md', 'SECURITY.md', 'AGENTS.md', 'CONTRIBUTING.md',
             '.claude-plugin/plugin.json']
    paths += sorted(os.path.relpath(p, ROOT)
                    for p in glob.glob(os.path.join(ROOT, 'docs', '**', '*.md'), recursive=True))
    paths += sorted(os.path.relpath(p, ROOT)
                    for p in glob.glob(os.path.join(ROOT, 'skills', 'dovetail', '**', '*.md'),
                                       recursive=True))
    return [p for p in paths if os.path.exists(os.path.join(ROOT, p))]


def spawned_commands() -> set[str]:
    """Every executable the scripts name literally as the first word of a subprocess call."""
    names: set[str] = set()
    for path in glob.glob(os.path.join(SCRIPTS, '*.py')):
        with open(path, encoding='utf-8') as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id == 'subprocess' and node.args):
                continue
            argv = node.args[0]
            if (isinstance(argv, ast.List) and argv.elts
                    and isinstance(argv.elts[0], ast.Constant)
                    and isinstance(argv.elts[0].value, str)):
                names.add(argv.elts[0].value)
    return names


class TestNoFirstVersionClaims(unittest.TestCase):
    def test_no_document_repeats_a_retired_claim(self):
        hits = []
        for rel in published_docs():
            text = read(rel)
            hits += [f'{rel}: {phrase!r}' for phrase in RETIRED if phrase in text]
        self.assertEqual(hits, [])

    def test_the_plugin_description_names_both_layers(self):
        description = json.loads(read('.claude-plugin/plugin.json'))['description']
        self.assertIn('judgement layer', description)
        self.assertIn('model', description)
        self.assertIn('approve', description)


class TestSecurityPage(unittest.TestCase):
    def setUp(self) -> None:
        self.page = read('SECURITY.md')

    def test_every_command_the_scripts_run_is_named(self):
        commands = spawned_commands()
        # The parser must actually find them, or this test proves nothing.
        self.assertTrue({'git', 'gh', 'claude'} <= commands, commands)
        missing = [name for name in sorted(commands) if f'`{name}' not in self.page]
        self.assertEqual(missing, [])

    def test_it_says_plugins_run_repository_code(self):
        self.assertIn('`.dovetail/checks/*.py` in the scanned repository runs on every scan',
                      self.page)

    def test_it_says_the_judgement_layer_sends_content_to_a_model(self):
        self.assertIn('The judgement layer sends repository content to a model', self.page)

    def test_it_says_the_triage_loop_writes(self):
        self.assertIn('The triage loop writes to the repository, and only what you approve',
                      self.page)


class TestAgentsFile(unittest.TestCase):
    def test_every_repo_local_plugin_is_named_and_counted(self):
        agents = read('AGENTS.md')
        plugins = sorted(os.path.basename(p) for p in
                         glob.glob(os.path.join(ROOT, '.dovetail', 'checks', '*.py')))
        self.assertTrue(plugins)
        self.assertEqual([p for p in plugins if f'`{p}`' not in agents], [])
        count = re.search(r'The repo carries (\w+) `\.dovetail/checks/` plugins', agents)
        self.assertIsNotNone(count)
        self.assertEqual(count.group(1), NUMBER_WORDS.get(len(plugins), str(len(plugins))))


class TestReadmeExample(unittest.TestCase):
    COMMAND = '$ python3 skills/dovetail/scripts/scan.py . --format github'

    def example_lines(self) -> list[str]:
        lines = read('README.md').splitlines()
        start = lines.index(self.COMMAND) + 1
        end = lines.index('```', start)
        return lines[start:end]

    def test_the_example_is_in_the_format_the_command_prints(self):
        example = self.example_lines()
        self.assertTrue(example)
        for line in example:
            self.assertRegex(line, ANNOTATION)

    def test_the_pattern_matches_what_the_scan_really_prints(self):
        # Hold the pattern to real output, so it cannot pass a made-up format.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as repo:
            subprocess.run(['git', 'init', '-q'], cwd=repo, check=True)
            with open(os.path.join(repo, 'README.md'), 'w', encoding='utf-8') as fh:
                fh.write('# Fixture\n\nSee [gone](gone.md).\n')
            result = subprocess.run(
                [sys.executable, os.path.join(SCRIPTS, 'scan.py'), repo, '--format', 'github'],
                capture_output=True, text=True, check=False)
        lines = [line for line in result.stdout.splitlines() if line.startswith('::')]
        self.assertTrue(lines, result.stdout + result.stderr)
        for line in lines:
            self.assertRegex(line, ANNOTATION)


if __name__ == '__main__':
    unittest.main()
