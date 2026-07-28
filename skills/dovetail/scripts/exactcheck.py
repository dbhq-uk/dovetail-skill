#!/usr/bin/env python3
"""
Exact checks: findings with no judgement component.

Everything here is decided by parsing, never by pattern-guessing at prose. That
is the whole bargain of Layer 1 - a finding that reaches the user must be one
Python proved, because the moment this layer produces false positives the tool
becomes something people switch off.

Each check is therefore written to under-report rather than over-report. Where a
construct cannot be resolved exactly (a dynamically built argparse, a code block
full of placeholders, a path behind a shell variable) the check stays silent. A
missed finding is a shame; a wrong one costs the tool its licence to fail a build.
"""

from __future__ import annotations

import ast
import json
import os
import re
import tomllib
from typing import Iterable

from store import make_finding

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_FENCE = re.compile(
    r'^(?P<indent>[ \t]*)(?P<fence>```+|~~~+)[ \t]*(?P<lang>[A-Za-z0-9_+-]*)[^\n]*\n'
    r'(?P<body>.*?)'
    r'^(?P=indent)(?P=fence)[ \t]*$',
    re.S | re.M,
)

# A block carrying any of these is illustrative, not literal. Parsing it and
# reporting the inevitable SyntaxError would be noise.
_PLACEHOLDER = re.compile(r'(\.\.\.|…|<[A-Za-z][^>\n]*>|\$\{?[A-Z_]{2,}|/path/to/|YOUR_|xxx+)',
                          re.I)
_REPL = re.compile(r'^\s*(>>>|\$|#|%)\s', re.M)


def _read(repo_root: str, path: str) -> str | None:
    try:
        with open(os.path.join(repo_root, path), encoding='utf-8') as fh:
            return fh.read()
    except (OSError, UnicodeDecodeError):
        return None


def _line_of(text: str, index: int) -> int:
    return text.count('\n', 0, index) + 1


def code_blocks(text: str) -> list[tuple[str, str, int]]:
    """Every fenced block as (language, body, first body line number)."""
    blocks = []
    for m in _FENCE.finditer(text):
        body = m.group('body')
        indent = m.group('indent')
        if indent:  # strip the common indent so the body parses on its own
            body = '\n'.join(line[len(indent):] if line.startswith(indent) else line
                             for line in body.split('\n'))
        blocks.append((m.group('lang').lower(), body, _line_of(text, m.start('body'))))
    return blocks


def _docs(inventory: dict) -> Iterable[dict]:
    for entry in inventory['files']:
        if entry['path'].lower().endswith(('.md', '.markdown')):
            yield entry


def _python_files(inventory: dict) -> Iterable[dict]:
    for entry in inventory['files']:
        if entry['path'].endswith('.py'):
            yield entry


# ---------------------------------------------------------------------------
# Documented flags vs real flags
# ---------------------------------------------------------------------------

_FLAG_TOKEN = re.compile(r'(?<![\w-])--([A-Za-z][A-Za-z0-9_-]*)')


def _declared_flags(tree: ast.AST) -> tuple[set[str], bool]:
    """Long options an argparse/click module declares, and whether it is exact.

    The bool is False when the parser is built in a way this cannot read
    statically - a loop over a list of flags, say. An inexact parser means the
    check must not run at all for that script, because absence of a flag in the
    static set would not prove absence in the program.
    """
    flags: set[str] = set()
    exact = True
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, 'attr', None) or getattr(func, 'id', None)
        if name not in ('add_argument', 'option', 'add_option'):
            continue
        if not node.args:
            continue
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                if arg.value.startswith('--'):
                    flags.add(arg.value[2:])
            else:
                exact = False  # a computed option name; the set is incomplete
    return flags, exact


def _dest_aliases(flags: set[str]) -> set[str]:
    """argparse accepts --foo-bar as --foo_bar and vice versa in prose."""
    out = set(flags)
    for f in flags:
        out.add(f.replace('-', '_'))
        out.add(f.replace('_', '-'))
    return out


