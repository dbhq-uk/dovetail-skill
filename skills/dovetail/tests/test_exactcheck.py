"""Tests for the exact checks.

Each check gets a positive case, and - more importantly - the negative cases
that caused real false positives when the checks were first run against live
repositories. Those regressions are the point: a check that over-reports is
worse than no check, because it costs the tool permission to fail a build.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from discover import discover  # noqa: E402
from exactcheck import (  # noqa: E402
    code_blocks, dead_python_code, flag_drift, missing_paths,
    signature_drift, unparseable_code_blocks, version_drift,
)
from refgraph import build_graph  # noqa: E402


def write(repo: str, rel: str, body: str) -> None:
    path = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write(body)


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = self._tmp.name
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True)
        for key, value in (('gc.auto', '0'), ('maintenance.auto', 'false')):
            subprocess.run(['git', '-C', self.repo, 'config', key, value],
                           check=True, capture_output=True)
        subprocess.run(['git', 'config', 'user.email', 't@example.com'],
                       cwd=self.repo, check=True)
        subprocess.run(['git', 'config', 'user.name', 'T'], cwd=self.repo, check=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def scan(self):
        inventory = discover(self.repo)
        return inventory, build_graph(self.repo, inventory)


class CodeBlockParsing(Base):
    def test_indented_block_is_dedented(self):
        blocks = code_blocks('text\n\n    ```python\n    x = 1\n    ```\n')
        self.assertEqual(blocks[0][0], 'python')
        self.assertEqual(blocks[0][1].strip(), 'x = 1')

    def test_language_and_line_number(self):
        blocks = code_blocks('a\nb\n```json\n{}\n```\n')
        self.assertEqual(blocks[0][0], 'json')
        self.assertEqual(blocks[0][2], 4)


class FlagDrift(Base):
    SCRIPT = (
        'import argparse\n'
        'p = argparse.ArgumentParser()\n'
        'p.add_argument("--output")\n'
    )

    def test_undocumented_flag_is_found(self):
        write(self.repo, 'scripts/run.py', self.SCRIPT)
        write(self.repo, 'README.md', 'Run it:\n\n```bash\nrun.py --out FILE\n```\n')
        found = flag_drift(*self.scan())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'flag_drift')

    def test_declared_flag_is_not_flagged(self):
        write(self.repo, 'scripts/run.py', self.SCRIPT)
        write(self.repo, 'README.md', '```bash\nrun.py --output FILE\n```\n')
        self.assertEqual(flag_drift(*self.scan()), [])

    def test_underscore_alias_accepted(self):
        write(self.repo, 'scripts/run.py',
              'import argparse\np = argparse.ArgumentParser()\n'
              'p.add_argument("--fail-on")\n')
        write(self.repo, 'README.md', '```bash\nrun.py --fail_on high\n```\n')
        self.assertEqual(flag_drift(*self.scan()), [])

    def test_dynamic_parser_is_skipped(self):
        # The flag set cannot be read statically, so absence proves nothing.
        write(self.repo, 'scripts/run.py',
              'import argparse\np = argparse.ArgumentParser()\n'
              'for f in ["--a", "--b"]:\n    p.add_argument(f)\n')
        write(self.repo, 'README.md', '```bash\nrun.py --anything\n```\n')
        self.assertEqual(flag_drift(*self.scan()), [])

    def test_placeholder_line_is_skipped(self):
        write(self.repo, 'scripts/run.py', self.SCRIPT)
        write(self.repo, 'README.md', '```bash\nrun.py --output <FILE>\n```\n')
        self.assertEqual(flag_drift(*self.scan()), [])


class UnparseableBlocks(Base):
    def test_broken_python_is_found(self):
        write(self.repo, 'README.md', '```python\ndef f(:\n```\n')
        found = unparseable_code_blocks(*self.scan())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'parse_error')

    def test_broken_json_is_found(self):
        write(self.repo, 'README.md', '```json\n{"a": }\n```\n')
        self.assertEqual(len(unparseable_code_blocks(*self.scan())), 1)

    def test_valid_blocks_are_silent(self):
        write(self.repo, 'README.md',
              '```python\nx = 1\n```\n\n```json\n{"a": 1}\n```\n\n```toml\na = 1\n```\n')
        self.assertEqual(unparseable_code_blocks(*self.scan()), [])

    def test_repl_transcript_is_skipped(self):
        write(self.repo, 'README.md', '```python\n>>> x = (\n```\n')
        self.assertEqual(unparseable_code_blocks(*self.scan()), [])

    def test_placeholder_block_is_skipped(self):
        write(self.repo, 'README.md', '```python\ndef f(...):\n```\n')
        self.assertEqual(unparseable_code_blocks(*self.scan()), [])

    def test_jsonl_is_not_treated_as_json(self):
        # Two objects on two lines is valid JSONL and invalid JSON.
        write(self.repo, 'README.md', '```jsonl\n{"a":1}\n{"a":2}\n```\n')
        self.assertEqual(unparseable_code_blocks(*self.scan()), [])


class MissingPaths(Base):
    def test_missing_sibling_is_found(self):
        write(self.repo, 'docs/a.md', 'x\n')
        write(self.repo, 'README.md', 'See `docs/gone.md` for detail.\n')
        found = missing_paths(*self.scan())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'missing_path')

    def test_existing_path_is_silent(self):
        write(self.repo, 'docs/a.md', 'x\n')
        write(self.repo, 'README.md', 'See `docs/a.md`.\n')
        self.assertEqual(missing_paths(*self.scan()), [])

    def test_path_in_a_directory_that_does_not_exist_is_skipped(self):
        # Regression: every doc documents `.dovetail/decisions.jsonl`, a file
        # the *reader* creates in *their* repo. Four false positives.
        write(self.repo, 'README.md', 'Append to `.dovetail/decisions.jsonl`.\n')
        self.assertEqual(missing_paths(*self.scan()), [])

    def test_untracked_but_present_file_is_skipped(self):
        # Regression: `.claude/settings.local.json` is gitignored, so it is
        # absent from the inventory but present on disk. Documenting it is fine.
        write(self.repo, '.claude/config.json', '{}\n')
        write(self.repo, '.claude/settings.local.json', '{}\n')
        write(self.repo, '.gitignore', 'settings.local.json\n')
        write(self.repo, 'README.md', 'See `.claude/settings.local.json`.\n')
        self.assertEqual(missing_paths(*self.scan()), [])

    def test_code_blocks_are_not_prose(self):
        write(self.repo, 'docs/a.md', 'x\n')
        write(self.repo, 'README.md', '```bash\ncat docs/gone.md\n```\n')
        self.assertEqual(missing_paths(*self.scan()), [])

    def test_urls_are_skipped(self):
        write(self.repo, 'README.md', 'See `https://example.com/a/b.md`.\n')
        self.assertEqual(missing_paths(*self.scan()), [])


class SignatureDrift(Base):
    def test_too_many_arguments_is_found(self):
        write(self.repo, 'lib.py', 'def render(value):\n    return value\n')
        write(self.repo, 'README.md', '```python\nrender(1, 2)\n```\n')
        found = signature_drift(*self.scan())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'signature_drift')

    def test_unknown_keyword_is_found(self):
        write(self.repo, 'lib.py', 'def render(value):\n    return value\n')
        write(self.repo, 'README.md', '```python\nrender(value=1, mode="x")\n```\n')
        self.assertEqual(len(signature_drift(*self.scan())), 1)

    def test_matching_call_is_silent(self):
        write(self.repo, 'lib.py', 'def render(value, mode="a"):\n    return value\n')
        write(self.repo, 'README.md', '```python\nrender(1, mode="b")\n```\n')
        self.assertEqual(signature_drift(*self.scan()), [])

    def test_locally_defined_helper_shadows_the_repo(self):
        # Regression: an archived plan defined its own `write(repo, path, body)`
        # helper in a fixture block; every call was checked against an unrelated
        # repo-level `write` and reported. 17 false positives from one document.
        write(self.repo, 'lib.py', 'def write(value):\n    return value\n')
        write(self.repo, 'PLAN.md',
              '```python\ndef write(repo, path, body):\n    pass\n```\n'
              '\n```python\nwrite("a", "b", "c")\n```\n')
        self.assertEqual(signature_drift(*self.scan()), [])

    def test_ambiguous_name_is_skipped(self):
        write(self.repo, 'a.py', 'def render(x):\n    return x\n')
        write(self.repo, 'b.py', 'def render(x, y):\n    return x\n')
        write(self.repo, 'README.md', '```python\nrender(1, 2)\n```\n')
        self.assertEqual(signature_drift(*self.scan()), [])

    def test_star_args_are_skipped(self):
        write(self.repo, 'lib.py', 'def render(value):\n    return value\n')
        write(self.repo, 'README.md', '```python\nrender(*args)\n```\n')
        self.assertEqual(signature_drift(*self.scan()), [])


class VersionDrift(Base):
    def test_disagreeing_manifests_are_found(self):
        write(self.repo, 'package.json', '{"version": "1.2.0"}\n')
        write(self.repo, 'pyproject.toml', '[project]\nversion = "1.3.0"\n')
        found = version_drift(*self.scan())
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]['category'], 'version_drift')

    def test_agreeing_manifests_are_silent(self):
        write(self.repo, 'package.json', '{"version": "1.2.0"}\n')
        write(self.repo, 'pyproject.toml', '[project]\nversion = "1.2.0"\n')
        self.assertEqual(version_drift(*self.scan()), [])

    def test_single_manifest_is_silent(self):
        write(self.repo, 'package.json', '{"version": "1.2.0"}\n')
        self.assertEqual(version_drift(*self.scan()), [])


class DeadCode(Base):
    def test_unreferenced_public_function_is_found(self):
        write(self.repo, 'lib.py', 'def orphaned():\n    return 1\n')
        write(self.repo, 'main.py', 'x = 1\n')
        found = dead_python_code(*self.scan())
        self.assertEqual([f['evidence'][0]['file'] for f in found], ['lib.py'])

    def test_referenced_function_is_silent(self):
        write(self.repo, 'lib.py', 'def used():\n    return 1\n')
        write(self.repo, 'main.py', 'from lib import used\nused()\n')
        self.assertEqual(dead_python_code(*self.scan()), [])

    def test_mentioned_in_a_doc_counts_as_live(self):
        write(self.repo, 'lib.py', 'def documented():\n    return 1\n')
        write(self.repo, 'README.md', 'Call `documented()` to do the thing.\n')
        self.assertEqual(dead_python_code(*self.scan()), [])

    def test_private_and_dunder_are_skipped(self):
        write(self.repo, 'lib.py',
              'def _helper():\n    return 1\n\n\ndef _other():\n    return 2\n')
        self.assertEqual(dead_python_code(*self.scan()), [])

    def test_tests_are_skipped(self):
        write(self.repo, 'tests/test_thing.py', 'def test_x():\n    pass\n')
        self.assertEqual(dead_python_code(*self.scan()), [])


if __name__ == '__main__':
    unittest.main()
