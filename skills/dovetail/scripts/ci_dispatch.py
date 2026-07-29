#!/usr/bin/env python3
"""
Headless reviewer fan-out for the scheduled CI job.

This is the *only* place a headless path exists. Interactively, judgement
reviewers run as in-session subagents driven by SKILL.md; that path is richer
and is where all the triage lives. This shim exists because a weekly unattended
audit cannot hold a conversation.

A second dispatch path is a real maintenance risk, and the mitigation is to give
it as little surface of its own as possible:

  * it reads the same rubrics from references/reviewers/
  * it validates against the same schema, via reviewer.validate_findings
  * it contains no triage logic, no rendering, and no write path

Everything interactive stays in SKILL.md, which CI never invokes. A contract
test asserts both paths emit schema-valid findings from the same fixture, so a
schema change cannot land green while quietly breaking this one.

Usage:
  ci_dispatch.py <repo-path> [--profile default|cheap|thorough]
                             [--reviewer NAME ...] [--timeout SECONDS]
                             [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

from claimscan import build_clusters
from config import ConfigError, load_config
from discover import discover
from refgraph import build_graph
from reviewer import (
    ROSTER, ValidationError, escalation_enabled, needs_escalation,
    resolve_roster, validate_findings,
)

REFERENCE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                             '..', 'references')
DEFAULT_TIMEOUT = 900

# Appended on a retry after unparseable output. Deliberately blunt: the first
# attempt already carried the full contract, so what failed was compliance,
# not comprehension.
RETRY_SUFFIX = (
    '\n\n---\n\nIMPORTANT: your previous reply could not be parsed. Reply with '
    'a JSON array and nothing else - no explanation, no preamble, no code '
    'fence. If you found nothing, reply exactly: []'
)
# Reviewers are independent, so they overlap. Bounded because each one is a
# subprocess holding a model connection, not because of local CPU.
MAX_PARALLEL = 4


def rubric_path(name: str) -> str:
    return os.path.normpath(os.path.join(REFERENCE_DIR, 'reviewers', f'{name}.md'))


def read_rubric(name: str) -> str:
    with open(rubric_path(name), encoding='utf-8') as fh:
        return fh.read()


def read_schema() -> str:
    with open(os.path.normpath(os.path.join(REFERENCE_DIR, 'finding-schema.md')),
              encoding='utf-8') as fh:
        return fh.read()


def build_prompt(name: str, repo_root: str, context: dict) -> str:
    """The full prompt for one reviewer: schema, rubric, and its own context.

    The context differs per reviewer - the contradiction reviewer gets clusters,
    everyone else gets a file list - which is the entire point of doing the
    narrowing in Python first.
    """
    parts = [
        f'You are the `{name}` reviewer for dovetail, auditing the repository '
        f'at {repo_root}.',
        '',
        '# Output contract',
        '',
        read_schema(),
        '',
        '# Your rubric',
        '',
        read_rubric(name),
        '',
        '# Your context',
        '',
    ]
    if context.get('clusters') is not None:
        parts.append(
            'Candidate clusters, already grouped by shared entity. Adjudicate '
            'each one. Most clusters are agreement, not contradiction.')
        parts.append('')
        parts.append('```json')
        parts.append(json.dumps(context['clusters'], indent=2, ensure_ascii=False))
        parts.append('```')
    else:
        parts.append('Review these files. Read them, and read whatever else in '
                     'the repository you need in order to judge:')
        parts.append('')
        for path in context.get('files', []):
            parts.append(f'- {path}')
    parts += [
        '',
        'Return only the JSON array. No prose before or after it.',
    ]
    return '\n'.join(parts)


def run_claude(prompt: str, model: str, repo_root: str,
               timeout: int = DEFAULT_TIMEOUT) -> str:
    """One headless `claude -p` call. Raises on any non-zero exit.

    The prompt goes on **stdin**, not argv. Passing it as an argument works
    until it does not: on a 474-file repository the contradiction reviewer's
    prompt carries every candidate cluster as JSON, and the exec call died with
    `OSError: [Errno 7] Argument list too long` - the kernel's ARG_MAX. That is
    a limit that scales with the repository being audited, so the failure only
    appears on exactly the repositories the tool is most useful on. stdin has
    no such ceiling.
    """
    result = subprocess.run(
        ['claude', '-p', '--model', model,
         '--allowedTools', 'Read,Glob,Grep'],
        input=prompt,
        cwd=repo_root, capture_output=True, text=True, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            (result.stderr or result.stdout or 'no output').strip()[:400])
    return result.stdout


def _context_for(name: str, inventory: dict, graph: dict) -> dict:
    docs = [e['path'] for e in inventory['files']
            if e['path'].lower().endswith(('.md', '.markdown'))]
    code = [e['path'] for e in inventory['files'] if e['category'] == 'code']
    if name == 'contradiction':
        return {'clusters': build_clusters(inventory, graph)}
    if name in ('staleness', 'spec-flow'):
        return {'files': docs}
    if name == 'code-hygiene':
        return {'files': code}
    return {'files': docs}


def dispatch(repo_root: str, profile: str = 'default',
             only: list[str] | None = None,
             timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Run the judgement layer headlessly and return findings plus failures."""
    root = os.path.abspath(repo_root)
    config = load_config(root)
    profile = profile or config['profile']
    roster = resolve_roster(profile, config.get('reviewers'))

    inventory = discover(root, ignore=config['ignore'])
    graph = build_graph(root, inventory)

    names = [n for n in ROSTER if roster[n].get('enabled', True)]
    if only:
        names = [n for n in names if n in only]
    # claim-extract feeds the contradiction reviewer rather than emitting
    # findings; the clustering it would serve is already done in Python here.
    names = [n for n in names if ROSTER[n].get('produces') != 'claims']

    findings: list[dict] = []
    failed: list[str] = []

    def run_one(name: str) -> tuple[str, list[dict] | None, str | None]:
        entry = roster[name]
        prompt = build_prompt(name, root, _context_for(name, inventory, graph))
        rejected: list[str] = []
        last_error = None

        # One retry on a transport failure. On the first live run the
        # `convention` reviewer replied in prose and produced nothing at all -
        # a whole reviewer's coverage lost to a formatting slip. Restating the
        # contract and asking again is far cheaper than the lost findings.
        for attempt in range(2):
            try:
                raw = run_claude(
                    prompt if attempt == 0 else prompt + RETRY_SUFFIX,
                    entry['model'], root, timeout=timeout)
            except (RuntimeError, subprocess.TimeoutExpired, FileNotFoundError,
                    OSError) as exc:
                return name, None, f'{type(exc).__name__}: {exc}'[:400]
            try:
                # rejected= makes this lenient: one unsound finding is dropped
                # and named, rather than discarding the reviewer's good work.
                found = validate_findings(raw, name, root, rejected=rejected)
            except ValidationError as exc:
                last_error = str(exc)
                continue
            note = None
            if rejected:
                note = (f'{name}: {len(rejected)} finding(s) dropped as unsound '
                        f'- {rejected[0][:160]}')
            return name, found, note

        return name, None, f'ValidationError after retry: {last_error}'[:400]

    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        for name, got, note in pool.map(run_one, names):
            if got is None:
                # A reviewer that fails is named, never silent: a partial
                # result presented as complete is a lie about coverage.
                failed.append(f'{name} ({note})')
                continue
            if note:
                # Findings survived, but something was dropped - say so rather
                # than let a quietly-reduced result read as a clean one.
                failed.append(note)
            findings.extend(got)

    if escalation_enabled(profile):
        findings, escalation_failures = _escalate(root, findings, roster, timeout)
        failed.extend(escalation_failures)

    return {'findings': findings, 'failed_reviewers': failed,
            'profile': profile, 'reviewers_run': names}