def flag_drift(inventory: dict, graph: dict) -> list[dict]:
    """A doc shows `script.py --flag` where the script declares no such flag.

    Scoped to command lines that actually invoke the script, rather than every
    `--token` in the document. A doc mentioning `--verbose` in prose about some
    other tool is not evidence about this one.
    """
    repo_root = inventory['repo_root']
    parsers: dict[str, tuple[set[str], bool]] = {}
    for entry in _python_files(inventory):
        text = _read(repo_root, entry['path'])
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        flags, exact = _declared_flags(tree)
        if flags or not exact:
            parsers[entry['path']] = (flags, exact)
    if not parsers:
        return []

    basenames: dict[str, list[str]] = {}
    for path in parsers:
        basenames.setdefault(os.path.basename(path), []).append(path)

    findings: list[dict] = []
    for entry in _docs(inventory):
        text = _read(repo_root, entry['path'])
        if text is None:
            continue
        for _lang, body, start_line in code_blocks(text):
            for offset, line in enumerate(body.split('\n')):
                if _PLACEHOLDER.search(line):
                    continue
                for base, candidates in basenames.items():
                    if base not in line:
                        continue
                    if len(candidates) != 1:
                        continue  # ambiguous which script; stay silent
                    script = candidates[0]
                    flags, exact = parsers[script]
                    if not exact:
                        continue
                    known = _dest_aliases(flags)
                    for used in _FLAG_TOKEN.findall(line):
                        if used in known:
                            continue
                        line_no = start_line + offset
                        findings.append(make_finding(
                            source='check:flags',
                            category='flag_drift',
                            problem=(f"{entry['path']} documents `--{used}` for "
                                     f'{base}, which declares no such option.'),
                            evidence=[
                                {'file': entry['path'], 'line': line_no,
                                 'quote': line.strip()[:200]},
                                {'file': script, 'line': 1,
                                 'quote': 'declares: ' + (', '.join(
                                     '--' + f for f in sorted(flags)) or '(none)')},
                            ],
                            suggestion=(f'Rename the documented flag to one of the '
                                        f'declared options, or add `--{used}` to {script}.'),
                            severity='medium',
                            claim=f'{script}|--{used}',
                        ))
    return findings


# ---------------------------------------------------------------------------
# Code blocks that do not parse
# ---------------------------------------------------------------------------

def _parses_python(body: str) -> str | None:
    try:
        ast.parse(body)
        return None
    except SyntaxError as exc:
        return f'{exc.msg} (line {exc.lineno})'


def _parses_json(body: str) -> str | None:
    try:
        json.loads(body)
        return None
    except json.JSONDecodeError as exc:
        return str(exc)


def _parses_toml(body: str) -> str | None:
    try:
        tomllib.loads(body)
        return None
    except tomllib.TOMLDecodeError as exc:
        return str(exc)


_PARSERS = {
    'python': _parses_python, 'py': _parses_python, 'python3': _parses_python,
    'json': _parses_json,
    'toml': _parses_toml,
}


def unparseable_code_blocks(inventory: dict, graph: dict) -> list[dict]:
    """A fenced block tagged with a language that will not parse as that language.

    Skips anything holding a placeholder or looking like a shell/REPL transcript:
    those are meant to be read, not run, and flagging them is exactly the noise
    that gets a checker disabled. `jsonl` and `jsonc` are deliberately absent
    from the parser table - neither is JSON.
    """
    repo_root = inventory['repo_root']
    findings: list[dict] = []
    for entry in _docs(inventory):
        text = _read(repo_root, entry['path'])
        if text is None:
            continue
        for lang, body, start_line in code_blocks(text):
            parser = _PARSERS.get(lang)
            if parser is None or not body.strip():
                continue
            if _PLACEHOLDER.search(body) or _REPL.search(body):
                continue
            error = parser(body)
            if error is None:
                continue
            findings.append(make_finding(
                source='check:codeblock',
                category='parse_error',
                problem=(f"{entry['path']} has a ```{lang} block that does not "
                         f'parse as {lang}: {error}'),
                evidence=[{'file': entry['path'], 'line': start_line,
                           'quote': body.strip().split('\n')[0][:200]}],
                suggestion='Fix the snippet, or retag the block if it is pseudo-code.',
                severity='medium',
                claim=f'{entry["path"]}|{lang}|{start_line}',
            ))
    return findings


# ---------------------------------------------------------------------------
# Paths named in prose that do not exist
# ---------------------------------------------------------------------------

