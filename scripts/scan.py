#!/usr/bin/env python3
"""
dovetail scan — the deterministic layer.

Builds an inventory and reference graph for a repository, runs every
deterministic check, drops findings suppressed by the committed decisions
ledger, and prints the result.

Read-only: this never modifies the target repository.

Usage:
  scan.py <repo-path> [--format json|github] [--since REF]
                      [--fail-on none|low|medium|high] [--ignore GLOB ...]
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import graphcheck
from discover import discover
from gitmeta import changed_since, is_git_repo, rev_exists
from refgraph import build_graph
from store import load_decisions

SEVERITY_RANK = {'high': 0, 'medium': 1, 'low': 2}


def run_scan(repo_root: str, *, ignore: list[str] | None = None,
             since: str | None = None) -> dict:
    """Run every deterministic check and return findings plus counts."""
    root = os.path.abspath(repo_root)
    if not os.path.isdir(root):
        raise ValueError(f'not a directory: {repo_root}')
    if not is_git_repo(root):
        raise ValueError(f'not a git repository: {repo_root}')

    inventory = discover(root, ignore=ignore)
    graph = build_graph(root, inventory)

    findings: list[dict] = []
    failed_checks: list[str] = []
    for check in graphcheck.ALL_CHECKS:
        try:
            findings.extend(check(inventory, graph))
        except Exception:  # a broken check must not take down the run
            failed_checks.append(check.__name__)

    if since:
        if not is_git_repo(root) or not rev_exists(root, since):
            raise ValueError(
                f'--since ref does not resolve: {since!r}. '
                'In CI this usually means a shallow clone — fetch enough '
                'history for the base ref, e.g. actions/checkout with '
                'fetch-depth: 0.'
            )
        changed = changed_since(root, since)
        findings = [
            f for f in findings
            if any(e['file'] in changed for e in f['evidence'])
        ]

    decisions = load_decisions(root)
    kept = [f for f in findings if f['id'] not in decisions]
    suppressed = len(findings) - len(kept)

    kept.sort(key=lambda f: (SEVERITY_RANK[f['severity']],
                             f['category'],
                             f['evidence'][0]['file'] if f['evidence'] else ''))

    counts = {'high': 0, 'medium': 0, 'low': 0}
    for finding in kept:
        counts[finding['severity']] += 1

    return {'findings': kept, 'suppressed': suppressed,
            'counts': counts, 'failed_checks': failed_checks}


def _escape(message: str) -> str:
    """Escape a workflow-command message per GitHub's rules."""
    return (message.replace('%', '%25')
                   .replace('\r', '%0D')
                   .replace('\n', '%0A')
                   .replace(',', '%2C')
                   .replace(':', '%3A'))


def format_github(result: dict) -> str:
    """Render findings as GitHub workflow annotations."""
    lines: list[str] = []
    for finding in result['findings']:
        level = 'error' if finding['severity'] == 'high' else 'warning'
        spot = finding['evidence'][0] if finding['evidence'] else {'file': '', 'line': 1}
        message = _escape(f"{finding['problem']} {finding['suggestion']}".strip())
        lines.append(
            f"::{level} file={spot['file']},line={spot['line']},"
            f"title={finding['category']}::{message}"
        )
    for name in result['failed_checks']:
        lines.append(f'::warning title=dovetail::check {name} failed; findings may be incomplete')
    return '\n'.join(lines)


def exit_code(result: dict, fail_on: str) -> int:
    """1 when a deterministic finding meets the threshold, else 0.

    Judgement-sourced findings can never fail a build: they are probabilistic,
    and a merge gate that produces false positives is one people learn to
    override.
    """
    if fail_on == 'none':
        return 0
    threshold = SEVERITY_RANK[fail_on]
    for finding in result['findings']:
        if not (finding['source'] == 'graph' or finding['source'].startswith('check:')):
            continue
        if SEVERITY_RANK[finding['severity']] <= threshold:
            return 1
    return 0


def _summary_markdown(result: dict) -> str:
    """Job-summary table for the GitHub Actions run page."""
    counts = result['counts']
    lines = [
        '## dovetail',
        '',
        f"{counts['high']} high · {counts['medium']} medium · {counts['low']} low"
        f" · {result['suppressed']} suppressed by prior decisions",
        '',
    ]
    if result['findings']:
        lines += ['| Severity | Category | File | Problem |',
                  '|---|---|---|---|']
        for finding in result['findings']:
            spot = finding['evidence'][0] if finding['evidence'] else {'file': '', 'line': 1}
            problem = finding['problem'].replace('|', '\\|')
            lines.append(
                f"| {finding['severity']} | {finding['category']} "
                f"| `{spot['file']}:{spot['line']}` | {problem} |"
            )
    else:
        lines.append('No findings.')
    return '\n'.join(lines) + '\n'


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='scan.py', description='dovetail deterministic scan (read-only)')
    parser.add_argument('repo', help='path to the repository to scan')
    parser.add_argument('--format', choices=['json', 'github'], default='json')
    parser.add_argument('--since', metavar='REF',
                        help='only report findings touching files changed since REF')
    parser.add_argument('--fail-on', choices=['none', 'low', 'medium', 'high'],
                        default='none', help='exit non-zero at or above this severity')
    parser.add_argument('--ignore', action='append', metavar='GLOB', default=[],
                        help='glob to exclude; repeatable')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_scan(args.repo, ignore=args.ignore, since=args.since)
    except ValueError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    if args.format == 'github':
        rendered = format_github(result)
        if rendered:
            print(rendered)
        summary = os.environ.get('GITHUB_STEP_SUMMARY')
        if summary:
            with open(summary, 'a', encoding='utf-8') as fh:
                fh.write(_summary_markdown(result))
    else:
        print(json.dumps(result, indent=2, ensure_ascii=False))

    return exit_code(result, args.fail_on)


if __name__ == '__main__':
    sys.exit(main())
