#!/usr/bin/env python3
"""
Meet the 3.11 floor on a host whose `python3` is older.

The floor is `tomllib`, and it is not negotiable. What *is* negotiable is which
interpreter runs: which Python `python3` resolves to is a property of the host,
not a choice the user makes per command. Debian and Ubuntu happily carry 3.12
alongside a 3.10 default, so a machine can have everything dovetail needs and
still fail every documented invocation with `ModuleNotFoundError: tomllib`.

The obvious fixes are both wrong here. Rewriting `python3` in SKILL.md at
install time breaks the live-symlink install, which is the property that lets an
edit to SKILL.md take effect without reinstalling. Telling the user to
repoint `python3` is worse: it is a global change to their machine to satisfy
one skill, and it silently strands anything pinned to the older interpreter.

So the resolution happens at runtime instead. `ensure()` re-runs the *original*
command line under the newest suitable interpreter it can find, which works for
both documented invocation styles - `python3 scan.py ...` and `python3 -c "..."`
alike - because `sys.orig_argv` reproduces either one exactly.

When no suitable interpreter exists this fails loudly, per the constraint in
AGENTS.md: a checker that reports success because it could not run is worse than
no checker at all.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

# tomllib landed in 3.11; see docs/design-notes.md.
MINIMUM = (3, 11)

# Set across the re-exec so a mis-detected interpreter cannot loop forever.
SENTINEL = 'DOVETAIL_BOOTSTRAP_REEXEC'

# Newest first, so a host with several gets the best rather than merely the
# first adequate one. `python3` is included because it is the common case and
# may already be new enough; it is version-checked like every other candidate.
CANDIDATES = (
    'python3.14',
    'python3.13',
    'python3.12',
    'python3.11',
    'python3',
)

_PROBE = 'import sys; print("%d.%d" % sys.version_info[:2])'


def _die(message: str) -> None:
    """Exit 2, the code SKILL.md documents as 'report this and stop'."""
    sys.stderr.write(message)
    raise SystemExit(2)


def _version_of(executable: str) -> tuple[int, int] | None:
    """Return (major, minor) for `executable`, or None if it cannot be asked.

    Probed by running it rather than parsed from the filename: `python3.12` on
    PATH is a conventional name, not a guarantee, and a wrapper that shadows it
    is exactly the case worth catching.
    """
    try:
        proc = subprocess.run(
            [executable, '-c', _PROBE],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        major, minor = (int(part) for part in proc.stdout.strip().split('.'))
    except ValueError:
        return None
    return (major, minor)


def find_interpreter(minimum: tuple[int, int] = MINIMUM) -> str | None:
    """Return the path of the newest interpreter on PATH meeting `minimum`.

    None when the host has nothing suitable, which the callers report rather
    than work around.
    """
    best: str | None = None
    best_version: tuple[int, int] | None = None
    seen: set[str] = set()

    for name in CANDIDATES:
        path = shutil.which(name)
        if path is None:
            continue
        # python3 is usually a symlink to one of the versioned names; probing
        # the same binary twice is pure cost.
        real = os.path.realpath(path)
        if real in seen:
            continue
        seen.add(real)

        version = _version_of(path)
        if version is None or version < minimum:
            continue
        if best_version is None or version > best_version:
            best, best_version = path, version

    return best


def ensure(minimum: tuple[int, int] = MINIMUM) -> None:
    """Re-exec under a suitable interpreter, or exit 2 explaining why it cannot.

    A no-op - and cheap, no subprocesses - on the overwhelmingly common path
    where the running interpreter already qualifies.
    """
    if sys.version_info >= minimum:
        return

    want = '.'.join(str(part) for part in minimum)
    running = '.'.join(str(part) for part in sys.version_info[:2])

    # Re-exec'd once already and still too old: the environment is lying to us,
    # so stop rather than spawn processes forever.
    if os.environ.get(SENTINEL):
        _die(
            f'dovetail: re-executed under {sys.executable} and still on Python '
            f'{running}, which is below the {want} floor. Giving up rather than '
            f'looping.\n'
        )

    interpreter = find_interpreter(minimum)
    if interpreter is None:
        _die(
            f'dovetail: needs Python {want} or newer for tomllib; this is Python '
            f'{running} and no newer interpreter is on PATH.\n'
            f'  macOS:  brew install python@3.12\n'
            f'  Ubuntu: sudo apt install python3.12\n'
            f'dovetail will then use it automatically - `python3` itself does '
            f'not have to change.\n'
        )

    # orig_argv reproduces the full command line, so `-c "..."` survives the
    # hop as faithfully as a script path does. argv[0] is the interpreter.
    argv = list(getattr(sys, 'orig_argv', None) or [sys.executable, *sys.argv])
    argv[0] = interpreter

    # execv replaces the process image without running Python's shutdown, so
    # anything still sitting in a buffer is simply lost - and stdout is block
    # buffered whenever it is a pipe, which is exactly the case in CI and under
    # an agent harness.
    sys.stdout.flush()
    sys.stderr.flush()

    os.environ[SENTINEL] = '1'
    os.execv(interpreter, argv)


if __name__ == '__main__':
    # The installers use this to report which interpreter dovetail will run
    # under, without duplicating the discovery logic in shell.
    found = find_interpreter()
    if found is None:
        raise SystemExit(1)
    print(found)