_PATH_TOKEN = re.compile(r'`([^`\n]+)`')
_LOOKS_LIKE_PATH = re.compile(r'^[\w.@-]+(?:/[\w.@-]+)+$')
_HAS_EXT = re.compile(r'\.[A-Za-z0-9]{1,8}$')


def missing_paths(inventory: dict, graph: dict) -> list[dict]:
    """A backticked repo-relative path in prose that is not in the repository.

    Deliberately narrow. Only inline-code tokens are considered (prose sentences
    are far too noisy), the token must have a file extension and a directory
    separator, and anything resembling a URL, a glob, or a path outside the repo
    is skipped. Markdown *links* are already covered by the graph's broken-link
    check; this catches the mentions that are not links.
    """
    repo_root = inventory['repo_root']
    known = set(inventory['all_paths'])
    known_dirs = {os.path.dirname(p) for p in known if os.path.dirname(p)}
    findings: list[dict] = []

    for entry in _docs(inventory):
        text = _read(repo_root, entry['path'])
        if text is None:
            continue
        # Blank out fenced blocks so command examples are not treated as prose.
        stripped = _FENCE.sub(lambda m: '\n' * m.group(0).count('\n'), text)
        for m in _PATH_TOKEN.finditer(stripped):
            token = m.group(1).strip()
            if any(c in token for c in ' \t*?<>|$'):
                continue
            if '://' in token or token.startswith(('~', '/', '#')):
                continue
            if token.startswith('./'):
                token = token[2:]
            if not _LOOKS_LIKE_PATH.match(token) or not _HAS_EXT.search(token):
                continue
            if token in known or token in known_dirs:
                continue
            # Resolve relative to the mentioning document as well as the root:
            # `scripts/scan.py` inside skills/dovetail/README.md means the
            # sibling, and reporting it against the repo root would be wrong.
            local = os.path.normpath(os.path.join(os.path.dirname(entry['path']), token))
            if local in known or local in known_dirs:
                continue
            # The parent directory must exist somewhere, or this is almost
            # certainly a path the reader is being told to create rather than
            # one that has gone stale. Dogfooding caught this: every doc here
            # documents `.dovetail/decisions.jsonl`, a file the *user* writes in
            # *their* repo, and all four reads were false positives. A missing
            # file inside a directory that does exist is real drift; a missing
            # file inside a directory that does not is an example.
            if (os.path.dirname(token) not in known_dirs
                    and os.path.dirname(local) not in known_dirs):
                continue
            # Present on disk but untracked - a gitignored artefact like
            # .claude/settings.local.json is legitimately documented and
            # legitimately absent from the inventory. Referring to it is not
            # drift, so check the filesystem before reporting.
            if (os.path.exists(os.path.join(repo_root, token))
                    or os.path.exists(os.path.join(repo_root, local))):
                continue
            findings.append(make_finding(
                source='check:paths',
                category='missing_path',
                problem=(f"{entry['path']} refers to `{token}`, which does not "
                         'exist in the repository.'),
                evidence=[{'file': entry['path'], 'line': _line_of(stripped, m.start()),
                           'quote': token}],
                suggestion='Correct the path, or remove the reference if it is obsolete.',
                severity='medium',
                claim=f'{entry["path"]}|{token}',
            ))
    return findings


# ---------------------------------------------------------------------------
# Signature drift in documented examples
# ---------------------------------------------------------------------------

def _signatures(tree: ast.AST) -> dict[str, ast.arguments]:
    out: dict[str, ast.arguments] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.setdefault(node.name, node.args)
    return out


def _accepts(args: ast.arguments, positional: int, keywords: set[str]) -> str | None:
    """None when the call fits the signature, else why it does not."""
    names = [a.arg for a in args.posonlyargs] + [a.arg for a in args.args]
    if names and names[0] in ('self', 'cls'):
        names = names[1:]
    defaults = len(args.defaults)
    required = len(names) - defaults
    if args.vararg is None and positional > len(names):
        return f'takes {len(names)} positional argument(s), called with {positional}'
    if positional < required and not keywords:
        return f'requires {required} positional argument(s), called with {positional}'
    if args.kwarg is None:
        allowed = set(names) | {a.arg for a in args.kwonlyargs}
        unknown = sorted(keywords - allowed)
        if unknown:
            return 'no such keyword argument: ' + ', '.join(unknown)
    return None


