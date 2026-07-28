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
from gitmeta import changed_since, is_git_repo
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

    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
