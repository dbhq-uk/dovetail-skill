"""The model table in SKILL.md must match the roster in reviewer.py.

A repo-specific rule, which is why it lives here rather than in convcheck.py:
nobody else has a SKILL.md documenting a reviewer roster. Written as a plugin
it is exact and free. Left in a prompt it would be something a model might
notice on a good day.

The drift this catches is the ordinary kind - someone retunes a reviewer's tier
in the code and the documented table quietly becomes a lie. It is the same
class of defect dovetail exists to find, so it would be embarrassing to ship it.
"""

import ast
import os
import re

SKILL = 'skills/dovetail/SKILL.md'
ROSTER_SOURCE = 'skills/dovetail/scripts/reviewer.py'

# | `xref` | haiku | low |
_ROW = re.compile(r'^\|\s*`?([a-z-]+)`?\s*\|\s*(haiku|sonnet|opus)\s*\|\s*(low|medium|high)\s*\|',
                  re.M)


def _documented(text):
    return {name: (model, effort) for name, model, effort in _ROW.findall(text)}


def _declared(source):
    """Read ROSTER out of reviewer.py without importing it."""
    tree = ast.parse(source)
    for node in tree.body:
        if not isinstance(node, ast.AnnAssign) and not isinstance(node, ast.Assign):
            continue
        targets = [node.target] if isinstance(node, ast.AnnAssign) else node.targets
        names = [t.id for t in targets if isinstance(t, ast.Name)]
        if 'ROSTER' not in names or node.value is None:
            continue
        roster = ast.literal_eval(node.value)
        return {name: (entry['model'], entry['effort'])
                for name, entry in roster.items()}
    return {}


def check(inventory, graph):
    root = inventory['repo_root']
    paths = set(inventory['all_paths'])
    if SKILL not in paths or ROSTER_SOURCE not in paths:
        return []

    try:
        with open(os.path.join(root, SKILL), encoding='utf-8') as fh:
            skill_text = fh.read()
        with open(os.path.join(root, ROSTER_SOURCE), encoding='utf-8') as fh:
            declared = _declared(fh.read())
    except (OSError, SyntaxError, ValueError):
        return []
    if not declared:
        return []

    documented = _documented(skill_text)
    findings = []
    for name, (model, effort) in sorted(declared.items()):
        # claim-extract feeds another reviewer and is not dispatched for
        # findings, so SKILL.md's dispatch table correctly omits it.
        if name == 'claim-extract':
            continue
        if name not in documented:
            findings.append({
                'id': f'local:roster:{name}:missing',
                'source': 'plugin:roster_matches_skill',
                'category': 'convention',
                'problem': f'reviewer.py declares `{name}` but SKILL.md\'s '
                           'dispatch table does not list it.',
                'evidence': [
                    {'file': ROSTER_SOURCE, 'line': 1, 'quote': f'{name}: {model}/{effort}'},
                    {'file': SKILL, 'line': 1, 'quote': 'dispatch table'},
                ],
                'suggestion': f'Add `{name}` ({model}, {effort}) to the table in {SKILL}.',
                'severity': 'medium',
            })
            continue
        if documented[name] != (model, effort):
            got_model, got_effort = documented[name]
            findings.append({
                'id': f'local:roster:{name}:drift',
                'source': 'plugin:roster_matches_skill',
                'category': 'convention',
                'problem': (f'SKILL.md documents `{name}` as {got_model}/{got_effort}, '
                            f'but reviewer.py declares {model}/{effort}.'),
                'evidence': [
                    {'file': SKILL, 'line': 1, 'quote': f'{name}: {got_model}/{got_effort}'},
                    {'file': ROSTER_SOURCE, 'line': 1, 'quote': f'{name}: {model}/{effort}'},
                ],
                'suggestion': 'Align the documented tier with the declared one.',
                'severity': 'medium',
            })
    return findings
