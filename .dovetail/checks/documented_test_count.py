"""Every documented test count must match the number of tests that exist.

A repo-specific rule, and one this repository proved it needed: four documents
claimed 243, 359, 243 and 243 tests when the suite had 394. Nobody noticed,
because a bare number in prose is not checkable by any general rule - which is
precisely what the plugin point is for.

Counting is deterministic (a regex over test method definitions, the same thing
pytest collects), so this is exact and free rather than something a reviewer
might spot on a good day.
"""

import ast
import os
import re

# "# 394 tests", "all 394 tests pass", "394 tests, no network required"
_CLAIM = re.compile(r'(?<![\d,])(\d{2,5})\s+tests\b')
# Below this, a number followed by "tests" is likely prose about something else
# ("3 tests fail intermittently"), not a claim about the suite's size.
MIN_CLAIM = 20


def _count_in_module(source):
    """Tests pytest would collect from one module, inheritance included.

    Counting `def test_` misses the case that actually bit here: a subclass of
    another test class re-collects every inherited test. Ignoring that
    undercounted the suite by 17 and made the check report drift that was its
    own arithmetic.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0

    own = {}
    bases = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        own[node.name] = {n.name for n in node.body
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                          and n.name.startswith('test_')}
        bases[node.name] = [b.id for b in node.bases if isinstance(b, ast.Name)]

    def collected(name, seen=None):
        seen = seen or set()
        if name in seen or name not in own:
            return set()
        seen.add(name)
        tests = set(own[name])
        for base in bases.get(name, []):
            tests |= collected(base, seen)
        return tests

    total = sum(len(collected(name)) for name in own)
    # Module-level test functions, outside any class.
    total += sum(1 for node in tree.body
                 if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                 and node.name.startswith('test_'))
    return total


def _count_tests(root, paths):
    total = 0
    for path in paths:
        if '/tests/' not in f'/{path}' or not path.endswith('.py'):
            continue
        try:
            with open(os.path.join(root, path), encoding='utf-8') as fh:
                total += _count_in_module(fh.read())
        except OSError:
            continue
    return total


def check(inventory, graph):
    root = inventory['repo_root']
    paths = inventory['all_paths']
    actual = _count_tests(root, paths)
    if not actual:
        return []

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
            for match in _CLAIM.finditer(line):
                claimed = int(match.group(1))
                if claimed < MIN_CLAIM or claimed == actual:
                    continue
                findings.append({
                    'id': f'local:testcount:{path}:{number}',
                    'source': 'plugin:documented_test_count',
                    'category': 'convention',
                    'problem': (f'{path}:{number} claims {claimed} tests; the '
                                f'suite has {actual}.'),
                    'evidence': [{'file': path, 'line': number,
                                  'quote': line.strip()[:200]}],
                    'suggestion': f'Update the count to {actual}.',
                    'severity': 'low',
                })
    return findings
