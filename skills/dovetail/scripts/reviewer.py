#!/usr/bin/env python3
"""
The judgement layer's shared parts: the roster, tiering, and schema validation.

Both dispatch paths use this module - the in-session subagents driven by
SKILL.md, and the headless `ci_dispatch.py` shim for the scheduled job. A second
dispatch path is a real maintenance risk, and the mitigation is to give it as
little of its own surface as possible: same roster, same rubrics, same
validator. Only the transport differs.

Validation is not a formality. A reviewer's output arrives from a model, and a
malformed finding downstream either crashes rendering or - far worse - gets
reported to a user as a real defect in their repository. Anything that does not
satisfy the contract is a reviewer failure, named in the run header, never a
silent partial result.
"""

from __future__ import annotations

import json
import os
import re

from store import fingerprint

# Categories the deterministic layer owns. A reviewer claiming one of these is
# duplicating work Python already did exactly, and its version is a guess.
DETERMINISTIC_CATEGORIES = frozenset({
    'broken_link', 'dangling_anchor', 'orphan', 'duplicate', 'near_duplicate',
    'flag_drift', 'signature_drift', 'version_drift', 'parse_error',
    'missing_path', 'decoupled', 'stale_todo',
})

REVIEWER_CATEGORIES = frozenset({
    'contradiction', 'missing_xref', 'staleness', 'convention', 'dead_code',
    'spec_drift', 'other',
})

SEVERITIES = frozenset({'high', 'medium', 'low'})
CONFIDENCES = frozenset({'high', 'medium', 'low'})
SSOT = frozenset({'a', 'b', 'uncertain', 'n/a'})

# The roster, with its default tiering. Extraction is high-volume and near
# mechanical, so it runs cheap and parallel; adjudication is low-volume and
# high-judgement, so the expensive model sees clusters rather than a corpus.
ROSTER: dict[str, dict] = {
    'claim-extract': {'model': 'haiku',  'effort': 'low',
                      'produces': 'claims'},
    'xref':          {'model': 'haiku',  'effort': 'low'},
    'convention':    {'model': 'sonnet', 'effort': 'medium'},
    'code-hygiene':  {'model': 'sonnet', 'effort': 'medium'},
    'contradiction': {'model': 'opus',   'effort': 'high'},
    'staleness':     {'model': 'opus',   'effort': 'high'},
    'spec-flow':     {'model': 'opus',   'effort': 'high'},
}

_TIER_ORDER = ['haiku', 'sonnet', 'opus']
_EFFORT_ORDER = ['low', 'medium', 'high']


def _shift(value: str, order: list[str], by: int) -> str:
    index = max(0, min(len(order) - 1, order.index(value) + by))
    return order[index]


def resolve_roster(profile: str = 'default',
                   overrides: dict | None = None) -> dict[str, dict]:
    """The roster for a run, after the profile and any per-reviewer overrides.

    `cheap` drops every reviewer one tier and disables escalation; `thorough`
    puts everything on the strongest model. Config overrides win over the
    profile, because they are the durable setting and the profile is usually
    spoken for a single run.
    """
    resolved: dict[str, dict] = {}
    for name, base in ROSTER.items():
        entry = dict(base)
        entry['enabled'] = True
        if profile == 'cheap':
            entry['model'] = _shift(entry['model'], _TIER_ORDER, -1)
            entry['effort'] = _shift(entry['effort'], _EFFORT_ORDER, -1)
        elif profile == 'thorough':
            entry['model'] = 'opus'
            entry['effort'] = 'high'
        resolved[name] = entry

    for name, settings in (overrides or {}).items():
        if name not in resolved:
            continue
        for key in ('model', 'effort', 'enabled'):
            if key in settings:
                resolved[name][key] = settings[key]
    return resolved


def escalation_enabled(profile: str) -> bool:
    """Whether low-confidence findings get re-run on a stronger model.

    Off under `cheap`: escalation exists to pay Opus rates only where a cheaper
    model was uncertain, and a profile chosen to save money should not
    reintroduce that spend through the back door.
    """
    return profile != 'cheap'


def needs_escalation(finding: dict, reviewer_model: str) -> bool:
    return finding.get('confidence') == 'low' and reviewer_model != 'opus'


class ValidationError(ValueError):
    """A reviewer returned something that is not a valid finding set."""


