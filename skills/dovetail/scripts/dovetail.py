#!/usr/bin/env python3
"""
dovetail's run driver: the verbs SKILL.md calls during an interactive run.

The scan result and the reviewer clusters are far too big to read into an
agent's context on a real repository. On one of about 900 files they came to
250 KB and 300 KB, roughly 140k tokens before the user saw the first
question, and the scan was read again after every fix. So a run keeps its
state on disk, and each verb prints only what the next step needs:

  scan            run the scan, start a new run, print a short summary
  next            print the next finding, rendered, with its options
  decide          record a decision; every value arrives as an argument
  check           report any file that changed and that dovetail did not write
  rescan          scan again and say which queued findings the last fix resolved
  prepare-review  write one prompt file per reviewer shard
  wave            hand out the next few shards to dispatch
  import-review   validate the shard results that have landed, and queue them

Run state lives in ~/.dbhq/dovetail/runs/<repo>-<hash>/. DOVETAIL_HOME moves
the root. The only write into the scanned repository is `decide intentional`
or `decide wontfix`, which appends one line to .dovetail/decisions.jsonl. Fixes
are applied by the agent, on the user's approval, and `decide fix` records
which files that fix changed.

Usage:
  dovetail.py scan [--repo PATH] [--since REF] [--ignore GLOB ...]
  dovetail.py next [--repo PATH] [--json | --batch]
  dovetail.py decide [--repo PATH] ID [ID ...] fix --files PATH [PATH ...]
  dovetail.py decide [--repo PATH] ID [ID ...] skip
  dovetail.py decide [--repo PATH] ID [ID ...] intentional|wontfix --reason TEXT [--summary TEXT]
  dovetail.py check [--repo PATH]
  dovetail.py rescan [--repo PATH]
  dovetail.py prepare-review [--repo PATH] [--profile P] [--reviewer NAME ...]
  dovetail.py wave [--repo PATH] [--size N]
  dovetail.py import-review [--repo PATH]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

import bootstrap

# Before any dovetail import that reaches tomllib.
bootstrap.ensure()

import ci_dispatch  # noqa: E402
import fixes  # noqa: E402
from config import ConfigError  # noqa: E402
from reviewer import (  # noqa: E402
    MATCH, MOVED, ValidationError, escalation_enabled, needs_escalation,
    quote_verdict, validate_findings,
)
from scan import SEVERITY_RANK, run_scan  # noqa: E402
from store import DECISIONS_REL, append_decision, load_decisions  # noqa: E402

SCRIPT = os.path.abspath(__file__)
STATE_VERSION = 1

# Shards handed out per wave. Each is a subagent holding a model connection,
# and a wave has to land before the next one goes out.
DEFAULT_WAVE = 4
MAX_WAVE = 5

CONFIDENCE_RANK = {'high': 0, 'medium': 1, 'low': 2}
DECIDED = ('fix', 'skip', 'intentional', 'wontfix')
# Statuses that take a finding out of the run for good.
GONE = ('resolved', 'escalated')

# Printed between what the user sees and what only the agent needs.
AGENT_LINE = '=== for the agent, not the user ==='

# Files named under a finding's blast radius; the rest are counted.
RADIUS_SHOWN = 8
# Findings shown together by `next --batch`. The rest wait for the next call.
BATCH_SHOWN = 20


class RunError(Exception):
    """The verb cannot run: no run started, an unknown id, a bad argument."""


# --------------------------------------------------------------------------
# Where state lives

def state_root() -> str:
    return (os.environ.get('DOVETAIL_HOME')
            or os.path.join(os.path.expanduser('~'), '.dbhq', 'dovetail'))


def run_dir(repo_root: str) -> str:
    """One directory per repository, named so a person can tell them apart."""
    real = os.path.realpath(repo_root)
    digest = hashlib.sha256(real.encode('utf-8')).hexdigest()[:12]
    name = re.sub(r'[^A-Za-z0-9._-]', '_', os.path.basename(real)) or 'repo'
    return os.path.join(state_root(), 'runs', f'{name}-{digest}')


def _private_dir(path: str) -> None:
    """Create `path` readable by its owner only. An existing one is left alone."""
    if os.path.isdir(path):
        return
    parent = os.path.dirname(path)
    if parent and parent != path:
        _private_dir(parent)
    os.mkdir(path)
    os.chmod(path, 0o700)


def _ensure_run_dir(repo_root: str) -> str:
    path = run_dir(repo_root)
    _private_dir(path)
    return path


def _write_json(path: str, data: object) -> None:
    tmp = f'{path}.tmp'
    with open(tmp, 'w', encoding='utf-8') as fh:
        json.dump(data, fh, indent=1, ensure_ascii=False)
        fh.write('\n')
    os.replace(tmp, path)


def _read_json(path: str) -> object:
    with open(path, encoding='utf-8') as fh:
        return json.load(fh)


def load_state(repo_root: str) -> dict:
    path = os.path.join(run_dir(repo_root), 'run.json')
    if not os.path.exists(path):
        raise RunError(f'no run for {repo_root}. Run `scan` first.')
    state = _read_json(path)
    if not isinstance(state, dict) or state.get('version') != STATE_VERSION:
        raise RunError('the run state is from another version. Run `scan` again.')
    return state


def save_state(repo_root: str, state: dict) -> None:
    _write_json(os.path.join(run_dir(repo_root), 'run.json'), state)


# --------------------------------------------------------------------------
# Write safety
#
# `git status --porcelain` cannot see a second edit to a file that is already
# modified: the line reads ` M file` before and after. So the check hashes the
# content of every file git can see, and compares hashes.

def _tree_files(repo_root: str) -> list[str]:
    result = subprocess.run(
        ['git', '-C', repo_root, 'ls-files', '-z', '--cached', '--others',
         '--exclude-standard'],
        capture_output=True, check=True)
    names = result.stdout.decode('utf-8', 'surrogateescape').split('\0')
    return sorted({name for name in names if name})


def _hash_file(path: str) -> str | None:
    digest = hashlib.sha256()
    try:
        with open(path, 'rb') as fh:
            for block in iter(lambda: fh.read(1 << 16), b''):
                digest.update(block)
    except FileNotFoundError:
        return None
    except IsADirectoryError:
        return 'directory'  # a submodule, or a symlink to a directory
    except OSError:
        return 'unreadable'
    return digest.hexdigest()


def take_snapshot(repo_root: str) -> dict[str, str | None]:
    return {rel: _hash_file(os.path.join(repo_root, rel))
            for rel in _tree_files(repo_root)}


def changed_files(repo_root: str, snapshot: dict[str, str | None]) -> list[str]:
    """Every path whose content differs from the snapshot, added and removed included."""
    now = take_snapshot(repo_root)
    return sorted(path for path in set(snapshot) | set(now)
                  if snapshot.get(path) != now.get(path))


def _snapshot_path(repo_root: str) -> str:
    return os.path.join(run_dir(repo_root), 'snapshot.json')


def _load_snapshot(repo_root: str) -> dict:
    try:
        return _read_json(_snapshot_path(repo_root))
    except FileNotFoundError as exc:
        raise RunError('no snapshot for this run. Run `scan` first.') from exc


def _accept(repo_root: str, snapshot: dict, paths: list[str]) -> None:
    """Take dovetail's own writes into the snapshot."""
    for rel in paths:
        snapshot[rel] = _hash_file(os.path.join(repo_root, rel))
    _write_json(_snapshot_path(repo_root), snapshot)


