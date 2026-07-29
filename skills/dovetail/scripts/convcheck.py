#!/usr/bin/env python3
"""
Convention checks: rules a repository states about itself, checked mechanically.

Every rule moved here is one a judgement reviewer stops paying for, and one that
cannot be missed on a bad day. The bar for inclusion is that the rule is
*general* - true of well-kept repositories rather than of one particular repo.

Rules specific to a single repository belong in `.dovetail/checks/`, not here.
That is not a cop-out: a repo-specific rule shipped in the tool is a rule every
other user has to suppress, and the plugin point exists precisely so those
rules can be exact and free without becoming everyone else's noise.
"""

from __future__ import annotations

import os
import re
import stat

from store import make_finding

SHEBANG = re.compile(r'^#!')
_FRONTMATTER = re.compile(r'^---\n(.*?)\n---', re.S)
_NAME = re.compile(r'^name:\s*(.+)$', re.M)
_DESCRIPTION = re.compile(r'^description:\s*(.+)$', re.M)
# `set -e`, `set -eu`, `set -euo pipefail`, `set -o errexit`.
_SET_E = re.compile(r'^\s*set\s+(-[a-zA-Z]*e[a-zA-Z]*|-o\s+errexit)\b', re.M)

SHELL_EXTS = ('.sh', '.bash')
# Test modules conventionally carry a shebang they never use - the runner
# imports them, nobody executes them. Twelve of fifteen findings on the first
# real run were test files, which is how a useful check becomes one people mute.
TEST_DIR_NAMES = frozenset({'test', 'tests', '__tests__'})


def _is_test(path: str) -> bool:
    parts = path.split('/')
    basename = parts[-1]
    if any(part in TEST_DIR_NAMES for part in parts[:-1]):
        return True
    return basename.startswith('test_') or basename.startswith('conftest.')


def _read(repo_root: str, path: str) -> str | None:
    try:
        with open(os.path.join(repo_root, path), encoding='utf-8') as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def shell_scripts_exit_on_error(inventory: dict, graph: dict) -> list[dict]:
    """A shell script without `set -e` continues after a failed command.

    Restricted to scripts that are executable or carry a shebang: a `.sh`
    fragment meant to be sourced has no business exiting the caller's shell,
    and flagging it would be wrong rather than merely noisy.
    """
    repo_root = inventory['repo_root']
    findings: list[dict] = []
    for entry in inventory['files']:
        path = entry['path']
        if not path.endswith(SHELL_EXTS):
            continue
        text = _read(repo_root, path)
        if text is None:
            continue
        executable = os.access(os.path.join(repo_root, path), os.X_OK)
        if not executable and not SHEBANG.match(text):
            continue  # a sourced fragment, not a program
        if _SET_E.search(text):
            continue
        findings.append(make_finding(
            source='check:convention',
            category='convention',
            problem=f'{path} is an executable shell script with no `set -e`; '
                    'it keeps going after a command fails.',
            evidence=[{'file': path, 'line': 1,
                       'quote': text.split('\n', 1)[0][:200]}],
            suggestion='Add `set -e` (or `set -euo pipefail`) near the top.',
            severity='medium',
            claim=f'{path}|set -e',
        ))
    return findings


def scripts_are_executable(inventory: dict, graph: dict) -> list[dict]:
    """A file with a shebang that cannot be run.

    The shebang is the author stating intent: this is a program. If the bit is
    missing the intent is unmet, and the failure shows up as a confusing
    'permission denied' at the worst moment.
    """
    repo_root = inventory['repo_root']
    findings: list[dict] = []
    for entry in inventory['files']:
        path = entry['path']
        if entry['modality'] != 'text':
            continue
        if _is_test(path):
            continue
        text = _read(repo_root, path)
        if text is None or not SHEBANG.match(text):
            continue
        full = os.path.join(repo_root, path)
        try:
            mode = os.stat(full).st_mode
        except OSError:
            continue
        if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            continue
        findings.append(make_finding(
            source='check:convention',
            category='convention',
            problem=f'{path} declares a shebang but is not executable.',
            evidence=[{'file': path, 'line': 1,
                       'quote': text.split('\n', 1)[0][:200]}],
            suggestion=f'`chmod +x {path}`, or drop the shebang if it is not a program.',
            severity='low',
            claim=f'{path}|executable',
        ))
    return findings


def skill_frontmatter(inventory: dict, graph: dict) -> list[dict]:
    """Every SKILL.md needs frontmatter with `name` and `description`.

    A skill whose frontmatter is malformed is silently never invoked, which is
    the worst failure mode available: everything looks installed and nothing
    happens. The name must also match its directory, since that is what the
    loader keys on.
    """
    repo_root = inventory['repo_root']
    findings: list[dict] = []
    for entry in inventory['files']:
        path = entry['path']
        if os.path.basename(path) != 'SKILL.md':
            continue
        text = _read(repo_root, path)
        if text is None:
            continue
        block = _FRONTMATTER.match(text)
        if not block:
            findings.append(make_finding(
                source='check:convention',
                category='convention',
                problem=f'{path} has no YAML frontmatter, so the skill will never load.',
                evidence=[{'file': path, 'line': 1, 'quote': text.split('\n', 1)[0][:200]}],
                suggestion='Add a `---` block declaring `name` and `description`.',
                severity='high',
                claim=f'{path}|frontmatter',
            ))
            continue
        body = block.group(1)
        name = _NAME.search(body)
        if not name:
            findings.append(make_finding(
                source='check:convention',
                category='convention',
                problem=f'{path} frontmatter declares no `name`.',
                evidence=[{'file': path, 'line': 2, 'quote': body.split('\n', 1)[0][:200]}],
                suggestion='Add `name: <skill-name>` to the frontmatter.',
                severity='high',
                claim=f'{path}|name',
            ))
        else:
            declared = name.group(1).strip()
            directory = os.path.basename(os.path.dirname(path))
            if directory and declared != directory:
                findings.append(make_finding(
                    source='check:convention',
                    category='convention',
                    problem=(f'{path} declares name `{declared}` but sits in '
                             f'`{directory}/`; the loader keys on the directory.'),
                    evidence=[{'file': path, 'line': 2, 'quote': f'name: {declared}'}],
                    suggestion=f'Rename the skill to `{directory}` or move the directory.',
                    severity='high',
                    claim=f'{path}|name-mismatch',
                ))
        if not _DESCRIPTION.search(body):
            findings.append(make_finding(
                source='check:convention',
                category='convention',
                problem=f'{path} frontmatter declares no `description`, so nothing '
                        'will ever trigger the skill.',
                evidence=[{'file': path, 'line': 2, 'quote': body.split('\n', 1)[0][:200]}],
                suggestion='Add a `description` naming the situations that should trigger it.',
                severity='high',
                claim=f'{path}|description',
            ))
    return findings


ALL_CHECKS = [
    shell_scripts_exit_on_error,
    scripts_are_executable,
    skill_frontmatter,
]