def _document_definitions(text: str) -> set[str]:
    """Names defined in any python block of a document.

    A helper defined in one block is routinely called from another, so the
    shadow check has to span the whole document rather than a single block.
    """
    names: set[str] = set()
    for lang, body, _line in code_blocks(text):
        if lang not in ('python', 'py', 'python3'):
            continue
        try:
            tree = ast.parse(body)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.add(node.name)
    return names


def signature_drift(inventory: dict, graph: dict) -> list[dict]:
    """A documented call that the real function signature would reject.

    Only fires when exactly one function of that name exists in the repository,
    the call is a plain call in a ```python block, and every argument is
    statically readable. Anything else is silence.
    """
    repo_root = inventory['repo_root']
    sigs: dict[str, list[tuple[str, ast.arguments]]] = {}
    for entry in _python_files(inventory):
        text = _read(repo_root, entry['path'])
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for name, args in _signatures(tree).items():
            sigs.setdefault(name, []).append((entry['path'], args))
    if not sigs:
        return []

    findings: list[dict] = []
    for entry in _docs(inventory):
        text = _read(repo_root, entry['path'])
        if text is None:
            continue
        for lang, body, start_line in code_blocks(text):
            if lang not in ('python', 'py', 'python3'):
                continue
            if _PLACEHOLDER.search(body) or _REPL.search(body):
                continue
            try:
                tree = ast.parse(body)
            except SyntaxError:
                continue  # unparseable_code_blocks owns that finding
            # A name the snippet defines itself refers to that definition, not
            # to a same-named function elsewhere in the repository. Without
            # this, a document containing its own `write(repo, path, body)`
            # test helper is checked against an unrelated `write` and every
            # call is reported. Dogfooding produced 17 such false positives
            # from a single archived document.
            local_defs = {n.name for n in ast.walk(tree)
                          if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef,
                                            ast.ClassDef))}
            doc_defs = _document_definitions(text)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
                    continue
                if node.func.id in local_defs or node.func.id in doc_defs:
                    continue
                target = sigs.get(node.func.id)
                if not target or len(target) != 1:
                    continue
                if any(isinstance(a, ast.Starred) for a in node.args):
                    continue
                if any(k.arg is None for k in node.keywords):
                    continue
                path, args = target[0]
                reason = _accepts(args, len(node.args), {k.arg for k in node.keywords})
                if reason is None:
                    continue
                line_no = start_line + (node.lineno - 1)
                findings.append(make_finding(
                    source='check:signature',
                    category='signature_drift',
                    problem=(f"{entry['path']} calls `{node.func.id}(...)` in a way "
                             f'{path} would reject: {reason}.'),
                    evidence=[
                        {'file': entry['path'], 'line': line_no,
                         'quote': ast.unparse(node)[:200]},
                        {'file': path, 'line': 1,
                         'quote': f'def {node.func.id}({ast.unparse(args)})'[:200]},
                    ],
                    suggestion='Update the example to match the signature, or the signature to match.',
                    severity='medium',
                    claim=f'{path}|{node.func.id}|{ast.unparse(node)}',
                ))
    return findings


# ---------------------------------------------------------------------------
# Version / identifier divergence
# ---------------------------------------------------------------------------

_SEMVER = re.compile(r'\b(\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?)\b')


def _declared_versions(repo_root: str, known: set[str]) -> list[tuple[str, str]]:
    """(version, source path) declared by a package manifest."""
    out: list[tuple[str, str]] = []
    if 'package.json' in known:
        text = _read(repo_root, 'package.json')
        if text:
            try:
                data = json.loads(text)
                if isinstance(data.get('version'), str):
                    out.append((data['version'], 'package.json'))
            except json.JSONDecodeError:
                pass
    if 'pyproject.toml' in known:
        text = _read(repo_root, 'pyproject.toml')
        if text:
            try:
                data = tomllib.loads(text)
                version = (data.get('project') or {}).get('version')
                if isinstance(version, str):
                    out.append((version, 'pyproject.toml'))
            except tomllib.TOMLDecodeError:
                pass
    for rel in ('.claude-plugin/plugin.json',):
        if rel in known:
            text = _read(repo_root, rel)
            if text:
                try:
                    data = json.loads(text)
                    if isinstance(data.get('version'), str):
                        out.append((data['version'], rel))
                except json.JSONDecodeError:
                    pass
    return out


