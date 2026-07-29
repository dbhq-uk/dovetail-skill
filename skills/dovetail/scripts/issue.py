#!/usr/bin/env python3
"""
Tracking-issue upsert for the scheduled audit.

One issue, found by label and rewritten in place. Never a new issue per run:
a weekly job that opens a fresh issue every week is a job whose notifications
people mute within a month, and muted output is the same as no output.

Ported from the prior tool's `find-issue.ts` / `issue.ts`, and smaller here because
`gh` does the API work.

Usage:
  issue.py --findings FILE [--scan FILE] [--repo OWNER/NAME]
           [--label dovetail] [--title "..."] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys

DEFAULT_LABEL = 'dovetail'
DEFAULT_TITLE = 'dovetail: repository coherence'
SEVERITY_RANK = {'high': 0, 'medium': 1, 'low': 2}
# GitHub rejects a body over 65536 characters, and a truncated-at-the-limit
# body loses the footer that says it was truncated.
MAX_BODY = 60_000


def _gh(*args: str, check: bool = True) -> str:
    result = subprocess.run(['gh', *args], capture_output=True, text=True)
    if check and result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip()[:400])
    return result.stdout


def find_issue(repo: str | None, label: str) -> int | None:
    """The open issue carrying the label, or None."""
    args = ['issue', 'list', '--label', label, '--state', 'open',
            '--json', 'number', '--limit', '1']
    if repo:
        args += ['--repo', repo]
    try:
        rows = json.loads(_gh(*args) or '[]')
    except (RuntimeError, json.JSONDecodeError):
        return None
    return rows[0]['number'] if rows else None


def ensure_label(repo: str | None, label: str) -> None:
    """Create the label if it is missing. Never fatal - it is cosmetic."""
    args = ['label', 'create', label, '--description',
            'Repository coherence findings from dovetail', '--color', '0E8A16']
    if repo:
        args += ['--repo', repo]
    try:
        _gh(*args, check=False)
    except RuntimeError:
        pass


def render_body(findings: list[dict], scan: dict | None = None,
                failed: list[str] | None = None) -> str:
    """The issue body: a summary, then findings grouped by severity."""
    lines: list[str] = []

    counts = {'high': 0, 'medium': 0, 'low': 0}
    for finding in findings:
        counts[finding.get('severity', 'low')] = counts.get(
            finding.get('severity', 'low'), 0) + 1

    lines.append(f"**{counts['high']} high · {counts['medium']} medium · "
                 f"{counts['low']} low**")
    if scan:
        lines.append('')
        lines.append(
            f"Scanned {scan.get('file_count', '?')} files, "
            f"{scan.get('edge_count', '?')} references. "
            f"{scan.get('suppressed', 0)} finding(s) suppressed by "
            '`.dovetail/decisions.jsonl`.')
    if failed:
        lines.append('')
        lines.append('> **Coverage is incomplete.** These reviewers failed, so '
                     'their findings are missing entirely:')
        for name in failed:
            lines.append(f'> - `{name}`')

    if not findings:
        lines += ['', 'No findings. The repository agrees with itself.']
        return '\n'.join(lines)

    ordered = sorted(
        findings,
        key=lambda f: (SEVERITY_RANK.get(f.get('severity', 'low'), 2),
                       f.get('category', ''),
                       f.get('evidence', [{}])[0].get('file', '')))

    current = None
    for finding in ordered:
        severity = finding.get('severity', 'low')
        if severity != current:
            current = severity
            lines += ['', f'## {severity.title()}', '']
        evidence = finding.get('evidence') or [{}]
        spot = evidence[0]
        where = f"`{spot.get('file', '?')}:{spot.get('line', '?')}`"
        source = finding.get('source', '')
        proved = source == 'graph' or source.startswith(('check:', 'plugin:'))
        mark = 'exact' if proved else 'judged'
        lines.append(f"- **{finding.get('category', '?')}** ({mark}) {where} - "
                     f"{finding.get('problem', '').strip()}")
        for item in evidence[1:]:
            lines.append(f"    - `{item.get('file', '?')}:{item.get('line', '?')}`"
                         f" {str(item.get('quote', '')).strip()[:140]}")
        if finding.get('suggestion'):
            lines.append(f"    - _{finding['suggestion'].strip()}_")

    body = '\n'.join(lines)
    if len(body) > MAX_BODY:
        body = body[:MAX_BODY].rsplit('\n', 1)[0]
        body += ('\n\n_Truncated: too many findings for one issue. '
                 'Run dovetail locally for the full list._')
    return body


def upsert(findings: list[dict], *, repo: str | None = None,
           label: str = DEFAULT_LABEL, title: str = DEFAULT_TITLE,
           scan: dict | None = None, failed: list[str] | None = None,
           dry_run: bool = False) -> str:
    """Create or update the tracking issue. Returns what it did."""
    body = render_body(findings, scan=scan, failed=failed)
    if dry_run:
        print(body)
        return 'dry-run'

    ensure_label(repo, label)
    number = find_issue(repo, label)
    if number is None:
        args = ['issue', 'create', '--title', title, '--body', body,
                '--label', label]
        if repo:
            args += ['--repo', repo]
        _gh(*args)
        return 'created'

    args = ['issue', 'edit', str(number), '--body', body]
    if repo:
        args += ['--repo', repo]
    _gh(*args)
    return f'updated #{number}'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='issue.py', description='Upsert the dovetail tracking issue')
    parser.add_argument('--findings', required=True,
                        help='JSON from ci_dispatch.py or scan.py')
    parser.add_argument('--scan', help='scan.py JSON, for the summary line')
    parser.add_argument('--repo', help='OWNER/NAME; defaults to the current repo')
    parser.add_argument('--label', default=DEFAULT_LABEL)
    parser.add_argument('--title', default=DEFAULT_TITLE)
    parser.add_argument('--dry-run', action='store_true',
                        help='print the body instead of touching GitHub')
    return parser


def _load(path: str) -> dict:
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = _load(args.findings)
    except (OSError, json.JSONDecodeError) as exc:
        print(f'error: cannot read --findings: {exc}', file=sys.stderr)
        return 2

    findings = list(payload.get('findings', []))
    failed = list(payload.get('failed_reviewers', []))

    scan = None
    if args.scan and os.path.exists(args.scan):
        try:
            scan = _load(args.scan)
        except (OSError, json.JSONDecodeError):
            scan = None
        if scan:
            findings += scan.get('findings', [])
            failed += scan.get('failed_checks', [])

    try:
        print(upsert(findings, repo=args.repo, label=args.label,
                     title=args.title, scan=scan, failed=failed,
                     dry_run=args.dry_run), file=sys.stderr)
    except RuntimeError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
