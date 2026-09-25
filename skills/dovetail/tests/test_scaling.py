#!/usr/bin/env python3
"""Scan time grows with the repository, not with its square.

Two checks compared every file with every other file: `near_duplicates` over
every pair, and `dead_python_code` with one search per definition per file.
On 1,000 generated files they took over two minutes together, and 3,000 did
not finish. Each file was also read about eight times, once per check that
needed its text.

These tests hold the fix three ways: the candidate pairs are exactly the ones
the old loop could have reported, the dead-code index answers exactly as the
old search did, and doubling the repository roughly doubles the time.
"""

from __future__ import annotations

import builtins
import itertools
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import cochange  # noqa: E402
import convcheck  # noqa: E402
import exactcheck  # noqa: E402
import graphcheck  # noqa: E402
import textcache  # noqa: E402
from discover import discover  # noqa: E402
from refgraph import build_graph  # noqa: E402
from scan import run_scan  # noqa: E402

GIT_ENV = {'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@e',
           'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@e'}


def git(repo: str, *args: str) -> None:
    subprocess.run(['git', *args], cwd=repo, env=dict(os.environ, **GIT_ENV),
                   check=True, capture_output=True)


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full) or repo, exist_ok=True)
    with open(full, 'w', encoding='utf-8', newline='') as fh:
        fh.write(text)


def generate(root: str, count: int) -> None:
    """A repository of `count` files: two thirds prose, one third Python.

    Every 25th document is a near copy of the one before, and each module
    defines public functions and classes that nothing else names.
    """
    rng = random.Random(7)
    vocab = [f'w{i}' for i in range(3000)]
    git(root, 'init', '-q', '-b', 'main')
    previous = ''
    for i in range(count):
        if i % 3 == 2:
            body = f'"""Module {i}."""\n\n' + ''.join(
                f'def func_{i}_{j}(x):\n    return x + {j}\n\n\n'
                f'class Thing{i}_{j}:\n    pass\n\n\n' for j in range(4))
            write(root, f'src/area{i % 20}/mod{i}.py', body + f'VALUE = func_{i}_0(1)\n')
            continue
        body = f'# Page {i}\n\n' + '\n\n'.join(
            ' '.join(rng.choice(vocab) for _ in range(60)) for _ in range(5)) + '\n'
        if i % 25 == 1 and previous:
            body = previous.replace('w1 ', 'w2 ', 1) + ' extra\n'
        write(root, f'docs/area{i % 20}/page{i}.md', body)
        previous = body
    git(root, 'add', '-A')
    git(root, 'commit', '-qm', 'generated')


def brute_force_pairs(sets: dict[str, frozenset], floor: float) -> set[tuple[str, str]]:
    pairs = set()
    for left, right in itertools.combinations(sorted(sets), 2):
        union = len(sets[left] | sets[right])
        if union and len(sets[left] & sets[right]) / union >= floor:
            pairs.add((left, right))
    return pairs


class TestCandidatePairs(unittest.TestCase):
    """Prefix filtering must never drop a pair the old loop would have kept."""

    def test_every_pair_at_or_above_the_floor_is_found(self):
        for seed, floor in itertools.product(range(12), (0.3, 0.5, 0.8, 0.95)):
            rng = random.Random(seed)
            universe = list(range(rng.randint(5, 60)))
            sets = {}
            for n in range(rng.randint(2, 40)):
                size = rng.randint(1, len(universe))
                sets[f'f{n:02d}'] = frozenset(rng.sample(universe, size))
            with self.subTest(seed=seed, floor=floor):
                found = graphcheck.candidate_pairs(sets, floor)
                self.assertLessEqual(brute_force_pairs(sets, floor), found)
                self.assertTrue(all(left < right for left, right in found))

    def test_the_floor_is_not_rounded_up_by_float_error(self):
        # 0.3 * 10 is 3.0000000000000004 in floating point. Rounding that up
        # to 4 shortens the prefix by one and loses this pair, whose Jaccard
        # is exactly 0.3.
        shared = set(range(3))
        sets = {'a': frozenset(shared | set(range(10, 17))),
                'b': frozenset(shared)}
        self.assertEqual(brute_force_pairs(sets, 0.3), {('a', 'b')})
        self.assertIn(('a', 'b'), graphcheck.candidate_pairs(sets, 0.3))

    def test_unrelated_sets_are_never_paired(self):
        sets = {f'f{i}': frozenset(range(i * 100, i * 100 + 50)) for i in range(30)}
        self.assertEqual(graphcheck.candidate_pairs(sets, 0.3), set())


