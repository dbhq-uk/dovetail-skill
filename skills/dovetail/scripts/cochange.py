#!/usr/bin/env python3
"""
Signals read out of git history: co-change coupling, and TODO age.

Co-change coupling is the check no linter has and no model can produce. Two
files that have changed together in nearly every commit for a year are coupled
in fact, whatever the code says - and when one of them then changes alone, that
is either a deliberate decoupling or a change someone forgot to mirror. Nobody
had to write the rule; the repository's own history states it.

TODO age turns a worthless observation into a useful one. "There is a TODO" is
noise. "This TODO is fourteen months old" is a decision waiting to be made.

Both degrade rather than fail when git is unavailable or the history is too
shallow to read.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import subprocess

from store import make_finding

# How much history to read. Enough to establish a pattern, bounded so a huge
# repository does not turn a "seconds" scan into a minutes one.
MAX_COMMITS = 500
# A pair must have changed together at least this often before its history is
# treated as evidence of anything.
MIN_SHARED_COMMITS = 5
# ...and that must be at least this fraction of the times either file changed.
MIN_COUPLING = 0.75
# Commits since the pair last moved together, before a decoupling is reported.
DECOUPLED_AFTER = 3

TODO_MARKER = re.compile(r'\b(TODO|FIXME|TBD|HACK|XXX)\b')
STALE_TODO_DAYS = 180
# Reading blame for every marker in a huge repository is the one thing here
# that can get slow, so the number of files blamed is bounded.
MAX_BLAME_FILES = 60


def _git(repo_root: str, *args: str, timeout: int = 60) -> str | None:
    try:
        out = subprocess.run(
            ['git', '-C', repo_root, *args],
            capture_output=True, text=True, timeout=timeout, check=True,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired,
            FileNotFoundError, OSError):
        return None
    return out.stdout


def commit_file_sets(repo_root: str, max_commits: int = MAX_COMMITS) -> list[set[str]]:
    """The set of files touched by each of the most recent commits.

    One `git log` traversal rather than one call per commit: on a large history
    the difference is seconds versus minutes.
    """
    out = _git(repo_root, 'log', f'-{max_commits}', '--name-only',
               '--pretty=format:%x00', '--no-merges')
    if out is None:
        return []
    commits: list[set[str]] = []
    for chunk in out.split('\x00'):
        files = {line.strip() for line in chunk.split('\n') if line.strip()}
        if files:
            commits.append(files)
    return commits


def decoupled_pairs(inventory: dict, graph: dict) -> list[dict]:
    """Files with a long shared history that have recently stopped moving together.

    Deliberately conservative on every axis - a long shared history, a high
    coupling ratio, and a clear run of solo changes - because this finding is a
    prompt to think, not a defect report, and one that fires loosely would be
    the first thing anyone turned off.
    """
    repo_root = inventory['repo_root']
    tracked = {e['path'] for e in inventory['files']}
    commits = commit_file_sets(repo_root)
    if len(commits) < MIN_SHARED_COMMITS * 2:
        return []

    # commits[0] is the newest, so the index doubles as "commits ago".
    changed_at: dict[str, list[int]] = {}
    for index, files in enumerate(commits):
        for path in files:
            if path in tracked:
                changed_at.setdefault(path, []).append(index)

    pair_shared: dict[tuple[str, str], list[int]] = {}
    for index, files in enumerate(commits):
        relevant = sorted(f for f in files if f in tracked)
        if len(relevant) > 40:
            continue  # a sweeping refactor couples everything; it is not evidence
        for i, left in enumerate(relevant):
            for right in relevant[i + 1:]:
                pair_shared.setdefault((left, right), []).append(index)

    findings: list[dict] = []
    for (left, right), shared in sorted(pair_shared.items()):
        if len(shared) < MIN_SHARED_COMMITS:
            continue
        left_total = len(changed_at.get(left, []))
        right_total = len(changed_at.get(right, []))
        if not left_total or not right_total:
            continue
        coupling = len(shared) / min(left_total, right_total)
        if coupling < MIN_COUPLING:
            continue

        last_together = min(shared)
        solo_since = sum(
            1 for index in range(last_together)
            if (index in changed_at.get(left, [])) != (index in changed_at.get(right, []))
        )
        if solo_since < DECOUPLED_AFTER:
            continue

        findings.append(make_finding(
            source='check:cochange',
            category='decoupled',
            problem=(f'{left} and {right} changed together in '
                     f'{len(shared)} of their last commits '
                     f'({round(coupling * 100)}% coupling), but have changed '
                     f'apart {solo_since} times since.'),
            evidence=[
                {'file': left, 'line': 1,
                 'quote': f'changed with {os.path.basename(right)} {len(shared)} times'},
                {'file': right, 'line': 1,
                 'quote': f'changed with {os.path.basename(left)} {len(shared)} times'},
            ],
            suggestion='Check whether the recent changes to one should have been '
                       'mirrored in the other.',
            severity='low',
            claim=f'decoupled:{left},{right}',
        ))
    return findings


def _blame_dates(repo_root: str, path: str) -> dict[int, _dt.date]:
    """Author date per line, 1-indexed, from a single porcelain blame."""
    out = _git(repo_root, 'blame', '--line-porcelain', '--', path, timeout=30)
    if out is None:
        return {}
    dates: dict[int, _dt.date] = {}
    line_no = None
    for line in out.split('\n'):
        if re.match(r'^[0-9a-f]{40} \d+ (\d+)', line):
            try:
                line_no = int(line.split(' ')[2])
            except (IndexError, ValueError):
                line_no = None
        elif line.startswith('author-time ') and line_no is not None:
            try:
                stamp = int(line.split(' ', 1)[1])
            except (IndexError, ValueError):
                continue
            dates[line_no] = _dt.datetime.fromtimestamp(
                stamp, _dt.timezone.utc).date()
    return dates


def stale_todos(inventory: dict, graph: dict) -> list[dict]:
    """TODO-style markers old enough to be decisions rather than intentions."""
    repo_root = inventory['repo_root']
    today = _dt.datetime.now(_dt.timezone.utc).date()

    with_markers: list[tuple[str, list[tuple[int, str]]]] = []
    for entry in inventory['files']:
        if entry['modality'] != 'text':
            continue
        try:
            with open(os.path.join(repo_root, entry['path']), encoding='utf-8') as fh:
                lines = fh.read().split('\n')
        except (OSError, UnicodeDecodeError):
            continue
        hits = [(i + 1, line.strip())
                for i, line in enumerate(lines) if TODO_MARKER.search(line)]
        if hits:
            with_markers.append((entry['path'], hits))

    # Largest first: a file thick with markers is the one worth the blame call.
    with_markers.sort(key=lambda pair: -len(pair[1]))

    findings: list[dict] = []
    for path, hits in with_markers[:MAX_BLAME_FILES]:
        dates = _blame_dates(repo_root, path)
        if not dates:
            continue
        for line_no, text in hits:
            when = dates.get(line_no)
            if when is None:
                continue
            age = (today - when).days
            if age < STALE_TODO_DAYS:
                continue
            months = age // 30
            findings.append(make_finding(
                source='check:todo',
                category='stale_todo',
                problem=f'{path}:{line_no} has carried a TODO for {months} months.',
                evidence=[{'file': path, 'line': line_no, 'quote': text[:200]}],
                suggestion='Do it, schedule it, or delete it - it has not been '
                           'load-bearing for a while.',
                severity='low',
                claim=f'{path}|{text}',
            ))
    return findings


ALL_CHECKS = [
    decoupled_pairs,
    stale_todos,
]
