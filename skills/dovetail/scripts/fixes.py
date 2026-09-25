#!/usr/bin/env python3
"""
Computed fixes, batch eligibility and blast radius for scan findings.

SKILL.md orders the queue by blast radius, offers a mechanical fix when one
exists, and batch-approves a class of findings that each have exactly one.
Those have to be facts the scan computes. Left to the agent, it invents the
fix and the order, and two runs on one repository triage differently.

A fix is offered only when there is exactly one candidate. Two plausible
targets is a choice, and a choice belongs to the user.

Nothing here writes a file. A fix is a unified diff for the agent to show and,
on the user's say-so, apply.
"""

from __future__ import annotations

import difflib
import os
import posixpath
import re
from urllib.parse import unquote

# How close a heading or flag must be to count as the one that was meant.
# 0.8 takes `#instalation` to `#installation` and `--dry_run` to `--dry-run`,
# and leaves `#quick-start` against `#getting-started` alone.
CLOSE_MATCH = 0.8

# A link destination is written after one of these, so a target is replaced
# there and not where the same text appears as link text or prose.
_DESTINATION_PREFIXES = ('](<', '](', ']: <', ']: ', 'src="', "src='", 'href="', "href='")


def no_fix() -> dict:
    return {'kind': 'none'}


_LINE = re.compile(r'[^\n]*\n|[^\n]+\Z')


def _read_lines(repo_root: str, path: str) -> list[str] | None:
    """The file's lines with their endings, exactly as on disk, so a diff applies."""
    try:
        with open(os.path.join(repo_root, path), encoding='utf-8', newline='') as fh:
            return _LINE.findall(fh.read())
    except (OSError, UnicodeDecodeError):
        return None


def _rewrite(line: str, change) -> str | None:
    """Apply `change` to a line's text and keep its ending."""
    body = line.rstrip('\r\n')
    rewritten = change(body)
    return None if rewritten is None else rewritten + line[len(body):]


def edit_fix(repo_root: str, path: str, edits: dict[int, tuple[str, str]]) -> dict:
    """A fix that rewrites text on some lines of one file, as a unified diff.

    `edits` maps a 1-based line number to (old, new). Each `old` must be found
    on its line as a link destination, or the whole fix is withdrawn: a diff
    that does not apply cleanly is worse than none.
    """
    lines = _read_lines(repo_root, path)
    if lines is None:
        return no_fix()
    after = list(lines)
    for line_no, (old, new) in sorted(edits.items()):
        if not 1 <= line_no <= len(after):
            return no_fix()
        rewritten = _rewrite(after[line_no - 1],
                             lambda body, old=old, new=new: _replace_destination(body, old, new))
        if rewritten is None:
            return no_fix()
        after[line_no - 1] = rewritten
    return _as_diff(path, lines, after)


def flag_fix(repo_root: str, path: str, line_no: int, old: str, new: str) -> dict:
    """A fix that renames one `--flag` on one line of a document."""
    lines = _read_lines(repo_root, path)
    if lines is None or not 1 <= line_no <= len(lines):
        return no_fix()
    pattern = re.compile(r'(?<![\w-])--' + re.escape(old) + r'(?![\w-])')
    rewritten = _rewrite(lines[line_no - 1],
                         lambda body: pattern.sub('--' + new, body) if pattern.search(body) else None)
    if rewritten is None:
        return no_fix()
    after = list(lines)
    after[line_no - 1] = rewritten
    return _as_diff(path, lines, after)


def _replace_destination(line: str, old: str, new: str) -> str | None:
    """`line` with every link destination `old` rewritten to `new`, or None if there is none."""
    changed = line
    for prefix in _DESTINATION_PREFIXES:
        changed = changed.replace(prefix + old, prefix + new)
    return changed if changed != line else None


def _as_diff(path: str, before: list[str], after: list[str]) -> dict:
    if before == after:
        return no_fix()
    out = []
    for piece in difflib.unified_diff(before, after, f'a/{path}', f'b/{path}', n=1):
        out.append(piece)
        if not piece.endswith('\n'):  # the file's last line has no newline
            out.append('\n\\ No newline at end of file\n')
    return {'kind': 'edit', 'files': [path], 'diff': ''.join(out)}


_HUNK = re.compile(r'^@@ -(\d+)(?:,\d+)? \+\d+(?:,\d+)? @@')