class TestDeadCodeIndex(unittest.TestCase):
    """The word index gives the same answer as one search per name per file."""

    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp()
        git(self.repo, 'init', '-q', '-b', 'main')

    def tearDown(self) -> None:
        shutil.rmtree(self.repo, ignore_errors=True)

    def dead(self) -> set[str]:
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'x')
        inventory = discover(self.repo)
        found = exactcheck.dead_python_code(inventory, build_graph(self.repo, inventory))
        return {f['problem'].split('`')[1] for f in found}

    def test_the_old_and_new_answers_agree_on_awkward_cases(self):
        write(self.repo, 'lib.py', (
            'def only_defined():\n    pass\n\n\n'
            'def used_below():\n    pass\n\n\n'
            'async def async_used():\n    pass\n\n\n'
            'class Shadowed:\n    def Shadowed(self):\n        pass\n\n\n'
            'def prefix():\n    pass\n\n\n'
            'def named_in_a_doc():\n    pass\n\n\n'
            'def named_in_a_string():\n    pass\n\n\n'
            'used_below()\nasync_used\nprefix_longer = 1\n'))
        write(self.repo, 'README.md', 'Call `named_in_a_doc` to start.\n')
        write(self.repo, 'config.yml', 'hook: "named_in_a_string"\n')
        self.assertEqual(self.dead(), {'only_defined', 'Shadowed', 'prefix'})

    def test_a_name_used_only_in_a_file_that_is_not_utf8_is_dead(self):
        # The old search skipped a file it could not decode, and so does this.
        write(self.repo, 'lib.py', 'def lonely():\n    pass\n')
        with open(os.path.join(self.repo, 'blob.txt'), 'wb') as fh:
            fh.write(b'lonely \xff\xfe')
        self.assertEqual(self.dead(), {'lonely'})


class TestTextCache(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = tempfile.mkdtemp()
        git(self.repo, 'init', '-q', '-b', 'main')

    def tearDown(self) -> None:
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_it_reads_as_text_mode_open_does(self):
        write(self.repo, 'crlf.md', '# One\r\n\r\nTwo\rThree\n')
        with open(os.path.join(self.repo, 'bad.md'), 'wb') as fh:
            fh.write(b'caf\xe9 au lait\n')
        git(self.repo, 'add', '-A')
        git(self.repo, 'commit', '-qm', 'x')
        inventory = discover(self.repo)
        for path in ('crlf.md', 'bad.md'):
            full = os.path.join(self.repo, path)
            with open(full, encoding='utf-8', errors='replace') as fh:
                self.assertEqual(textcache.read(inventory, path, strict=False), fh.read())
        self.assertIsNone(textcache.read(inventory, 'bad.md'))
        self.assertIsNone(textcache.read(inventory, 'missing.md'))

    def test_no_check_opens_a_file_discover_already_read(self):
        generate(self.repo, 60)
        inventory = discover(self.repo)
        graph = build_graph(self.repo, inventory)
        opened: list[str] = []
        real_open = builtins.open

        def counting_open(file, *args, **kwargs):
            if isinstance(file, str) and file.startswith(self.repo):
                opened.append(os.path.relpath(file, self.repo))
            return real_open(file, *args, **kwargs)

        with mock.patch('builtins.open', counting_open):
            for check in (graphcheck.ALL_CHECKS + exactcheck.ALL_CHECKS
                          + convcheck.ALL_CHECKS + cochange.ALL_CHECKS):
                check(inventory, graph)
        self.assertEqual(opened, [])


class TestTimings(unittest.TestCase):
    def test_the_scan_reports_seconds_for_every_step(self):
        repo = tempfile.mkdtemp()
        try:
            generate(repo, 12)
            write(repo, '.dovetail/checks/nothing.py', 'def check(inventory, graph):\n    return []\n')
            result = run_scan(repo)
        finally:
            shutil.rmtree(repo, ignore_errors=True)
        timings = result['timings']
        expected = {'discover', 'graph', 'plugin:nothing'} | {
            check.__name__ for check in (graphcheck.ALL_CHECKS + exactcheck.ALL_CHECKS
                                         + convcheck.ALL_CHECKS + cochange.ALL_CHECKS)}
        self.assertEqual(set(timings), expected)
        self.assertTrue(all(isinstance(v, float) and v >= 0 for v in timings.values()))


class TestScaling(unittest.TestCase):
    """Three times the files must cost about three times the time, not nine."""

    SMALL, LARGE = 1000, 3000
    # Linear is 3. Quadratic is 9. The old code needed over two minutes at
    # the small size and never reached the large one.
    CEILING = 6.0

    @classmethod
    def setUpClass(cls) -> None:
        cls.small = tempfile.mkdtemp()
        cls.large = tempfile.mkdtemp()
        generate(cls.small, cls.SMALL)
        generate(cls.large, cls.LARGE)

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.small, ignore_errors=True)
        shutil.rmtree(cls.large, ignore_errors=True)

    @staticmethod
    def cpu_seconds(repo: str) -> float:
        # CPU time rather than wall time, so a busy runner does not make the
        # ratio.
        started = time.process_time()
        run_scan(repo)
        return time.process_time() - started

    def test_scan_time_grows_roughly_linearly(self):
        small, large = self.cpu_seconds(self.small), self.cpu_seconds(self.large)
        if large / small >= self.CEILING:
            # One slow run must not fail the build: measure again, keep the best.
            small = min(small, self.cpu_seconds(self.small))
            large = min(large, self.cpu_seconds(self.large))
        self.assertLess(large / small, self.CEILING,
                        f'{self.SMALL} files took {small:.2f}s, {self.LARGE} took {large:.2f}s')


if __name__ == '__main__':
    unittest.main()