def _relative(repo_root: str, path: str) -> str:
    full = os.path.realpath(os.path.join(repo_root, path))
    root = os.path.realpath(repo_root)
    if os.path.commonpath([root, full]) != root:
        raise RunError(f'{path} is outside the repository')
    return os.path.relpath(full, root).replace(os.sep, '/')


# --------------------------------------------------------------------------
# Findings in a run

def short_id(finding_id: str) -> str:
    if finding_id.startswith('sha256:'):
        return finding_id[7:19]
    return hashlib.sha256(finding_id.encode('utf-8')).hexdigest()[:12]


def layer_of(finding: dict) -> str:
    return 'judged' if finding.get('source', '').startswith('reviewer:') else 'exact'


def _entry(finding: dict, *, model: str | None = None) -> dict:
    return {'finding': finding, 'status': 'queued', 'layer': layer_of(finding),
            'model': model, 'short': short_id(finding['id'])}


def order_key(entry: dict) -> tuple:
    """Blast radius, then severity, then confidence. Certain before judged."""
    finding = entry['finding']
    first = finding['evidence'][0] if finding.get('evidence') else {}
    return (-len(finding.get('blast_radius') or []),
            SEVERITY_RANK.get(finding.get('severity'), 3),
            CONFIDENCE_RANK.get(finding.get('confidence', 'high'), 1),
            0 if entry['layer'] == 'exact' else 1,
            finding.get('category', ''),
            first.get('file', ''), first.get('line', 0))


def _queued(state: dict) -> list[dict]:
    return sorted((e for e in state['entries'].values() if e['status'] == 'queued'),
                  key=order_key)


def _live(state: dict) -> list[dict]:
    """Findings still part of the run: queued, or decided in it."""
    return [e for e in state['entries'].values()
            if e['status'] == 'queued' or e['status'] in DECIDED]


