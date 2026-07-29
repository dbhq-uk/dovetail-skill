"""No em or en dashes anywhere in this repository's own markdown.

AGENTS.md states the rule; nothing enforced it, and a run header in SKILL.md
carried an en dash as a bullet glyph for weeks. A stated rule with no mechanism
is a rule that decays.

This lives in .dovetail/checks/ rather than in convcheck.py on purpose: plenty
of repositories use em dashes deliberately, and shipping this in the tool would
make it everyone else's noise. It is a house rule, so it belongs with the house.
"""

import os

DASHES = {'—': 'em dash', '–': 'en dash'}


def check(inventory, graph):
    root = inventory['repo_root']
    findings = []
    for entry in inventory['files']:
        path = entry['path']
        if not path.endswith(('.md', '.markdown')):
            continue
        try:
            with open(os.path.join(root, path), encoding='utf-8') as fh:
                lines = fh.read().split('\n')
        except (OSError, UnicodeDecodeError):
            continue
        for number, line in enumerate(lines, start=1):
            for char, name in DASHES.items():
                if char not in line:
                    continue
                findings.append({
                    'id': f'local:dash:{path}:{number}:{name}',
                    'source': 'plugin:house_style_dashes',
                    'category': 'convention',
                    'problem': (f'{path}:{number} uses an {name}; the house '
                                'style is a plain hyphen.'),
                    'evidence': [{'file': path, 'line': number,
                                  'quote': line.strip()[:200]}],
                    'suggestion': f'Replace the {name} with `-`.',
                    'severity': 'low',
                })
                break
    return findings
