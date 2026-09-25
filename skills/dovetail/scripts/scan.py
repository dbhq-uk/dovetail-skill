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

import bootstrap

# Before any dovetail import, several of which reach tomllib: on a host whose
# `python3` predates 3.11 this re-execs the whole command under a newer one.
bootstrap.ensure()

import cochange  # noqa: E402
import convcheck
import exactcheck
import fixes
import graphcheck
import plugins as plugin_runner
from config import HEURISTIC_CHECKS, ConfigError, check_enabled, gated_checks, load_config
from discover import discover
from gitmeta import changed_since, is_git_repo, rev_exists
from refgraph import build_graph, written_target
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

    # A present-but-invalid config raises rather than falling back: silently
    # ignoring it would hide findings the user meant to see.
    config = load_config(root)
    ignore = list(ignore or []) + list(config['ignore'])

    inventory = discover(root, ignore=ignore)
    graph = build_graph(root, inventory)

    findings: list[dict] = []
    failed_checks: list[str] = []
    for check in (graphcheck.ALL_CHECKS + exactcheck.ALL_CHECKS
                  + convcheck.ALL_CHECKS + cochange.ALL_CHECKS):
        name = check.__name__
        if not check_enabled(config, name):
            continue
        try:
            produced = check(inventory, graph)
        except Exception:  # a broken check must not take down the run
            failed_checks.append(name)
            continue
        tier = 'heuristic' if name in HEURISTIC_CHECKS else 'proven'
        for finding in produced:
            finding['check'] = name
            finding['tier'] = tier
        findings.extend(produced)

    # Repo-local checks last, so a plugin can rely on everything above having
    # run. A plugin that raises is named, not fatal. Its findings are
    # heuristic: nothing about a plugin says its rule is certain.
    for result in plugin_runner.run_plugins(root, inventory, graph):
        if result.error:
            failed_checks.append(f'plugin:{result.name} ({result.error})')
        else:
            for finding in result.findings:
                finding['check'] = f'plugin:{result.name}'
                finding['tier'] = 'heuristic'
            findings.extend(result.findings)

    # Facts SKILL.md orders and batches by. Computed here so every run on one
    # repository triages the same way, rather than the agent judging them.
    for finding in findings:
        if not finding.get('blast_radius'):
            finding['blast_radius'] = fixes.blast_radius(finding, graph['inbound'])
        finding['batch_eligible'] = fixes.batch_eligible(finding)

    if since:
        if not is_git_repo(root) or not rev_exists(root, since):
            raise ValueError(
                f'--since ref does not resolve: {since!r}. '
                'In CI this usually means a shallow clone — fetch enough '
                'history for the base ref, e.g. actions/checkout with '
                'fetch-depth: 0.'
            )
        changed = changed_since(root, since)
        targets = _link_targets(graph)
        findings = [f for f in findings if _touches(f, changed, targets)]

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
            'counts': counts, 'failed_checks': failed_checks,
            'profile': config['profile'], 'gate': gated_checks(config),
            'file_count': len(inventory['files']),
            'edge_count': len(graph['edges'])}


# Findings about a link, where the change that caused them is usually to the
# target rather than to the file holding the link.
_LINK_CATEGORIES = frozenset({'broken_link', 'dangling_anchor'})


def _link_targets(graph: dict) -> dict[tuple[str, int], set[str]]:
    """Every path each (file, line) links to, resolved or only as written."""
    targets: dict[tuple[str, int], set[str]] = {}
    for edge in graph['edges']:
        found = targets.setdefault((edge['src'], edge['line']), set())
        if edge['dst']:
            found.add(edge['dst'])
        written = written_target(edge['src'], edge['raw'])
        if written:
            found.add(written)
    return targets


def _touches(finding: dict, changed: set[str],
             targets: dict[tuple[str, int], set[str]]) -> bool:
    """Whether a change since the ref could have caused this finding.

    A finding's evidence names the file holding a link, but the pull request
    that breaks the link is usually the one that deletes, renames or edits
    the file it points at. Matching on evidence alone let that pull request
    pass and blamed the break on whoever touched the linking file next.
    """
    if any(item['file'] in changed for item in finding['evidence']):
        return True
    if finding['category'] not in _LINK_CATEGORIES:
        return False
    for item in finding['evidence']:
        for target in targets.get((item['file'], item['line']), ()):
            # An import written as `./b` names b.ts, and a link to a
            # directory names the files in it.
            if target in changed or any(
                    path.startswith((target + '.', target + '/')) for path in changed):
                return True
    return False