def _line_matches(repo_root: str, item: dict) -> bool:
    """Whether the quoted text really appears at the cited line.

    This is the anti-fabrication check. A model that invents a plausible quote
    at a plausible line is the single most damaging failure available here,
    because the finding reads exactly like a true one. Comparing against the
    file is cheap and settles it.
    """
    path = os.path.join(repo_root, item['file'])
    try:
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().split('\n')
    except (OSError, UnicodeDecodeError):
        return True  # unreadable here is not proof of fabrication
    index = item['line'] - 1
    if index < 0 or index >= len(lines):
        return False
    quote = re.sub(r'\s+', ' ', str(item.get('quote', ''))).strip()
    if not quote:
        return True
    actual = re.sub(r'\s+', ' ', lines[index]).strip()
    # Substring either way: reviewers routinely quote a fragment of a long line,
    # and occasionally normalise punctuation in a way that trims the ends.
    return quote in actual or actual in quote


def validate_findings(raw: object, reviewer: str, repo_root: str,
                      rejected: list[str] | None = None) -> list[dict]:
    """Return schema-valid findings, or raise ValidationError.

    Two different failures, handled differently - a distinction learned by
    running this against a live model.

    **Transport failure** (not JSON, not an array) raises. Nothing can be
    salvaged and the caller retries.

    **A single invalid finding** is dropped and appended to `rejected`, and the
    rest are kept. The original design discarded the whole batch on the theory
    that a reviewer producing one bad finding cannot be trusted at all. On the
    first live run that cost every `spec-flow` finding because one of them
    quoted a line that did not exist - and the others were fine. A fabricated
    quote is a property of that finding, not proof the others are wrong, and
    silently losing good findings is a worse failure than reporting them with
    a note about what was dropped.

    When `rejected` is None the strict behaviour is kept, so existing callers
    and the contract tests are unaffected.
    """
    strict = rejected is None
    if isinstance(raw, str):
        text = raw.strip()
        fence = re.match(r'^```(?:json)?\s*\n(.*?)\n```$', text, re.S)
        if fence:
            text = fence.group(1)
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValidationError(f'{reviewer}: output is not JSON: {exc}') from exc

    if not isinstance(raw, list):
        raise ValidationError(
            f'{reviewer}: expected a JSON array, got {type(raw).__name__}')

    out: list[dict] = []
    for index, finding in enumerate(raw):
        where = f'{reviewer}[{index}]'
        try:
            out.append(_validate_one(finding, where, reviewer, repo_root))
        except ValidationError as exc:
            if strict:
                raise
            rejected.append(str(exc))
    return out


def _validate_one(finding: object, where: str, reviewer: str,
                  repo_root: str) -> dict:
    """Validate a single finding, raising ValidationError if it is not sound."""
    if True:
        if not isinstance(finding, dict):
            raise ValidationError(f'{where}: not an object')

        for key in ('category', 'problem', 'evidence', 'suggestion', 'severity'):
            if key not in finding:
                raise ValidationError(f'{where}: missing {key}')

        category = finding['category']
        if category in DETERMINISTIC_CATEGORIES:
            raise ValidationError(
                f'{where}: category {category!r} belongs to the deterministic '
                'layer and must not be reported by a reviewer')
        if category not in REVIEWER_CATEGORIES:
            raise ValidationError(f'{where}: unknown category {category!r}')

        if finding['severity'] not in SEVERITIES:
            raise ValidationError(f'{where}: bad severity {finding["severity"]!r}')
        confidence = finding.setdefault('confidence', 'medium')
        if confidence not in CONFIDENCES:
            raise ValidationError(f'{where}: bad confidence {confidence!r}')
        ssot = finding.setdefault('ssot_direction', 'n/a')
        if ssot not in SSOT:
            raise ValidationError(f'{where}: bad ssot_direction {ssot!r}')

        evidence = finding['evidence']
        if not isinstance(evidence, list) or not evidence:
            raise ValidationError(f'{where}: evidence must be a non-empty list')
        for item in evidence:
            if not isinstance(item, dict) or 'file' not in item or 'line' not in item:
                raise ValidationError(f'{where}: evidence item needs file and line')
            if not isinstance(item['line'], int):
                raise ValidationError(f'{where}: evidence line must be an integer')
            if not _line_matches(repo_root, item):
                raise ValidationError(
                    f'{where}: quote does not appear at {item["file"]}:'
                    f'{item["line"]} - fabricated evidence')

        if category == 'contradiction' and len(evidence) < 2:
            raise ValidationError(
                f'{where}: a contradiction needs evidence from both sides')

        finding['source'] = f'reviewer:{reviewer}'
        finding.setdefault('fix', {'kind': 'none'})
        finding.setdefault('blast_radius', [])
        # The reviewer does not invent the id: it is derived here, from the
        # same fingerprint function the deterministic layer uses, so a decision
        # recorded against a finding suppresses it whichever layer found it.
        finding['id'] = fingerprint(
            category,
            [item['file'] for item in evidence],
            finding.get('claim') or finding['problem'],
        )
        return finding