def find_entry(state: dict, wanted: str) -> dict:
    """An entry by full id, short id, or a prefix of the short id."""
    entries = state['entries']
    if wanted in entries:
        return entries[wanted]
    hits = [e for e in entries.values() if e['short'].startswith(wanted)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise RunError(f'no finding {wanted!r} in this run')
    raise RunError(f'{wanted!r} matches {len(hits)} findings; give more of the id')


def _where(finding: dict) -> str:
    first = finding['evidence'][0] if finding.get('evidence') else None
    return f"{first['file']}:{first['line']}" if first else finding.get('category', '?')


# --------------------------------------------------------------------------
# Rendering one finding

def _quote_block(quote: str) -> list[str]:
    return ['> ' + line if line.strip() else '>' for line in quote.split('\n')]


def render_finding(entry: dict, index: int, total: int) -> str:
    """One finding as clean markdown. Never one fenced block: diffs only."""
    finding = entry['finding']
    head = f"**[{index}/{total}] {finding['category']} · {finding['severity']}**"
    if entry['layer'] == 'exact':
        head += ' · exact'
        if finding.get('tier'):
            head += f" · {finding['tier']}"
    else:
        head += ' · judged'
        if entry.get('model'):
            head += f" · {entry['model']}"
        head += f" · {finding.get('confidence', 'medium')} confidence"
    lines = [head, '', finding['problem'].strip(), '', '**Evidence**']
    for item in finding.get('evidence', []):
        note = f" - {item['note']}" if item.get('note') else ''
        lines += ['', f"`{item['file']}:{item['line']}`{note}"]
        quote = str(item.get('quote') or '').strip()
        if quote:
            lines += _quote_block(quote)
    fix = finding.get('fix') or {}
    if fix.get('diff'):
        lines += ['', '**Fix**', '', '```diff', fix['diff'].rstrip('\n'), '```']
    elif str(finding.get('suggestion') or '').strip():
        lines += ['', '**Suggestion**', '', finding['suggestion'].strip()]
    radius = finding.get('blast_radius') or []
    if radius:
        shown = ' · '.join(f'`{path}`' for path in radius[:RADIUS_SHOWN])
        if len(radius) > RADIUS_SHOWN:
            shown += f' and {len(radius) - RADIUS_SHOWN} more'
        lines += ['', f'**Blast radius** - {len(radius)} further '
                      f"{'file cites' if len(radius) == 1 else 'files cite'} this", shown]
    return '\n'.join(lines)


def options_for(entry: dict) -> tuple[list[str], str]:
    """The options for the question box, and whether one may be recommended.

    SKILL.md holds the rules; this applies the ones a program can apply, so the
    agent does not re-derive them from the finding every time.
    """
    finding = entry['finding']
    fix = finding.get('fix') or {}
    keep = ['Skip - leave it for this run; it comes back on the next one',
            'Mark intentional - record it in .dovetail/decisions.jsonl with a '
            'reason; it never comes back']
    if entry['layer'] == 'judged':
        options = ['one option per candidate resolution, each naming the file it '
                   'changes (these are the actions; there is no plain Fix)'] + keep
        ssot = finding.get('ssot_direction', 'n/a')
        confidence = finding.get('confidence', 'medium')
        if ssot in ('a', 'b') and confidence == 'high' and len(finding['evidence']) >= 2:
            index = 0 if ssot == 'a' else 1
            other = finding['evidence'][1 - index]['file']
            advice = (f"allowed: ssot_direction is {ssot} and confidence is high, so "
                      f"{finding['evidence'][index]['file']} is authoritative and "
                      f'{other} changes. Say why above the box')
        elif ssot == 'uncertain':
            advice = 'none: ssot_direction is uncertain, so this is the user\'s call'
        elif confidence != 'high':
            advice = f'none: the reviewer returned {confidence} confidence'
        else:
            advice = 'none: the finding names no authoritative side'
        return options, advice

    kind = fix.get('kind', 'none')
    if kind == 'none':
        options = ['Fix it - draft the edit the suggestion describes, show the diff, '
                   'then apply it'] + keep
        advice = 'none: there is no computed fix, so the edit is not mechanical'
    else:
        options = ['Apply the fix - make the edit shown above'] + keep
        if kind == 'delete':
            advice = 'none: the fix deletes something, so it is always asked'
        else:
            advice = 'allowed: an exact finding with one mechanical fix'
    return options, advice


# --------------------------------------------------------------------------
# Verbs

def _summary_lines(root: str, result: dict) -> list[str]:
    counts = result['counts']
    total = sum(counts.values())
    lines = [
        f"dovetail · {os.path.basename(root)} · {result['file_count']:,} files, "
        f"{result['edge_count']:,} references",
        f"exact       {total} finding{'' if total == 1 else 's'} "
        f"({counts['high']} high · {counts['medium']} medium · {counts['low']} low)",
        f"suppressed  {result['suppressed']} by prior decisions",
    ]
    stale = result.get('stale_decisions') or []
    if stale:
        lines.append(f'stale       {len(stale)} decision(s) match no current finding; '
                     'a moved file changes the id, so re-record any that still apply')
        for row in stale[:5]:
            summary = str(row.get('summary') or '(no summary)').split('\n')[0][:100]
            lines.append(f"            {row['id'][:15]}  {summary}")
        if len(stale) > 5:
            lines.append(f'            and {len(stale) - 5} more in the scan JSON')
    proven = sum(1 for f in result['findings'] if f.get('tier') == 'proven')
    if total:
        lines.insert(2, f'tiers       {proven} proven, {total - proven} heuristic')
    failed = result['failed_checks']
    if failed:
        shown = ', '.join(name[:120] for name in failed[:5])
        more = f' and {len(failed) - 5} more' if len(failed) > 5 else ''
        lines.append(f'failed      {len(failed)} check(s), findings incomplete: {shown}{more}')
    else:
        lines.append('failed      none')
    by_category: dict[str, int] = {}
    for finding in result['findings']:
        by_category[finding['category']] = by_category.get(finding['category'], 0) + 1
    if by_category:
        ranked = sorted(by_category.items(), key=lambda kv: (-kv[1], kv[0]))
        lines.append('categories  ' + ' · '.join(f'{name} {n}' for name, n in ranked))
    return lines


def cmd_scan(args: argparse.Namespace) -> int:
    root = os.path.realpath(args.repo)
    result = run_scan(root, ignore=args.ignore, since=args.since)
    directory = _ensure_run_dir(root)
    # A new run starts clean: shards from an old one would import stale work.
    shutil.rmtree(os.path.join(directory, 'review'), ignore_errors=True)
    state = {
        'version': STATE_VERSION,
        'repo': root,
        'started': datetime.datetime.now(datetime.timezone.utc).isoformat(timespec='seconds'),
        'options': {'since': args.since, 'ignore': list(args.ignore)},
        'summary': {key: result[key] for key in
                    ('file_count', 'edge_count', 'suppressed', 'failed_checks', 'profile')},
        'entries': {f['id']: _entry(f) for f in result['findings']},
        'last_fix': None,
    }
    _write_json(_snapshot_path(root), take_snapshot(root))
    save_state(root, state)
    print('\n'.join(_summary_lines(root, result)))
    print(f'run         {directory}')
    print(f'next        python3 {SCRIPT} next --repo {root}')
    return 0


def cmd_next(args: argparse.Namespace) -> int:
    root = os.path.realpath(args.repo)
    state = load_state(root)
    queued = _queued(state)
    live = _live(state)
    if not queued:
        tally: dict[str, int] = {}
        for entry in state['entries'].values():
            tally[entry['status']] = tally.get(entry['status'], 0) + 1
        parts = [f'{n} {status}' for status, n in sorted(tally.items())]
        print('Queue empty. ' + (', '.join(parts) if parts else 'No findings.'))
        pending = _review_pending(root)
        if pending:
            print(f'{pending} reviewer shard(s) have not been imported yet. '
                  'Run import-review when they land.')
        return 0
    entry = queued[0]
    if args.json:
        print(json.dumps(entry['finding'], indent=2, ensure_ascii=False))
        return 0
    if args.batch:
        return _print_batch(root, entry, queued)
    decided = sum(1 for e in live if e['status'] in DECIDED)
    print(render_finding(entry, decided + 1, len(live)))
    options, advice = options_for(entry)
    print()
    print(AGENT_LINE)
    print(f"id         {entry['short']}")
    print(f"header     {entry['finding']['category'][:12]}")
    for option in options:
        print(f'option     {option}')
    print(f'recommend  {advice}')
    fix = entry['finding'].get('fix') or {}
    if fix.get('kind') == 'edit':
        print(f"fix        the scan computed one fix, the diff above, to {', '.join(fix['files'])}")
    else:
        print('fix        none computed; draft the edit and show it before applying it')
    batch = _batch_of(entry, queued)
    if len(batch) > 1:
        print(f"batch      {len(batch)} queued {entry['finding']['category']} findings are "
              f'batch_eligible. To offer them in one box: python3 {SCRIPT} next --batch '
              f'--repo {root}')
    else:
        print('batch      no: fix this one on its own')
    files = ' '.join(fix['files']) if fix.get('kind') == 'edit' else 'PATH...'
    print(f"record     python3 {SCRIPT} decide --repo {root} {entry['short']} "
          f'fix --files {files} | skip | intentional --reason TEXT')
    return 0


def _batch_of(entry: dict, queued: list[dict]) -> list[dict]:
    """Queued findings that may be fixed together with `entry`: its batch_eligible class."""
    finding = entry['finding']
    if not finding.get('batch_eligible'):
        return []
    return [e for e in queued if e['finding'].get('batch_eligible')
            and e['finding']['category'] == finding['category']]


def _print_batch(root: str, entry: dict, queued: list[dict]) -> int:
    """The combined diff of a batch_eligible class, for one box and one confirmation."""
    batch = _batch_of(entry, queued)
    if len(batch) < 2:
        print('No batch: the next finding has to be fixed on its own. Run next.')
        return 0
    shown = batch[:BATCH_SHOWN]
    combined = fixes.combine(root, [item['finding']['fix'] for item in shown])
    if combined['kind'] != 'edit':
        print('No batch: these fixes touch the same text, so they have to be made one at '
              'a time. Run next.')
        return 0
    category = entry['finding']['category']
    print(f'**{len(batch)} {category} findings, each with one mechanical fix**')
    print()
    print('```diff')
    print(combined['diff'].rstrip('\n'))
    print('```')
    if len(batch) > len(shown):
        print(f'\nThe other {len(batch) - len(shown)} come in the next batch.')
    files = combined['files']
    print()
    print(AGENT_LINE)
    print(f'header     {category[:12]}')
    print(f'option     Apply all {len(shown)} - make every edit shown above')
    print('option     One at a time - go through them with next')
    print('recommend  none: a batch is the user\'s call')
    print(f"record     python3 {SCRIPT} decide --repo {root} "
          f"{' '.join(item['short'] for item in shown)} fix --files {' '.join(files)}")
    return 0


def cmd_decide(args: argparse.Namespace) -> int:
    root = os.path.realpath(args.repo)
    state = load_state(root)
    entries = [find_entry(state, wanted) for wanted in args.ids]
    verdict = args.verdict
    status = 0
    shorts = ', '.join(entry['short'] for entry in entries)

    if verdict in ('intentional', 'wontfix'):
        reason = (args.reason or '').strip()
        if not reason:
            raise RunError(f'{verdict} needs --reason: the ledger records why, '
                           'and a guessed reason is worse than none')
        for entry in entries:
            finding = entry['finding']
            summary = (args.summary or finding['problem']).strip().split('\n')[0][:200]
            # `layer` tells the scan whether it can check the row later: it
            # re-derives exact findings, never a reviewer's.
            append_decision(root, {
                'id': finding['id'], 'verdict': verdict, 'reason': reason,
                'at': datetime.date.today().isoformat(), 'summary': summary,
                'layer': entry['layer'],
            })
        snapshot = _load_snapshot(root)
        _accept(root, snapshot, [_relative(root, DECISIONS_REL)])
        print(f'recorded {verdict} for {shorts} in {DECISIONS_REL}')
    elif verdict == 'fix':
        if not args.files:
            raise RunError('fix needs --files: every file the fix changed')
        expected = [_relative(root, path) for path in args.files]
        snapshot = _load_snapshot(root)
        unexpected = [path for path in changed_files(root, snapshot)
                      if path not in expected]
        _accept(root, snapshot, expected)
        state['last_fix'] = [entry['finding']['id'] for entry in entries]
        print(f"recorded fix for {shorts} ({', '.join(expected)})")
        if unexpected:
            print('STOP: these files changed and dovetail did not change them:')
            for path in unexpected[:20]:
                print(f'  {path}')
            if len(unexpected) > 20:
                print(f'  and {len(unexpected) - 20} more')
            print('Something else is writing to the tree. End the run and tell the user.')
            status = 3
        else:
            print(f'Now run: python3 {SCRIPT} rescan --repo {root}')
    else:
        print(f'skipped {shorts} for this run')

    for entry in entries:
        entry['status'] = verdict
    save_state(root, state)
    return status


def cmd_check(args: argparse.Namespace) -> int:
    root = os.path.realpath(args.repo)
    load_state(root)
    changed = changed_files(root, _load_snapshot(root))
    if not changed:
        print('clean: nothing has changed that dovetail did not write')
        return 0
    print('STOP: these files changed and dovetail did not change them:')
    for path in changed[:20]:
        print(f'  {path}')
    if len(changed) > 20:
        print(f'  and {len(changed) - 20} more')
    return 1


def cmd_rescan(args: argparse.Namespace) -> int:
    root = os.path.realpath(args.repo)
    state = load_state(root)
    options = state['options']
    result = run_scan(root, ignore=options['ignore'], since=options['since'])
    now = {f['id']: f for f in result['findings']}
    entries = state['entries']

    resolved: list[dict] = []
    for entry in entries.values():
        # Only what is still open can be resolved by a fix. A fixed finding
        # that is still reported is named below instead.
        if entry['status'] not in ('queued', 'skip'):
            continue
        if entry['layer'] == 'exact':
            if entry['finding']['id'] not in now:
                resolved.append(entry)
        elif entry['status'] == 'queued' and not _evidence_holds(root, entry['finding']):
            # A judged finding cannot be re-derived by a scan, but its quotes
            # can be re-checked. A fix that rewrote a quoted line has changed
            # what the finding is about.
            resolved.append(entry)
    for entry in resolved:
        entry['status'] = 'resolved'

    fresh = [f for fid, f in now.items() if fid not in entries]
    for finding in fresh:
        entries[finding['id']] = _entry(finding)
    for key in ('file_count', 'edge_count', 'suppressed', 'failed_checks'):
        state['summary'][key] = result[key]

    last = state.get('last_fix') or []
    last = [last] if isinstance(last, str) else last
    save_state(root, state)

    by_fix = [e for e in resolved if e['finding']['id'] not in last]
    remaining = len(_queued(state))
    for fixed in last:
        fixed_entry = entries.get(fixed)
        if fixed_entry is not None and fixed_entry['layer'] == 'exact' and fixed in now:
            print(f"! the fix for {fixed_entry['short']} did not resolve it: "
                  f"{_where(fixed_entry['finding'])} is still reported")
    if by_fix:
        places = ', '.join(_where(e['finding']) for e in by_fix[:6])
        more = f' and {len(by_fix) - 6} more' if len(by_fix) > 6 else ''
        print(f'✓ applied. {len(by_fix)} queued finding(s) resolved by this fix '
              f'({places}{more}) - {remaining} remaining.')
    else:
        print(f'✓ applied. No other queued finding resolved - {remaining} remaining.')
    if fresh:
        places = ', '.join(_where(f) for f in fresh[:6])
        print(f'! {len(fresh)} new finding(s) appeared since the last scan ({places}). '
              'They are queued.')
    if result['failed_checks']:
        print(f"! {len(result['failed_checks'])} check(s) failed on this scan; "
              'findings incomplete')
    return 0


def _evidence_holds(root: str, finding: dict) -> bool:
    for item in finding.get('evidence', []):
        verdict, _ = quote_verdict(root, dict(item))
        if verdict not in (MATCH, MOVED):
            return False
    return True


# --------------------------------------------------------------------------
# Reviewer shards

def _review_dir(root: str) -> str:
    return os.path.join(run_dir(root), 'review')


def _manifest_path(root: str) -> str:
    return os.path.join(_review_dir(root), 'manifest.json')


def _load_manifest(root: str) -> dict:
    try:
        return _read_json(_manifest_path(root))
    except FileNotFoundError as exc:
        raise RunError('no reviewer shards for this run. Run `prepare-review` first.') from exc


def _review_pending(root: str) -> int:
    try:
        manifest = _read_json(_manifest_path(root))
    except (FileNotFoundError, json.JSONDecodeError):
        return 0
    return sum(1 for s in manifest['shards'] if s['status'] in ('pending', 'dispatched'))


_OUTPUT_NOTE = (
    '\n\n---\n\nThis is an interactive run. Write the JSON array to this file, '
    'and nothing else into it:\n\n{result}\n\nDo not edit any file in the '
    'repository. Reply with one line: how many findings you wrote.\n'
)

# Appended when a shard's output could not be parsed, before it goes out once
# more. The scheduled job retries the same way (ci_dispatch.RETRY_SUFFIX): the
# first attempt already carried the contract, so what failed was compliance.
_RETRY_NOTE = (
    '\n---\n\nIMPORTANT: this shard has been handed out before, and what was '
    'written to the file above could not be parsed. Write a JSON array to that '
    'file and nothing else - no explanation, no preamble, no code fence. If you '
    'found nothing, write exactly: []\n'
)


def _write_shard(root: str, manifest: dict, *, shard_id: str, reviewer: str,
                 model: str, effort: str, prompt: str, kind: str = 'review',
                 held: str | None = None, items: int = 0) -> None:
    directory = _review_dir(root)
    prompt_path = os.path.join(directory, f'{shard_id}.md')
    result_path = os.path.join(directory, f'{shard_id}.json')
    with open(prompt_path, 'w', encoding='utf-8') as fh:
        fh.write(prompt + _OUTPUT_NOTE.format(result=result_path))
    manifest['shards'].append({
        'id': shard_id, 'reviewer': reviewer, 'model': model, 'effort': effort,
        'kind': kind, 'held': held, 'items': items, 'status': 'pending',
        'attempt': 1, 'prompt': prompt_path, 'result': result_path, 'note': None,
    })


def _retry_shard(shard: dict) -> None:
    """Hand an unparseable shard out once more, with the contract restated."""
    with open(shard['prompt'], 'a', encoding='utf-8') as fh:
        fh.write(_RETRY_NOTE)
    os.remove(shard['result'])
    shard['attempt'] = shard.get('attempt', 1) + 1
    shard['status'] = 'pending'


def cmd_prepare_review(args: argparse.Namespace) -> int:
    root = os.path.realpath(args.repo)
    state = load_state(root)
    # The scheduled job takes its shards from the same plan, so an interactive
    # run and a CI run of the same repository shard it the same way.
    plan = ci_dispatch.plan_shards(root, profile=args.profile, only=args.reviewers,
                                   ignore=state['options']['ignore'])

    directory = _review_dir(root)
    shutil.rmtree(directory, ignore_errors=True)
    _private_dir(directory)
    manifest = {'profile': plan['profile'], 'shards': []}
    for shard in plan['shards']:
        _write_shard(root, manifest, shard_id=f"{shard['reviewer']}-{shard['index']:02d}",
                     reviewer=shard['reviewer'], model=shard['model'],
                     effort=shard['effort'], prompt=shard['prompt'],
                     items=shard['items'])
    _write_json(_manifest_path(root), manifest)

    lines = []
    for name in plan['reviewers']:
        entry = plan['roster'][name]
        mine = [s for s in plan['shards'] if s['reviewer'] == name]
        unit = 'clusters' if name == 'contradiction' else 'files'
        lines.append(f"  {name:<14} {entry['model'] + '/' + entry['effort']:<14} "
                     f"{len(mine):>3} shard(s), {sum(s['items'] for s in mine)} {unit}")
    total = len(manifest['shards'])
    print(f"review      {len(plan['reviewers'])} reviewer(s), {total} shard(s), "
          f"profile {plan['profile']}")
    print('\n'.join(lines))
    print(f'dispatch    python3 {SCRIPT} wave --repo {root}')
    return 0


def cmd_wave(args: argparse.Namespace) -> int:
    root = os.path.realpath(args.repo)
    manifest = _load_manifest(root)
    size = max(1, min(args.size, MAX_WAVE))
    waiting = [s for s in manifest['shards'] if s['status'] == 'dispatched']
    pending = [s for s in manifest['shards'] if s['status'] == 'pending']
    if not pending:
        print(f'all shards handed out; {len(waiting)} not imported yet')
        return 0
    wave = pending[:size]
    for shard in wave:
        shard['status'] = 'dispatched'
    _write_json(_manifest_path(root), manifest)
    left = len(pending) - len(wave)
    print(f'wave        {len(wave)} shard(s), one agent each; {left} still to hand out')
    for shard in wave:
        print(f"{shard['id']:<18} model {shard['model']}, effort {shard['effort']}")
        print(f"  prompt    Read {shard['prompt']} and do exactly what it says.")
    print(f'then        python3 {SCRIPT} import-review --repo {root}')
    return 0


def _rejection_kind(message: str) -> str:
    if 'edited after' in message:
        return 'stale'
    if 'fabricated' in message:
        return 'fabricated'
    return 'invalid'


def cmd_import_review(args: argparse.Namespace) -> int:
    root = os.path.realpath(args.repo)
    state = load_state(root)
    manifest = _load_manifest(root)
    decisions = load_decisions(root)
    profile = manifest['profile']
    entries = state['entries']

    imported = queued = known = suppressed = held = released = 0
    dropped: list[str] = []
    failed: list[str] = []
    retried: list[str] = []
    for shard in list(manifest['shards']):
        if shard['status'] not in ('pending', 'dispatched'):
            continue
        if not os.path.exists(shard['result']):
            continue
        with open(shard['result'], encoding='utf-8') as fh:
            raw = fh.read()
        rejected: list[str] = []
        try:
            found = validate_findings(raw, shard['reviewer'], root, rejected=rejected)
        except ValidationError as exc:
            shard['note'] = str(exc)[:300]
            if shard.get('attempt', 1) < 2:
                _retry_shard(shard)
                retried.append(shard['id'])
                continue
            shard['status'] = 'failed'
            failed.append(f"{shard['id']}: {shard['note'][:160]}")
            held_entry = entries.get(shard['held']) if shard['kind'] == 'escalate' else None
            if held_entry is not None and held_entry['status'] == 'held':
                # A failed escalation must not lose the finding. It stays at the
                # confidence its reviewer gave it, as it does in the scheduled job.
                held_entry['status'] = 'queued'
                released += 1
            continue
        shard['status'] = 'imported'
        imported += 1
        if rejected:
            kinds: dict[str, int] = {}
            for message in rejected:
                kind = _rejection_kind(message)
                kinds[kind] = kinds.get(kind, 0) + 1
            dropped.append(f"{shard['id']}: " + ', '.join(
                f'{n} {kind}' for kind, n in sorted(kinds.items())))
        if shard['kind'] == 'escalate' and shard['held'] in entries:
            entries[shard['held']]['status'] = 'escalated'
        for finding in found:
            if finding['id'] in decisions:
                suppressed += 1
                continue
            existing = entries.get(finding['id'])
            if existing is not None and existing['status'] not in ('held', 'escalated'):
                known += 1
                continue
            entry = _entry(finding, model=shard['model'])
            entries[finding['id']] = entry
            if (shard['kind'] == 'review' and escalation_enabled(profile)
                    and needs_escalation(finding, shard['model'])):
                entry['status'] = 'held'
                held += 1
                _write_shard(root, manifest, shard_id=f"escalate-{entry['short']}",
                             reviewer=shard['reviewer'], model='opus', effort='high',
                             prompt=ci_dispatch.escalation_prompt(finding),
                             kind='escalate', held=finding['id'], items=1)
            else:
                queued += 1

    _write_json(_manifest_path(root), manifest)
    save_state(root, state)
    waiting = sum(1 for s in manifest['shards'] if s['status'] in ('pending', 'dispatched'))
    print(f'imported    {imported} shard(s): {queued} finding(s) queued, '
          f'{known} already known, {suppressed} suppressed by prior decisions')
    if held:
        print(f'escalate    {held} low-confidence finding(s) held for opus; '
              'the next wave hands them out')
    if dropped:
        print('dropped     ' + ' · '.join(dropped[:8])
              + (f' and {len(dropped) - 8} more' if len(dropped) > 8 else ''))
    if retried:
        print(f'retry       {len(retried)} shard(s) wrote something that is not a JSON '
              f"array, and go out once more in the next wave: {', '.join(retried[:8])}"
              + (f' and {len(retried) - 8} more' if len(retried) > 8 else ''))
    if failed:
        print(f'failed      {len(failed)} shard(s), their findings are missing:')
        for line in failed[:8]:
            print(f'  {line}')
    if released:
        print(f'released    {released} held finding(s) whose escalation failed are '
              'queued at the confidence their reviewer gave')
    print(f'waiting     {waiting} shard(s) not yet imported')
    return 0


# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='dovetail.py', description='Drive an interactive dovetail run.')
    sub = parser.add_subparsers(dest='verb', required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--repo', default='.', help='repository (default: here)')

    p = sub.add_parser('scan', parents=[common], help='scan and start a new run')
    p.add_argument('--since', metavar='REF')
    p.add_argument('--ignore', action='append', metavar='GLOB', default=[])
    p.set_defaults(run=cmd_scan)

    p = sub.add_parser('next', parents=[common], help='show the next finding')
    p.add_argument('--json', action='store_true', help='the raw finding instead')
    p.add_argument('--batch', action='store_true',
                   help="the combined diff of the next finding's batch_eligible class")
    p.set_defaults(run=cmd_next)

    p = sub.add_parser('decide', parents=[common], help='record a decision')
    p.add_argument('ids', nargs='+', metavar='ID',
                   help='finding id, as printed by next; several for a batch')
    p.add_argument('verdict', choices=DECIDED)
    p.add_argument('--reason', help='why; required for intentional and wontfix')
    p.add_argument('--summary', help='one line for the ledger (default: the problem)')
    p.add_argument('--files', nargs='+', metavar='PATH',
                   help='for fix: every file the fix changed')
    p.set_defaults(run=cmd_decide)

    p = sub.add_parser('check', parents=[common],
                       help='has anything changed that dovetail did not write?')
    p.set_defaults(run=cmd_check)

    p = sub.add_parser('rescan', parents=[common],
                       help='scan again and report what the last fix resolved')
    p.set_defaults(run=cmd_rescan)

    p = sub.add_parser('prepare-review', parents=[common],
                       help='write one prompt file per reviewer shard')
    p.add_argument('--profile', choices=['default', 'cheap', 'thorough'])
    p.add_argument('--reviewer', action='append', dest='reviewers', metavar='NAME')
    p.set_defaults(run=cmd_prepare_review)

    p = sub.add_parser('wave', parents=[common], help='hand out the next shards')
    p.add_argument('--size', type=int, default=DEFAULT_WAVE,
                   help=f'shards in this wave (default {DEFAULT_WAVE}, most {MAX_WAVE})')
    p.set_defaults(run=cmd_wave)

    p = sub.add_parser('import-review', parents=[common],
                       help='validate and queue the shard results that have landed')
    p.set_defaults(run=cmd_import_review)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.run(args)
    except (RunError, ConfigError, ValueError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f'error: git failed: {exc}', file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
