"""Proven and heuristic findings, and the false positives that made the split necessary.

The pull-request job promised that exact findings are certain enough to fail
a build on. On a real repository several were not: checks that report likely
problems (an orphan, a missing path, two files that stopped changing
together) went red as often on intent as on drift, and a few checks that are
meant to be certain reported things that were fine. A gate that goes red on
noise gets removed. Each fixture here is one of those cases, and each failed
before this change.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, '..', 'scripts')
SCAN = os.path.join(SCRIPTS, 'scan.py')
sys.path.insert(0, SCRIPTS)

import cochange  # noqa: E402
import convcheck  # noqa: E402
import exactcheck  # noqa: E402
import graphcheck  # noqa: E402
from config import HEURISTIC_CHECKS  # noqa: E402
from scan import run_scan  # noqa: E402

GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e'}


def git(repo: str, *args: str) -> None:
    subprocess.run(['git', *args], cwd=repo, env=dict(os.environ, **GIT_ENV),
                   check=True, capture_output=True)


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


class Repo(unittest.TestCase):
    FILES: dict[str, str] = {}
    SYMLINKS: dict[str, str] = {}

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = self._tmp.name
        git(self.repo, 'init', '-q', '-b', 'main')
        for rel, text in self.FILES.items():
            write(self.repo, rel, text)
        for link, target in self.SYMLINKS.items():
            os.symlink(target, os.path.join(self.repo, link))
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'fixture')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def findings(self, category: str | None = None) -> list[dict]:
        found = run_scan(self.repo)['findings']
        return [f for f in found if category is None or f['category'] == category]

    def broken_targets(self) -> list[str]:
        return sorted(e['quote'].removeprefix('link target: ')
                      for f in self.findings('broken_link') for e in f['evidence'])

    def cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, SCAN, self.repo, *args],
                              capture_output=True, text=True)


class TestSymlinks(Repo):
    FILES = {
        'README.md': '# Project\n\nRead [the agent notes](AGENTS.md#setup) '
                     'and [this](AGENTS.md#no-such-heading).\n',
        'CLAUDE.md': '# Notes\n\n## Setup\n\nSee [gone](gone.md).\n',
    }
    SYMLINKS = {'AGENTS.md': 'CLAUDE.md'}

    def test_a_symlink_is_not_a_duplicate_of_its_target(self):
        self.assertEqual(self.findings('duplicate'), [])
        self.assertEqual(self.findings('near_duplicate'), [])

    def test_a_finding_in_the_target_is_reported_once(self):
        broken = self.findings('broken_link')
        self.assertEqual(len(broken), 1, broken)
        self.assertEqual(broken[0]['evidence'][0]['file'], 'CLAUDE.md')

    def test_a_link_to_the_symlink_uses_the_targets_anchors(self):
        dangling = self.findings('dangling_anchor')
        self.assertEqual([f['evidence'][0]['quote'] for f in dangling],
                         ['anchor: #no-such-heading'])

    def test_a_link_to_the_symlink_keeps_the_target_from_being_an_orphan(self):
        orphans = {e['file'] for f in self.findings('orphan') for e in f['evidence']}
        self.assertNotIn('CLAUDE.md', orphans)


class TestLinksThatAreNotLinks(Repo):
    FILES = {
        'README.md': (
            '# Project\n\n'
            'Write a link as `[text](path/to/file.md)`, or with an image as '
            '``![alt](img/none.png)``.\n\n'
            'HTML works too: `<img src="nowhere.png">`.\n'),
    }

    def test_a_link_inside_inline_code_is_an_example(self):
        self.assertEqual(self.broken_targets(), [])


class TestCodeSpanMasking(unittest.TestCase):
    def test_spans_are_blanked_and_offsets_kept(self):
        from refgraph import _mask_code_spans
        text = 'a `b` c ``d ` e`` f `g'
        masked = _mask_code_spans(text)
        self.assertEqual(len(masked), len(text))
        self.assertEqual(masked.split(), ['a', 'c', 'f', '`g'])
        self.assertEqual(_mask_code_spans('```'), '```')

    def test_a_long_line_of_backticks_is_fast(self):
        # A regex that searched for "the next run of the same length" took
        # minutes on a line like these.
        import time
        from refgraph import _mask_code_spans
        for line in ('`' * 50_000, 'a`' * 50_000, '``x' * 20_000,
                     ''.join('`' * k + 'x' for k in range(1, 400))):
            started = time.monotonic()
            _mask_code_spans(line)
            self.assertLess(time.monotonic() - started, 2.0)


class TestLinksThatWereMissed(Repo):
    FILES = {
        'README.md': (
            '# Project\n\n'
            'See [the configuration\n'
            'guide](docs/missing-guide.md) for details.\n\n'
            '[![build](badges/missing.svg)](MISSING.md)\n\n'
            'A [link that is\n'
            'fine](README.md) wraps too.\n'),
    }

    def test_a_link_whose_text_wraps_is_read(self):
        broken = [f for f in self.findings('broken_link')
                  if 'docs/missing-guide.md' in f['problem']]
        self.assertEqual(len(broken), 1)
        self.assertEqual(broken[0]['evidence'][0]['line'], 4)  # where the target is

    def test_a_badge_that_links_somewhere_is_read_as_both_links(self):
        self.assertIn('MISSING.md', self.broken_targets())
        self.assertIn('badges/missing.svg', self.broken_targets())

    def test_a_wrapped_link_that_resolves_is_not_reported(self):
        self.assertEqual(self.broken_targets(),
                         ['MISSING.md', 'badges/missing.svg', 'docs/missing-guide.md'])


class TestIndentedSnippets(Repo):
    FILES = {
        'README.md': (
            '# Project\n\nOne method of the class:\n\n'
            '```python\n'
            '    def handler(self, value):\n'
            '        return value + 1\n'
            '```\n\n'
            '- In a list:\n\n'
            '  ```python\n'
            '      def other(self):\n'
            '          return 2\n'
            '  ```\n'),
    }

    def test_an_indented_method_parses(self):
        self.assertEqual(self.findings('parse_error'), [])


class TestAbsoluteLocalPaths(Repo):
    FILES = {
        'README.md': '# Project\n\nNotes: [one](notes.md).\n',
        'notes.md': (
            '# Notes\n\n'
            '- [a](/home/someone/work/a.md)\n'
            '- [b](/Users/someone/work/b.md)\n'
            '- [c](~/work/c.md)\n'
            '- [d](/home/someone/work/a.md)\n'
            '- [broken](gone.md)\n'),
    }

    def test_one_file_citing_local_paths_is_one_finding(self):
        broken = self.findings('broken_link')
        local = [f for f in broken if 'absolute paths' in f['problem']]
        self.assertEqual(len(local), 1, [f['problem'] for f in broken])
        self.assertIn('3 absolute paths', local[0]['problem'])
        self.assertEqual([e['line'] for e in local[0]['evidence']], [3, 4, 5, 6])

    def test_an_ordinary_broken_link_is_still_its_own_finding(self):
        others = [f['problem'] for f in self.findings('broken_link')
                  if 'absolute' not in f['problem']]
        self.assertEqual(others, ['notes.md links to gone.md, which does not exist.'])

    def test_a_repo_root_link_is_not_a_local_path(self):
        write(self.repo, 'home/x.md', '# X\n')
        write(self.repo, 'notes.md', '# Notes\n\n[root](/home/missing.md)\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'root dir named home')
        (finding,) = self.findings('broken_link')
        self.assertNotIn('absolute', finding['problem'])


class TestTiers(Repo):
    FILES = {
        'README.md': '# Project\n\nSee [gone](gone.md).\n',
        'lonely.md': '# Nothing links here\n',
    }

    def test_every_finding_names_its_check_and_tier(self):
        for finding in self.findings():
            expected = 'heuristic' if finding['check'] in HEURISTIC_CHECKS else 'proven'
            self.assertEqual(finding['tier'], expected, finding['check'])

    def test_the_heuristic_list_names_real_checks(self):
        names = {check.__name__ for check in (graphcheck.ALL_CHECKS + exactcheck.ALL_CHECKS
                                              + convcheck.ALL_CHECKS + cochange.ALL_CHECKS)}
        self.assertLessEqual(HEURISTIC_CHECKS, names)
        # The certain ones stay proven.
        for proven in ('broken_links', 'dangling_anchors', 'exact_duplicates',
                       'unparseable_code_blocks', 'flag_drift', 'signature_drift'):
            self.assertIn(proven, names - HEURISTIC_CHECKS)

    def test_a_heuristic_finding_does_not_fail_the_gate(self):
        write(self.repo, 'README.md', '# Project\n')
        git(self.repo, 'commit', '-qam', 'fix the link')
        tiers = {f['tier'] for f in self.findings()}
        self.assertEqual(tiers, {'heuristic'})  # the orphan
        self.assertEqual(self.cli('--fail-on', 'low').returncode, 0)

    def test_config_can_opt_a_heuristic_check_into_the_gate(self):
        write(self.repo, 'README.md', '# Project\n')
        write(self.repo, '.dovetail/config.toml', '[gate]\norphans = true\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'gate on orphans')
        proc = self.cli('--fail-on', 'low', '--format', 'github')
        self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)

    def test_a_proven_finding_still_fails_the_gate(self):
        self.assertEqual(self.cli('--fail-on', 'high').returncode, 1)

    def test_gate_refuses_a_proven_check_or_an_unknown_name(self):
        for body in ('[gate]\nbroken_links = true\n', '[gate]\norphan = true\n',
                     '[gate]\norphans = "yes"\n'):
            write(self.repo, '.dovetail/config.toml', body)
            proc = self.cli('--fail-on', 'high')
            self.assertEqual(proc.returncode, 2, body)
            self.assertIn('gate', proc.stderr)


if __name__ == '__main__':
    unittest.main()