def _escalate(repo_root: str, findings: list[dict], roster: dict,
              timeout: int) -> tuple[list[dict], list[str]]:
    """Re-judge low-confidence findings on the strongest model.

    Opus rates get paid only where a cheaper model was uncertain, rather than
    everywhere as insurance. Upkeep already collects `confidence` and uses it
    for nothing but sort order.
    """
    keep: list[dict] = []
    failures: list[str] = []
    for finding in findings:
        name = finding['source'].split(':', 1)[-1]
        model = roster.get(name, {}).get('model', 'opus')
        if not needs_escalation(finding, model):
            keep.append(finding)
            continue
        prompt = (
            'A cheaper reviewer produced this finding but was not confident. '
            'Judge it: is it real?\n\n'
            '```json\n' + json.dumps(finding, indent=2, ensure_ascii=False) + '\n```\n\n'
            'Reply with a JSON array: the finding with `confidence` corrected '
            'if it is real, or an empty array if it is not.'
        )
        try:
            raw = run_claude(prompt, 'opus', repo_root, timeout=timeout)
            judged = validate_findings(raw, name, repo_root)
        except (RuntimeError, ValidationError, subprocess.TimeoutExpired,
                FileNotFoundError, OSError) as exc:
            # Escalation failing must not lose the finding - it just stays
            # low-confidence, which is honest.
            failures.append(f'escalate:{name} ({type(exc).__name__})')
            keep.append(finding)
            continue
        keep.extend(judged)
    return keep, failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='ci_dispatch.py',
        description='Headless judgement-layer fan-out (scheduled CI job only)')
    parser.add_argument('repo')
    parser.add_argument('--profile', choices=['default', 'cheap', 'thorough'],
                        default=None)
    parser.add_argument('--reviewer', action='append', dest='reviewers',
                        metavar='NAME', help='restrict to this reviewer; repeatable')
    parser.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT,
                        metavar='SECONDS', help='per-reviewer timeout')
    parser.add_argument('--out', metavar='FILE', help='write JSON here as well as stdout')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = dispatch(args.repo, profile=args.profile,
                          only=args.reviewers, timeout=args.timeout)
    except ConfigError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2

    rendered = json.dumps(result, indent=2, ensure_ascii=False)
    print(rendered)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as fh:
            fh.write(rendered + '\n')

    # Never fails the build. This job's output is a report, not a gate - that
    # is the whole reason the deterministic job is separate.
    return 0


if __name__ == '__main__':
    sys.exit(main())
