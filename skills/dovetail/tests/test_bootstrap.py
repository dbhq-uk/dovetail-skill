"""Tests for the 3.11-floor bootstrap.

The re-exec path cannot be exercised by simply running an old interpreter -
CI tests 3.11, 3.12 and 3.13, all of which already meet the floor. So the
version comparison is driven by passing an unreachable `minimum` instead, which
makes the interesting branches reachable on any supported Python.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import bootstrap  # noqa: E402

SCRIPTS = os.path.join(os.path.dirname(__file__), '..', 'scripts')

# Above any real release, so `find_interpreter` is guaranteed to come back
# empty and the running interpreter is guaranteed to be "too old".
UNREACHABLE = (99, 0)


class TestFindInterpreter(unittest.TestCase):
    def test_finds_something_meeting_the_real_floor(self) -> None:
        # The suite itself runs on >= 3.11, so a qualifying interpreter exists
        # by construction.
        found = bootstrap.find_interpreter(bootstrap.MINIMUM)
        self.assertIsNotNone(found)
        self.assertTrue(os.path.exists(found))

    def test_what_it_finds_actually_meets_the_floor(self) -> None:
        found = bootstrap.find_interpreter(bootstrap.MINIMUM)
        self.assertGreaterEqual(bootstrap._version_of(found), bootstrap.MINIMUM)

    def test_returns_none_when_nothing_qualifies(self) -> None:
        self.assertIsNone(bootstrap.find_interpreter(UNREACHABLE))

    def test_a_non_interpreter_is_rejected_rather_than_trusted(self) -> None:
        # Probed by running it, not by trusting the name - so something on PATH
        # called python3.x that is not a Python cannot be selected.
        self.assertIsNone(bootstrap._version_of('/bin/false'))
        self.assertIsNone(bootstrap._version_of('/nonexistent/python3.12'))


class TestEnsure(unittest.TestCase):
    def test_noop_when_already_new_enough(self) -> None:
        # Must not raise, and must not re-exec the test runner out from under us.
        self.assertIsNone(bootstrap.ensure((3, 0)))

    def test_reports_and_exits_2_when_no_interpreter_qualifies(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            bootstrap.ensure(UNREACHABLE)
        # 2 is the code SKILL.md documents as "report the error and stop".
        self.assertEqual(caught.exception.code, 2)

    def test_the_failure_names_the_fix(self) -> None:
        proc = subprocess.run(
            [sys.executable, '-c',
             f'import sys; sys.path.insert(0, {SCRIPTS!r}); '
             f'import bootstrap; bootstrap.ensure({UNREACHABLE!r})'],
            capture_output=True, text=True,
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn('99.0 or newer', proc.stderr)
        self.assertIn('apt install python3.12', proc.stderr)


class TestReexec(unittest.TestCase):
    """The actual os.execv hop, in a subprocess so it cannot kill the runner."""

    def _run(self, extra: str = '') -> subprocess.CompletedProcess:
        # find_interpreter is stubbed to the running interpreter, so the re-exec
        # is real but lands somewhere known. The stub is inside the -c body so
        # it survives into the re-exec'd process too.
        program = (
            f'import sys, os; sys.path.insert(0, {SCRIPTS!r}); '
            f'import bootstrap; '
            f'bootstrap.find_interpreter = lambda minimum=None: sys.executable; '
            f'print("pass:" + os.environ.get(bootstrap.SENTINEL, "unset")); '
            f'{extra}'
            f'bootstrap.ensure({UNREACHABLE!r})'
        )
        env = dict(os.environ)
        env.pop(bootstrap.SENTINEL, None)
        return subprocess.run(
            [sys.executable, '-c', program],
            capture_output=True, text=True, env=env, timeout=60,
        )

    def test_reexecs_once_then_refuses_to_loop(self) -> None:
        proc = self._run()
        # Two passes: the original, then the re-exec'd one. A third would mean
        # the sentinel is not holding.
        self.assertEqual(
            proc.stdout.split(), ['pass:unset', 'pass:1'],
            f'expected exactly one re-exec, got:\n{proc.stdout}\n{proc.stderr}',
        )
        self.assertEqual(proc.returncode, 2)
        self.assertIn('Giving up rather than looping', proc.stderr)

    def test_the_reexec_preserves_the_original_command_line(self) -> None:
        # `python3 -c "..."` is one of the two documented invocation styles, and
        # it only survives the hop because orig_argv reproduces it verbatim.
        proc = self._run(extra='print("argv:" + repr(sys.orig_argv[1]));')
        self.assertEqual(proc.stdout.count("argv:'-c'"), 2, proc.stdout)


if __name__ == '__main__':
    unittest.main()
