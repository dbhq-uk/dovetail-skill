#!/usr/bin/env python3
"""
Dynamic references: files that code loads by constructed path.

A rubric loaded as `os.path.join(REFERENCE_DIR, 'reviewers', f'{name}.md')` is
referenced. The reference graph cannot see it, because there is no literal path
anywhere - the filename is computed at runtime. Left alone, every such file
reads as an orphan.

The wrong fix is to make the user add cross-links to their documents so the
checker stops complaining. That inverts the relationship: documents exist to be
read, not to satisfy a tool, and a link added only to silence a finding is
noise a human reader has to step over forever.

The right fix is for the tool to be less wrong. The literal fragments of a
constructed path are statically readable - a directory name and an extension -
and that is enough to say "files in this directory with this extension are
loaded by this module". So the reference is *inferred*, and nothing in the
repository has to change to accommodate the checker.

This is used only to suppress orphan findings. It never asserts a link and can
never make a broken link, for the same reason a bare basename cannot: the
evidence is real but imprecise, and the two bars are different.
"""

from __future__ import annotations

import ast
import os
import posixpath
import re

# A literal that names a file extension, e.g. '.md' or '.py'. Bounded at
# five characters so a dot-directory like '.dovetail' is not read as one.
_EXT = re.compile(r'^\.[A-Za-z0-9]{1,5}$')
# A literal ending in an extension, e.g. 'finding-schema.md' or '*.py'.
_ENDS_WITH_EXT = re.compile(r'\.[A-Za-z0-9]{1,8}$')
# A literal that could be a single directory segment.
_DIR_SEGMENT = re.compile(r'^[A-Za-z0-9_.-]+$')

# Shell and workflow files: a glob is written literally, so no AST is needed.
_SHELL_GLOB = re.compile(r'([\w./-]*/)?(\*[\w.-]*|[\w-]+\*[\w.-]*)')


class Rule:
    """Files in `directory` ending with `suffix` are loaded by `source`."""

    __slots__ = ('directory', 'suffix', 'source')

    def __init__(self, directory: str, suffix: str, source: str):
        self.directory = directory
        self.suffix = suffix
        self.source = source

    def matches(self, path: str) -> bool:
        if self.suffix and not path.endswith(self.suffix):
            return False
        if not self.directory:
            return False
        # The directory must appear as a whole path segment, and the file must
        # sit directly inside it - a rule inferred from 'reviewers' should not
        # claim every markdown file in the repository.
        parent = posixpath.dirname(path)
        return parent == self.directory or parent.endswith('/' + self.directory)


def _string_parts(node: ast.AST) -> list[str]:
    """Every literal string fragment reachable in an expression.

    Covers the shapes that actually occur: os.path.join with literal segments,
    f-strings with literal tails, `/` on pathlib Paths, and `+` concatenation.
    A computed segment simply contributes nothing, which is correct - it is the
    literal parts that carry the information.
    """
    parts: list[str] = []
    for child in ast.walk(node):
        if isinstance(child, ast.Constant) and isinstance(child.value, str):
            parts.append(child.value)
    return parts


def _rules_from_python(path: str, source: str) -> list[Rule]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    rules: list[Rule] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, 'attr', None) or getattr(node.func, 'id', None)

        # glob('*.py') / Path(...).glob('*.md') / iterdir + endswith
        if name in ('glob', 'iglob', 'rglob'):
            for arg in node.args:
                for literal in _string_parts(arg):
                    if '*' in literal:
                        directory = posixpath.dirname(literal)
                        suffix = posixpath.basename(literal).lstrip('*')
                        rules.append(Rule(directory, suffix, path))
            continue

        # join('reviewers', f'{name}.md') and friends
        if name not in ('join', 'open', 'read_text', 'normpath'):
            continue
        literals = [p for arg in node.args for p in _string_parts(arg)]
        if not literals:
            continue
        suffix = ''
        directory = ''
        for literal in literals:
            if _EXT.match(literal):
                suffix = literal
            elif _ENDS_WITH_EXT.search(literal) and '/' not in literal:
                # A fully literal filename is an ordinary path reference the
                # graph already sees; only a *pattern* is interesting here.
                if '*' in literal or '{' in literal:
                    suffix = literal[literal.rfind('.'):]
            elif _DIR_SEGMENT.match(literal) and '.' not in literal:
                directory = literal
        if directory and suffix:
            rules.append(Rule(directory, suffix, path))

    # An f-string tail like f'{name}.md' next to a literal directory is the
    # commonest shape and is caught above, because both are constants inside
    # the same call.
    return rules


def _rules_from_shell(path: str, source: str) -> list[Rule]:
    rules: list[Rule] = []
    for line in source.split('\n'):
        if line.lstrip().startswith('#'):
            continue
        for match in _SHELL_GLOB.finditer(line):
            literal = match.group(0)
            if '*' not in literal:
                continue
            directory = posixpath.dirname(literal).strip('"\'').rstrip('/')
            suffix = posixpath.basename(literal).lstrip('*')
            if not _ENDS_WITH_EXT.search(suffix):
                continue
            if directory:
                rules.append(Rule(posixpath.basename(directory), suffix, path))
    return rules


def collect_rules(inventory: dict) -> list[Rule]:
    """Every dynamic-reference rule the repository's own code implies."""
    root = inventory['repo_root']
    rules: list[Rule] = []
    for entry in inventory['files']:
        path = entry['path']
        if entry['modality'] != 'text':
            continue
        if not path.endswith(('.py', '.sh', '.bash', '.yml', '.yaml')):
            continue
        try:
            with open(os.path.join(root, path), encoding='utf-8',
                      errors='replace') as fh:
                source = fh.read()
        except OSError:
            continue
        if path.endswith('.py'):
            rules.extend(_rules_from_python(path, source))
        else:
            rules.extend(_rules_from_shell(path, source))
    return rules


def dynamically_referenced(inventory: dict) -> dict[str, str]:
    """Repo paths loaded by constructed path, mapped to the module doing it."""
    rules = collect_rules(inventory)
    if not rules:
        return {}
    found: dict[str, str] = {}
    for entry in inventory['files']:
        path = entry['path']
        for rule in rules:
            if rule.source == path:
                continue  # a module globbing itself proves nothing
            if rule.matches(path):
                found[path] = rule.source
                break
    return found
