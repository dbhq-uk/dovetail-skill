"""Tests for `.dovetail/config.toml` and `.dovetail/checks/` plugins."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from config import ConfigError, check_enabled, load_config  # noqa: E402
from discover import discover  # noqa: E402
from plugins import discover_plugins, run_plugins  # noqa: E402
from refgraph import build_graph  # noqa: E402
from scan import run_scan  # noqa: E402


def write(repo: str, rel: str, body: str) -> None:
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(body)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = self._tmp.name
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True)
        for key, value in (('gc.auto', '0'), ('maintenance.auto', 'false')):
            subprocess.run(['git', '-C', self.repo, 'config', key, value],
                           check=True, capture_output=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def scan_inputs(self):
        inventory = discover(self.repo)
        return inventory, build_graph(self.repo, inventory)


class Config(Base):
    def test_absent_config_gives_defaults(self):
        config = load_config(self.repo)
        self.assertEqual(config['profile'], 'default')
        self.assertEqual(config['ignore'], [])

    def test_ignore_and_profile_are_read(self):
        write(self.repo, '.dovetail/config.toml',
              'ignore = ["vendor/**"]\nprofile = "thorough"\n')
        config = load_config(self.repo)
        self.assertEqual(config['ignore'], ['vendor/**'])
        self.assertEqual(config['profile'], 'thorough')

    def test_malformed_toml_raises(self):
        # Silently ignoring a broken config would hide findings the user
        # explicitly asked to see, or show ones they muted.
        write(self.repo, '.dovetail/config.toml', 'ignore = [\n')
        with self.assertRaises(ConfigError):
            load_config(self.repo)

    def test_unknown_profile_raises(self):
        write(self.repo, '.dovetail/config.toml', 'profile = "turbo"\n')
        with self.assertRaises(ConfigError):
            load_config(self.repo)

    def test_wrongly_typed_ignore_raises(self):
        write(self.repo, '.dovetail/config.toml', 'ignore = "vendor/**"\n')
        with self.assertRaises(ConfigError):
            load_config(self.repo)

    def test_check_can_be_disabled(self):
        write(self.repo, '.dovetail/config.toml', '[checks]\norphans = false\n')
        config = load_config(self.repo)
        self.assertFalse(check_enabled(config, 'orphans'))
        self.assertTrue(check_enabled(config, 'broken_links'))

    def test_disabling_a_check_removes_its_findings(self):
        write(self.repo, 'docs/stray.md', 'nothing links here\n')
        write(self.repo, '.dovetail/config.toml', '[checks]\norphans = false\n')
        subprocess.run(['git', '-C', self.repo, 'add', '-A'], check=True,
                       capture_output=True)
        result = run_scan(self.repo)
        self.assertEqual(
            [f for f in result['findings'] if f['category'] == 'orphan'], [])

    def test_ignore_glob_is_applied_by_the_scan(self):
        write(self.repo, 'vendor/stray.md', 'orphan\n')
        write(self.repo, '.dovetail/config.toml', 'ignore = ["vendor/**"]\n')
        result = run_scan(self.repo)
        files = {e['file'] for f in result['findings'] for e in f['evidence']}
        self.assertNotIn('vendor/stray.md', files)


class ConfigNames(Base):
    """A name that exists nowhere must stop the run, not silently do nothing."""

    SCAN = os.path.join(os.path.dirname(__file__), '..', 'scripts', 'scan.py')

    def rejected(self, body: str) -> str:
        write(self.repo, '.dovetail/config.toml', body)
        with self.assertRaises(ConfigError) as caught:
            load_config(self.repo)
        return str(caught.exception)

    def test_the_review_reproduction_exits_2_and_names_the_typo(self):
        write(self.repo, 'README.md', '# Project\n')
        write(self.repo, '.dovetail/config.toml',
              '[checks]\nstale_todo = false\n\n'
              '[reviewers.contradiction]\nmodel = "gpt-9"\neffort = "extreme"\n')
        result = subprocess.run([sys.executable, self.SCAN, self.repo, '--format', 'json'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 2, result.stdout[:200])
        self.assertIn('`stale_todo`', result.stderr)
        self.assertIn('Did you mean `stale_todos`?', result.stderr)

    def test_an_unknown_check_is_rejected_with_the_nearest_name(self):
        message = self.rejected('[checks]\nstale_todo = false\n')
        self.assertIn('`stale_todo` is not a check', message)
        self.assertIn('Did you mean `stale_todos`?', message)

    def test_an_unknown_reviewer_is_rejected(self):
        message = self.rejected('[reviewers.contradictions]\nenabled = false\n')
        self.assertIn('`contradictions` is not a reviewer', message)
        self.assertIn('Did you mean `contradiction`?', message)

    def test_an_unknown_model_is_rejected(self):
        message = self.rejected('[reviewers.contradiction]\nmodel = "gpt-9"\n')
        self.assertIn('reviewers.contradiction.model', message)
        self.assertIn("'gpt-9'", message)

    def test_an_unknown_effort_is_rejected(self):
        message = self.rejected('[reviewers.staleness]\neffort = "extreme"\n')
        self.assertIn('reviewers.staleness.effort', message)
        self.assertIn("'extreme'", message)

    def test_a_misspelled_reviewer_setting_is_rejected(self):
        message = self.rejected('[reviewers.staleness]\nmodle = "opus"\n')
        self.assertIn('Did you mean `model`?', message)

    def test_enabled_must_be_a_boolean(self):
        message = self.rejected('[reviewers.xref]\nenabled = "no"\n')
        self.assertIn('reviewers.xref.enabled', message)

    def test_an_unknown_top_level_setting_is_rejected(self):
        message = self.rejected('profiles = "cheap"\n')
        self.assertIn('Did you mean `profile`?', message)

    def test_an_unknown_gate_name_is_rejected_with_the_nearest_name(self):
        message = self.rejected('[gate]\norphan = true\n')
        self.assertIn('Did you mean `orphans`?', message)

    def test_every_valid_name_is_accepted(self):
        from config import known_checks, known_reviewers
        checks = ''.join(f'{name} = true\n' for name in known_checks())
        reviewers = ''.join(f'[reviewers.{name}]\nenabled = true\nmodel = "opus"\n'
                            f'effort = "low"\n' for name in known_reviewers())
        write(self.repo, '.dovetail/config.toml',
              f'profile = "cheap"\nignore = []\n[checks]\n{checks}{reviewers}')
        self.assertEqual(load_config(self.repo)['profile'], 'cheap')

    def test_the_reference_lists_every_check(self):
        from config import known_checks
        with open(os.path.join(os.path.dirname(__file__), '..', 'references', 'config.md'),
                  encoding='utf-8') as fh:
            reference = fh.read()
        for name in known_checks():
            self.assertIn(f'| `{name}` |', reference)


PLUGIN_OK = '''
def check(inventory, graph):
    return [{
        'id': 'sha256:test',
        'source': 'plugin:x',
        'category': 'convention',
        'problem': 'a repo-specific rule was broken',
        'evidence': [{'file': 'README.md', 'line': 1, 'quote': 'x'}],
        'suggestion': 'fix it',
        'severity': 'medium',
    }]
'''


class Plugins(Base):
    def test_plugin_findings_are_collected(self):
        write(self.repo, 'README.md', 'x\n')
        write(self.repo, '.dovetail/checks/rule.py', PLUGIN_OK)
        results = run_plugins(self.repo, *self.scan_inputs())
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0].error)
        self.assertEqual(results[0].findings[0]['source'], 'plugin:rule')

    def test_raising_plugin_is_named_not_fatal(self):
        write(self.repo, '.dovetail/checks/bad.py',
              'def check(inventory, graph):\n    raise RuntimeError("boom")\n')
        results = run_plugins(self.repo, *self.scan_inputs())
        self.assertEqual(results[0].findings, [])
        self.assertIn('boom', results[0].error)

    def test_plugin_without_check_is_an_error(self):
        write(self.repo, '.dovetail/checks/empty.py', 'x = 1\n')
        results = run_plugins(self.repo, *self.scan_inputs())
        self.assertIn('check', results[0].error)

    def test_malformed_finding_is_rejected(self):
        # Junk must fail as that plugin, not corrupt the run or get reported
        # to the user as a real defect in their repository.
        write(self.repo, '.dovetail/checks/junk.py',
              'def check(inventory, graph):\n    return [{"problem": "no keys"}]\n')
        results = run_plugins(self.repo, *self.scan_inputs())
        self.assertEqual(results[0].findings, [])
        self.assertIsNotNone(results[0].error)

    def test_underscore_modules_are_skipped(self):
        write(self.repo, '.dovetail/checks/_helper.py', 'x = 1\n')
        self.assertEqual(discover_plugins(self.repo), [])

    def test_plugin_failure_surfaces_in_the_scan(self):
        write(self.repo, '.dovetail/checks/bad.py',
              'def check(inventory, graph):\n    raise RuntimeError("boom")\n')
        result = run_scan(self.repo)
        self.assertTrue(any('plugin:bad' in name
                            for name in result['failed_checks']))


if __name__ == '__main__':
    unittest.main()