def _escape_data(message: str) -> str:
    """Escape a workflow-command message (the part after `::`).

    GitHub decodes only these three in the data portion; escaping `:` or `,`
    here would render literally in the Actions UI.
    """
    return (message.replace('%', '%25')
                   .replace('\r', '%0D')
                   .replace('\n', '%0A'))


def _escape_property(value: str) -> str:
    """Escape a workflow-command property value (file=, line=, title=).

    Property values additionally need `:` and `,` escaped, or they would
    terminate the property list early and corrupt the annotation.
    """
    return (value.replace('%', '%25')
                 .replace('\r', '%0D')
                 .replace('\n', '%0A')
                 .replace(':', '%3A')
                 .replace(',', '%2C'))


def gates(finding: dict, gate: list[str] | set[str] = ()) -> bool:
    """Whether a finding can fail `--fail-on`.

    Proven findings can, and heuristic ones only when `[gate]` in the config
    names their check. Judged findings never carry a tier, so never can.
    """
    if not (finding['source'] == 'graph' or finding['source'].startswith('check:')):
        return False
    return finding.get('tier') == 'proven' or finding.get('check') in gate


def format_github(result: dict) -> str:
    """Render findings as GitHub workflow annotations.

    Only a finding that can fail the build is an error. A high heuristic
    finding is a warning, because a red annotation on a green build says two
    things at once.
    """
    lines: list[str] = []
    gate = set(result.get('gate') or [])
    for finding in result['findings']:
        level = 'error' if finding['severity'] == 'high' and gates(finding, gate) else 'warning'
        spot = finding['evidence'][0] if finding['evidence'] else {'file': '', 'line': 1}
        message = _escape_data(f"{finding['problem']} {finding['suggestion']}".strip())
        lines.append(
            f"::{level} file={_escape_property(spot['file'])},"
            f"line={spot['line']},"
            f"title={_escape_property(finding['category'])}::{message}"
        )
    for name in result['failed_checks']:
        lines.append(
            f'::error title=dovetail::check {_escape_data(name)} failed; '
            'findings may be incomplete'
        )
    return '\n'.join(lines)


def exit_code(result: dict, fail_on: str) -> int:
    """1 when a finding that gates meets the threshold, else 0.

    Only proven findings gate, plus heuristic checks that `[gate]` in the
    config opts in. Judgement-sourced findings can never fail a build: they
    are probabilistic, and a merge gate that produces false positives is one
    people learn to override. Heuristic findings are the same argument one
    step down.

    A check that raised is treated as failure too, whenever `fail_on` is not
    'none': `run_scan` swallows check exceptions into `failed_checks` so one
    broken check cannot take down the whole run, but an incomplete result
    must not read as a clean, passing one — a regression that makes a check
    throw would otherwise produce a green build with only an easily-missed
    annotation.
    """
    if fail_on == 'none':
        return 0
    if result['failed_checks']:
        return 1
    threshold = SEVERITY_RANK[fail_on]
    gate = set(result.get('gate') or [])
    for finding in result['findings']:
        if gates(finding, gate) and SEVERITY_RANK[finding['severity']] <= threshold:
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
        lines += ['| Severity | Tier | Category | File | Problem |',
                  '|---|---|---|---|---|']
        for finding in result['findings']:
            spot = finding['evidence'][0] if finding['evidence'] else {'file': '', 'line': 1}
            problem = finding['problem'].replace('|', '\\|')
            lines.append(
                f"| {finding['severity']} | {finding.get('tier', 'judged')} "
                f"| {finding['category']} "
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
                        default='none',
                        help='exit non-zero when a proven finding is at or above this '
                             'severity; heuristic checks count only when [gate] names them')
    parser.add_argument('--ignore', action='append', metavar='GLOB', default=[],
                        help='glob to exclude; repeatable')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_scan(args.repo, ignore=args.ignore, since=args.since)
    except ConfigError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
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
