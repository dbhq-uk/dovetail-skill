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
import subprocess

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

# The roster, with its default tiering. Extracting the claims to compare is
# mechanical, so claimscan.py does it in Python for nothing; adjudication is
# low-volume and high-judgement, so the expensive model sees clusters rather
# than a corpus.
ROSTER: dict[str, dict] = {
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


class StaleEvidenceError(ValidationError):
    """The cited line was edited after the reviewer read it.

    A subclass so existing handlers still drop the finding, but the wording
    never accuses the reviewer of fabricating anything - because it did not.
    """


# Quote verdicts.
MATCH = 'match'          # quote is at the cited line
MOVED = 'moved'          # quote is in the file, at a different line
STALE = 'stale'          # quote was in the committed file, not in the working one
ABSENT = 'absent'        # quote is in neither - fabricated
MISSING = 'missing'      # the cited file is gone, and HEAD cannot vouch for it
UNREADABLE = 'unreadable'  # the cited file exists but is not readable text
OUTSIDE = 'outside'      # the cited path resolves outside the repository
EMPTY = 'empty'          # there is no quote to check

# A reviewer quoting a sentence of hard-wrapped prose sometimes quotes past the
# end of the cited line. The rest is checked against the lines that follow, up
# to this many and never across a blank line.
WRAP_WINDOW = 5


def _norm(text: str) -> str:
    return re.sub(r'\s+', ' ', str(text)).strip()


def _trimmed_from(actual: str, quote: str) -> bool:
    """Whether `quote` is the line `actual` plus nothing but punctuation.

    The reverse match. Reviewers sometimes carry a bullet, a closing full stop
    or a pair of quote marks the line does not have, and that is what this
    forgives. It used to be plain `actual in quote`, which forgave far more: a
    blank line is inside every quote, and a line reading `---` is inside any
    quote holding three hyphens, so a short line vouched for a long invented
    sentence. So the line must cover the whole quote except for characters
    that are neither letters nor digits - a reviewer can add a full stop, but
    never a word.
    """
    start = quote.find(actual)
    if start == -1:
        return False
    extra = quote[:start] + quote[start + len(actual):]
    return not any(ch.isalnum() for ch in extra)


def _found_at(lines: list[str], index: int, quote: str) -> bool:
    """Whether `quote` is really at line `index` (0-indexed) of `lines`.

    Three ways to match, each of which proves the text is in the file:

    - the quote is a fragment of the line, which is the common case
    - the line is the whole quote bar some punctuation at the ends
    - the quote starts on the line and runs on into the lines that follow
    """
    if not 0 <= index < len(lines):
        return False
    actual = _norm(lines[index])
    if not actual:
        return False
    if quote in actual or _trimmed_from(actual, quote):
        return True
    joined = actual
    for following in lines[index + 1:index + 1 + WRAP_WINDOW]:
        following = _norm(following)
        if not following:
            break
        joined = f'{joined} {following}'
        # The quote has to begin on the cited line. One that sits wholly on a
        # later line is a moved quote, and is reported as one.
        start = joined.find(quote)
        if start != -1 and start < len(actual):
            return True
    return False


def _quote_line(lines: list[str], quote: str) -> int | None:
    """1-indexed line holding `quote`, or None."""
    for index in range(len(lines)):
        if _found_at(lines, index, quote):
            return index + 1
    return None


def _inside(repo_root: str, rel: str) -> bool:
    """Whether `rel` resolves to a path inside the repository.

    Resolved through symlinks, so a link inside the repository that points
    out of it does not smuggle an outside file in as evidence.
    """
    try:
        root = os.path.realpath(repo_root)
        target = os.path.realpath(os.path.join(root, rel))
        return os.path.commonpath([root, target]) == root
    except ValueError:  # a NUL byte, or a different drive on Windows
        return False


def _committed_lines(repo_root: str, path: str) -> list[str] | None:
    """The file as of HEAD, or None if git cannot say.

    `HEAD:./<path>` rather than `HEAD:<path>`: the second is read from the
    top of the repository, so it misses every file when `repo_root` is a
    subdirectory of it.
    """
    try:
        result = subprocess.run(
            ['git', '-C', repo_root, 'show', f'HEAD:./{path}'],
            capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.split('\n')


def quote_verdict(repo_root: str, item: dict) -> tuple[str, int | None]:
    """Classify a piece of evidence against the file it cites.

    This is the anti-fabrication check. A model that invents a plausible quote
    at a plausible line is the single most damaging failure available here,
    because the finding reads exactly like a true one.

    But "the quote is not at that line" has three causes, and only one of them
    is the model's fault:

    - an edit *above* the line shifted it, and the quote is still in the file
    - an edit *to* the line rewrote it, so the quote is gone from the working
      file but still present in the committed one
    - the model made it up

    Treating all three as fabrication is wrong and was observed to be
    expensive: during a triage run the orchestrator applied a fix the user had
    approved, and the very next reviewer to return had its whole output
    rejected because one finding quoted the line that fix had rewritten. Ten
    sound findings were nearly discarded because dovetail edited the file
    itself. Reading the committed blob separates the cases exactly, with no
    heuristic about timestamps.

    Everything else that cannot be proven is a failure, never a pass: an empty
    quote proves nothing, a path outside the repository is not evidence about
    it, and a file that does not exist or cannot be read cannot vouch for a
    quote. Where git cannot answer, the strict reading stands: unproven is
    fabricated.
    """
    raw_quote = item.get('quote')
    quote = _norm(raw_quote) if isinstance(raw_quote, str) else ''
    if not quote:
        return EMPTY, None

    if not _inside(repo_root, item['file']):
        return OUTSIDE, None

    path = os.path.join(repo_root, item['file'])
    try:
        with open(path, encoding='utf-8') as fh:
            lines = fh.read().split('\n')
    except FileNotFoundError:
        # Deleted by an approved fix mid-run is the one innocent way a cited
        # file can vanish, and the committed blob tells it apart exactly.
        committed = _committed_lines(repo_root, item['file'])
        if committed is not None and _quote_line(committed, quote) is not None:
            return STALE, None
        return MISSING, None
    except (OSError, UnicodeDecodeError):
        return UNREADABLE, None

    if _found_at(lines, item['line'] - 1, quote):
        return MATCH, None

    moved_to = _quote_line(lines, quote)
    if moved_to is not None:
        return MOVED, moved_to

    committed = _committed_lines(repo_root, item['file'])
    if committed is not None and _quote_line(committed, quote) is not None:
        return STALE, None

    return ABSENT, None


# Evidence that cannot be checked is rejected as fabricated rather than let
# through. Each of these once returned a pass, which meant a reviewer could get
# an invented finding past the check by citing something it could not read.
_UNPROVEN = {
    EMPTY: 'has no quote, so nothing could be checked',
    OUTSIDE: 'is outside the repository',
    MISSING: 'does not exist',
    UNREADABLE: 'cannot be read as text, so the quote could not be checked',
}


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
            if not isinstance(item['file'], str) or not item['file']:
                raise ValidationError(f'{where}: evidence file must be a path')
            verdict, moved_to = quote_verdict(repo_root, item)
            if verdict == MOVED:
                # The quote is real and the line number drifted under an edit
                # elsewhere in the file. Correct it and keep the finding: the
                # evidence is sound, only the coordinate was stale.
                item['line'] = moved_to
            elif verdict == STALE:
                raise StaleEvidenceError(
                    f'{where}: {item["file"]}:{item["line"]} was edited after '
                    'the reviewer read it - the quote is in the committed file '
                    'but not the working one. Dropped as unverifiable, not '
                    'fabricated; re-run the reviewer to re-check it')
            elif verdict == ABSENT:
                raise ValidationError(
                    f'{where}: quote does not appear at {item["file"]}:'
                    f'{item["line"]} - fabricated evidence')
            elif verdict in _UNPROVEN:
                raise ValidationError(
                    f'{where}: {item["file"]}:{item["line"]} '
                    f'{_UNPROVEN[verdict]} - fabricated evidence')

        if category == 'contradiction' and len(evidence) < 2:
            raise ValidationError(
                f'{where}: a contradiction needs evidence from both sides')

        finding['source'] = f'reviewer:{reviewer}'
        finding.setdefault('fix', {'kind': 'none'})
        finding.setdefault('blast_radius', [])
        finding['batch_eligible'] = False  # a reviewer's finding is never batched
        # The reviewer does not invent the id: it is derived here, from the
        # same fingerprint function the deterministic layer uses, so a decision
        # recorded against a finding suppresses it whichever layer found it.
        finding['id'] = fingerprint(
            category,
            [item['file'] for item in evidence],
            finding.get('claim') or finding['problem'],
        )
        return finding