def version_drift(inventory: dict, graph: dict) -> list[dict]:
    """Two manifests in the same repository declaring different versions.

    Only manifest-versus-manifest. Comparing a manifest against a semver-shaped
    string somewhere in prose was tried and rejected in review: dependency pins,
    changelog entries and example output all look identical to a project version,
    and the check has to be trustworthy more than it has to be clever.
    """
    repo_root = inventory['repo_root']
    known = set(inventory['all_paths'])
    declared = _declared_versions(repo_root, known)
    if len(declared) < 2:
        return []
    distinct = {v for v, _ in declared}
    if len(distinct) < 2:
        return []
    evidence = [{'file': path, 'line': 1, 'quote': f'version {version}'}
                for version, path in declared]
    return [make_finding(
        source='check:version',
        category='version_drift',
        problem='Manifests in this repository declare different versions: '
                + ', '.join(f'{p} says {v}' for v, p in declared) + '.',
        evidence=evidence,
        suggestion='Align the manifests, or record why they version independently.',
        severity='medium',
        claim='|'.join(sorted(f'{p}={v}' for v, p in declared)),
    )]


# ---------------------------------------------------------------------------
# Dead Python code
# ---------------------------------------------------------------------------

_DUNDER = re.compile(r'^__\w+__$')


def dead_python_code(inventory: dict, graph: dict) -> list[dict]:
    """A module-level public function or class nothing in the repository names.

    Name-based on purpose: a symbol referenced anywhere by name - another
    module, a test, a doc, a string in a config - is live. That over-counts
    liveness, which is the correct direction to be wrong in for a check allowed
    to fail a build.

    Private (`_`-prefixed) and dunder names are skipped, as are `__init__.py`
    re-exports, `conftest.py`, and anything under a tests directory.
    """
    repo_root = inventory['repo_root']
    definitions: list[tuple[str, str, int]] = []
    corpus: list[tuple[str, str]] = []

    for entry in inventory['files']:
        text = _read(repo_root, entry['path'])
        if text is None:
            continue
        corpus.append((entry['path'], text))

    text_by_path = dict(corpus)

    for entry in _python_files(inventory):
        path = entry['path']
        base = os.path.basename(path)
        parts = path.split('/')
        if base in ('__init__.py', 'conftest.py', 'setup.py'):
            continue
        if any(p in ('test', 'tests', 'fixtures') for p in parts) or base.startswith('test_'):
            continue
        text = text_by_path.get(path)
        if text is None:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in tree.body:  # module level only
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                continue
            name = node.name
            if name.startswith('_') or _DUNDER.match(name):
                continue
            definitions.append((path, name, node.lineno))

    if not definitions:
        return []

    findings: list[dict] = []
    for path, name, line in definitions:
        pattern = re.compile(r'(?<![\w])' + re.escape(name) + r'(?![\w])')
        used = False
        for other_path, text in corpus:
            if other_path == path:
                # Uses inside the defining module still count, but the def line
                # itself must not: `def foo` is not a use of foo.
                without_def = re.sub(r'^\s*(?:async\s+)?(?:def|class)\s+'
                                     + re.escape(name) + r'\b', '', text, flags=re.M)
                if pattern.search(without_def):
                    used = True
                    break
                continue
            if pattern.search(text):
                used = True
                break
        if used:
            continue
        findings.append(make_finding(
            source='check:deadcode',
            category='dead_code',
            problem=f'`{name}` in {path} is public but nothing in the repository names it.',
            evidence=[{'file': path, 'line': line, 'quote': f'def/class {name}'}],
            suggestion='Remove it, make it private, or reference it where it is meant to be used.',
            severity='low',
            claim=f'{path}|{name}',
        ))
    return findings


ALL_CHECKS = [
    flag_drift,
    unparseable_code_blocks,
    missing_paths,
    signature_drift,
    version_drift,
    dead_python_code,
]