def _changed_lines(diff: str) -> list[tuple[int, str, str]]:
    """(line number, old text, new text) for each line an `edit` diff rewrites."""
    changes: list[tuple[int, str, str]] = []
    removed: list[tuple[int, str]] = []
    line_no = 0
    for row in diff.split('\n'):
        hunk = _HUNK.match(row)
        if hunk:
            line_no = int(hunk.group(1))
            continue
        if row.startswith(('---', '+++', '\\')):
            continue
        if row.startswith('-'):
            removed.append((line_no, row[1:]))
            line_no += 1
        elif row.startswith('+'):
            number, before = removed.pop(0)
            changes.append((number, before, row[1:]))
        elif row.startswith(' '):
            line_no += 1
    return changes


def _span(before: str, after: str) -> tuple[int, int, str]:
    """The one stretch of `before` that `after` replaces: (start, end, replacement)."""
    start = 0
    while start < min(len(before), len(after)) and before[start] == after[start]:
        start += 1
    end_before, end_after = len(before), len(after)
    while end_before > start and end_after > start and before[end_before - 1] == after[end_after - 1]:
        end_before -= 1
        end_after -= 1
    return start, end_before, after[start:end_after]


def combine(repo_root: str, edits: list[dict]) -> dict:
    """One `edit` fix that makes every edit in `edits` at once, one diff per file.

    Each fix was computed against the file on disk, so two of them can rewrite
    the same line. Their changed stretches are applied right to left, so
    neither moves the other. If two stretches overlap, the edits are not
    independent and nothing is combined.
    """
    by_file: dict[str, dict[int, list[tuple[int, int, str]]]] = {}
    for fix in edits:
        if fix.get('kind') != 'edit' or len(fix.get('files') or []) != 1:
            return no_fix()
        path = fix['files'][0]
        for number, before, after in _changed_lines(fix['diff']):
            by_file.setdefault(path, {}).setdefault(number, []).append(_span(before, after))
    diffs, files = [], []
    for path in sorted(by_file):
        lines = _read_lines(repo_root, path)
        if lines is None:
            return no_fix()
        after = list(lines)
        for number, spans in by_file[path].items():
            if not 1 <= number <= len(after):
                return no_fix()
            spans = sorted(set(spans), key=lambda span: span[0], reverse=True)
            for (start, end, _), (next_start, _, _) in zip(spans[1:], spans):
                if end > next_start:
                    return no_fix()  # overlapping: not independent edits
            text = after[number - 1]
            body = text.rstrip('\r\n')
            for start, end, replacement in spans:
                body = body[:start] + replacement + body[end:]
            after[number - 1] = body + text[len(text.rstrip('\r\n')):]
        combined = _as_diff(path, lines, after)
        if combined['kind'] != 'edit':
            return no_fix()
        diffs.append(combined['diff'])
        files.append(path)
    return {'kind': 'edit', 'files': files, 'diff': ''.join(diffs)} if diffs else no_fix()


def one_close_match(wanted: str, candidates: list[str] | set[str]) -> str | None:
    """The single candidate close to `wanted`, or None when there are none or several."""
    close = difflib.get_close_matches(wanted, sorted(set(candidates)), n=2,
                                      cutoff=CLOSE_MATCH)
    return close[0] if len(close) == 1 else None


def moved_target(src: str, raw: str, all_paths: list[str]) -> str | None:
    """Where a broken link should point, when exactly one file has its basename.

    Keeps the anchor and the link's style: a link written from the repository
    root stays rooted, a relative one stays relative.
    """
    path_part, _, anchor = raw.partition('#')
    path_part = unquote(path_part).rstrip('/')
    name = posixpath.basename(path_part)
    if not name:
        return None
    matches = [p for p in all_paths if posixpath.basename(p) == name and p != src]
    if len(matches) != 1:
        return None
    target = matches[0]
    if path_part.startswith('/'):
        written = '/' + target
    else:
        written = posixpath.relpath(target, posixpath.dirname(src) or '.')
    return written + (f'#{anchor}' if anchor else '')


def blast_radius(finding: dict, inbound: dict[str, list[str]]) -> list[str]:
    """Every other file that cites a file this finding is about."""
    files = {item['file'] for item in finding.get('evidence', [])}
    cited_by: set[str] = set()
    for path in files:
        cited_by.update(inbound.get(path, ()))
    return sorted(cited_by - files)


def batch_eligible(finding: dict) -> bool:
    """Whether a finding may be fixed together with the rest of its class.

    SKILL.md's rule, as a field: an exact finding with exactly one mechanical
    fix that deletes nothing. Never a reviewer's finding, and never one whose
    authoritative side is uncertain.
    """
    fix = finding.get('fix') or {}
    return (fix.get('kind') == 'edit'
            and (finding['source'] == 'graph' or finding['source'].startswith('check:'))
            and finding.get('ssot_direction') != 'uncertain')
