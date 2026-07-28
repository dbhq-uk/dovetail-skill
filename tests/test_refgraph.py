#!/usr/bin/env python3
"""Tests for refgraph.py."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from refgraph import build_graph  # noqa: E402


def write(repo: str, rel: str, text: str) -> None:
    full = os.path.join(repo, rel)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, 'w', encoding='utf-8') as fh:
        fh.write(text)


def fake_inventory(repo: str, paths: list[str]) -> dict:
    files = []
    for p in paths:
        ext = os.path.splitext(p)[1]
        modality = 'raster_image' if ext == '.png' else 'text'
        files.append({
            'path': p, 'modality': modality, 'category': 'doc',
            'size_bytes': 1, 'sha256': 'x', 'last_commit_iso': None,
        })
    return {'repo_root': os.path.abspath(repo), 'generated_at_iso': '', 'files': files}


class GraphCase(unittest.TestCase):
    def setUp(self):
        self.repo = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.repo, ignore_errors=True)

    def graph(self, paths):
        return build_graph(self.repo, fake_inventory(self.repo, paths))


class TestMarkdownLinks(GraphCase):
    def test_resolves_relative_link(self):
        write(self.repo, 'README.md', 'see [setup](docs/setup.md)\n')
        write(self.repo, 'docs/setup.md', '# Setup\n')
        g = self.graph(['README.md', 'docs/setup.md'])
        edge = next(e for e in g['edges'] if e['kind'] == 'md_link')
        self.assertEqual(edge['dst'], 'docs/setup.md')
        self.assertEqual(edge['src'], 'README.md')
        self.assertEqual(edge['line'], 1)

    def test_resolves_dot_dot_link(self):
        write(self.repo, 'docs/a.md', 'see [root](../README.md)\n')
        write(self.repo, 'README.md', '# hi\n')
        g = self.graph(['docs/a.md', 'README.md'])
        self.assertEqual(g['edges'][0]['dst'], 'README.md')

    def test_unresolvable_link_has_null_dst(self):
        write(self.repo, 'README.md', 'see [gone](docs/gone.md)\n')
        g = self.graph(['README.md'])
        self.assertIsNone(g['edges'][0]['dst'])
        self.assertEqual(g['edges'][0]['raw'], 'docs/gone.md')

    def test_external_urls_are_not_edges(self):
        write(self.repo, 'README.md',
              '[a](https://x.com) [b](http://x.com) [c](mailto:a@b.c) [d](//x.com)\n')
        self.assertEqual(self.graph(['README.md'])['edges'], [])

    def test_image_edges_are_kind_md_image(self):
        write(self.repo, 'README.md', '![logo](assets/logo.png)\n')
        write(self.repo, 'assets/logo.png', 'x')
        g = self.graph(['README.md', 'assets/logo.png'])
        self.assertEqual(g['edges'][0]['kind'], 'md_image')
        self.assertEqual(g['edges'][0]['dst'], 'assets/logo.png')

    def test_reference_definitions(self):
        write(self.repo, 'README.md', 'text [ref]\n\n[ref]: docs/a.md\n')
        write(self.repo, 'docs/a.md', '# a\n')
        g = self.graph(['README.md', 'docs/a.md'])
        kinds = {e['kind'] for e in g['edges']}
        self.assertIn('md_refdef', kinds)

    def test_links_inside_fenced_code_are_ignored(self):
        write(self.repo, 'README.md', '```\n[x](docs/nope.md)\n```\n')
        self.assertEqual(self.graph(['README.md'])['edges'], [])

    def test_line_numbers_are_one_indexed(self):
        write(self.repo, 'README.md', 'line one\n\n[a](b.md)\n')
        write(self.repo, 'b.md', 'x')
        g = self.graph(['README.md', 'b.md'])
        self.assertEqual(g['edges'][0]['line'], 3)


class TestAnchors(GraphCase):
    def test_same_file_anchor(self):
        write(self.repo, 'README.md', '# Setup\n\n[jump](#setup)\n')
        g = self.graph(['README.md'])
        edge = g['edges'][0]
        self.assertEqual(edge['dst'], 'README.md')
        self.assertEqual(edge['anchor'], 'setup')

    def test_cross_file_anchor(self):
        write(self.repo, 'README.md', '[jump](docs/a.md#install)\n')
        write(self.repo, 'docs/a.md', '# Install\n')
        g = self.graph(['README.md', 'docs/a.md'])
        self.assertEqual(g['edges'][0]['dst'], 'docs/a.md')
        self.assertEqual(g['edges'][0]['anchor'], 'install')

    def test_headings_index_is_populated(self):
        write(self.repo, 'docs/a.md', '# Install\n## Advanced Setup\n')
        g = self.graph(['docs/a.md'])
        self.assertEqual(g['headings']['docs/a.md'], ['install', 'advanced-setup'])


class TestHtmlAndImports(GraphCase):
    def test_html_src(self):
        write(self.repo, 'README.md', '<img src="assets/logo.png">\n')
        write(self.repo, 'assets/logo.png', 'x')
        g = self.graph(['README.md', 'assets/logo.png'])
        self.assertEqual(g['edges'][0]['kind'], 'html')

    def test_python_relative_import_is_not_a_path_edge(self):
        # Bare module imports are not file paths; only quoted specifiers count.
        write(self.repo, 'a.py', 'import os\n')
        self.assertEqual(self.graph(['a.py'])['edges'], [])

    def test_js_import_specifier(self):
        write(self.repo, 'src/a.ts', "import { x } from './b.js';\n")
        write(self.repo, 'src/b.ts', 'export const x = 1;\n')
        g = self.graph(['src/a.ts', 'src/b.ts'])
        edge = next(e for e in g['edges'] if e['kind'] == 'import')
        self.assertEqual(edge['dst'], 'src/b.ts')

    def test_require_specifier(self):
        write(self.repo, 'src/a.js', "const b = require('./b.js');\n")
        write(self.repo, 'src/b.js', 'module.exports = 1;\n')
        g = self.graph(['src/a.js', 'src/b.js'])
        self.assertEqual(next(e for e in g['edges'] if e['kind'] == 'import')['dst'],
                         'src/b.js')


class TestPathLiterals(GraphCase):
    def test_bare_path_in_prose_is_an_edge(self):
        write(self.repo, 'README.md', 'Run `scripts/run.py` to start.\n')
        write(self.repo, 'scripts/run.py', 'x = 1\n')
        g = self.graph(['README.md', 'scripts/run.py'])
        edge = next(e for e in g['edges'] if e['kind'] == 'path_literal')
        self.assertEqual(edge['dst'], 'scripts/run.py')

    def test_bare_word_is_not_an_edge(self):
        # The upkeep bug: the word "config" must not reference config.py
        write(self.repo, 'README.md', 'Edit the config to taste.\n')
        write(self.repo, 'config.py', 'x = 1\n')
        g = self.graph(['README.md', 'config.py'])
        self.assertEqual([e for e in g['edges'] if e['dst'] == 'config.py'], [])


class TestPythonImports(GraphCase):
    def test_from_import_of_sibling_module(self):
        write(self.repo, 'scripts/discover.py', 'from classify import classify\n')
        write(self.repo, 'scripts/classify.py', 'def classify(): pass\n')
        g = self.graph(['scripts/discover.py', 'scripts/classify.py'])
        edge = next(e for e in g['edges'] if e['kind'] == 'import')
        self.assertEqual(edge['dst'], 'scripts/classify.py')
        self.assertEqual(g['inbound']['scripts/classify.py'], ['scripts/discover.py'])

    def test_plain_import_of_sibling_module(self):
        write(self.repo, 'scripts/a.py', 'import helper\n')
        write(self.repo, 'scripts/helper.py', 'x = 1\n')
        g = self.graph(['scripts/a.py', 'scripts/helper.py'])
        self.assertEqual(next(e for e in g['edges'] if e['kind'] == 'import')['dst'],
                         'scripts/helper.py')

    def test_stdlib_import_creates_no_edge(self):
        write(self.repo, 'scripts/a.py', 'import os\nfrom pathlib import Path\n')
        self.assertEqual(self.graph(['scripts/a.py'])['edges'], [])

    def test_package_import_resolves_to_init(self):
        write(self.repo, 'a.py', 'from pkg import thing\n')
        write(self.repo, 'pkg/__init__.py', 'thing = 1\n')
        g = self.graph(['a.py', 'pkg/__init__.py'])
        self.assertEqual(next(e for e in g['edges'] if e['kind'] == 'import')['dst'],
                         'pkg/__init__.py')

    def test_import_word_in_prose_creates_no_edge(self):
        # The precision guard: only .py files are scanned for bare imports.
        write(self.repo, 'README.md', 'You can import config to override this.\n')
        write(self.repo, 'config.py', 'x = 1\n')
        g = self.graph(['README.md', 'config.py'])
        self.assertEqual([e for e in g['edges'] if e['dst'] == 'config.py'], [])

    def test_relative_import_resolves(self):
        write(self.repo, 'pkg/a.py', 'from .b import thing\n')
        write(self.repo, 'pkg/b.py', 'thing = 1\n')
        g = self.graph(['pkg/a.py', 'pkg/b.py'])
        self.assertEqual(next(e for e in g['edges'] if e['kind'] == 'import')['dst'],
                         'pkg/b.py')

    def test_import_inside_a_docstring_creates_no_edge(self):
        # The precision guard: ast sees docstrings as strings, not imports.
        write(self.repo, 'scripts/tool.py',
              '"""\nExample:\n    from helper import run\n"""\n')
        write(self.repo, 'scripts/helper.py', 'def run(): pass\n')
        g = self.graph(['scripts/tool.py', 'scripts/helper.py'])
        self.assertEqual([e for e in g['edges'] if e['kind'] == 'import'], [])

    def test_import_inside_a_comment_creates_no_edge(self):
        write(self.repo, 'scripts/tool.py', '# from helper import run\n')
        write(self.repo, 'scripts/helper.py', 'def run(): pass\n')
        g = self.graph(['scripts/tool.py', 'scripts/helper.py'])
        self.assertEqual([e for e in g['edges'] if e['kind'] == 'import'], [])

    def test_stdlib_name_shadowed_by_a_repo_file_creates_no_edge(self):
        write(self.repo, 'scripts/other.py', 'import json\n')
        write(self.repo, 'json.py', 'x = 1\n')
        g = self.graph(['scripts/other.py', 'json.py'])
        self.assertEqual([e for e in g['edges'] if e['dst'] == 'json.py'], [])

    def test_relative_import_of_a_stdlib_name_still_resolves(self):
        write(self.repo, 'pkg/a.py', 'from .json import thing\n')
        write(self.repo, 'pkg/json.py', 'thing = 1\n')
        g = self.graph(['pkg/a.py', 'pkg/json.py'])
        self.assertEqual(next(e for e in g['edges'] if e['kind'] == 'import')['dst'],
                         'pkg/json.py')

    def test_import_nested_in_a_function_is_found(self):
        write(self.repo, 'scripts/a.py',
              'def go():\n    from helper import run\n    return run\n')
        write(self.repo, 'scripts/helper.py', 'def run(): pass\n')
        g = self.graph(['scripts/a.py', 'scripts/helper.py'])
        edge = next(e for e in g['edges'] if e['kind'] == 'import')
        self.assertEqual(edge['dst'], 'scripts/helper.py')
        self.assertEqual(edge['line'], 2)

    def test_raw_is_the_module_not_the_whole_line(self):
        write(self.repo, 'scripts/a.py', 'from helper import run  # noqa\n')
        write(self.repo, 'scripts/helper.py', 'def run(): pass\n')
        g = self.graph(['scripts/a.py', 'scripts/helper.py'])
        self.assertEqual(next(e for e in g['edges'] if e['kind'] == 'import')['raw'],
                         'helper')

    def test_unparseable_python_yields_no_import_edges(self):
        write(self.repo, 'scripts/broken.py', 'def (((\n')
        write(self.repo, 'scripts/helper.py', 'def run(): pass\n')
        g = self.graph(['scripts/broken.py', 'scripts/helper.py'])
        self.assertEqual([e for e in g['edges'] if e['kind'] == 'import'], [])


class TestKindScoping(GraphCase):
    def test_js_import_regex_does_not_run_on_python(self):
        # refgraph's own docstring describes JS import syntax; that text must
        # not become an edge when the file is Python.
        write(self.repo, 'scripts/a.py',
              '"""Docs: from \'./x.js\' / require(\'./x.js\') are JS forms."""\n')
        write(self.repo, 'scripts/x.js', 'module.exports = 1;\n')
        g = self.graph(['scripts/a.py', 'scripts/x.js'])
        self.assertEqual([e for e in g['edges'] if e['kind'] == 'import'], [])

    def test_markdown_link_regex_does_not_run_on_python(self):
        write(self.repo, 'scripts/a.py', '# see [setup](docs/setup.md)\n')
        write(self.repo, 'docs/setup.md', '# Setup\n')
        g = self.graph(['scripts/a.py', 'docs/setup.md'])
        self.assertEqual([e for e in g['edges'] if e['kind'] == 'md_link'], [])

    def test_python_path_literal_still_captured(self):
        # A genuine data-file reference from Python must still create an edge.
        write(self.repo, 'scripts/a.py', "open('data/config.json')\n")
        write(self.repo, 'data/config.json', '{}\n')
        g = self.graph(['scripts/a.py', 'data/config.json'])
        self.assertEqual(next(e for e in g['edges'] if e['kind'] == 'path_literal')['dst'],
                         'data/config.json')

    def test_bare_import_regex_does_not_run_on_markdown(self):
        write(self.repo, 'README.md', "Use `from helper import run` in your code.\n")
        write(self.repo, 'helper.py', 'def run(): pass\n')
        g = self.graph(['README.md', 'helper.py'])
        self.assertEqual([e for e in g['edges'] if e['dst'] == 'helper.py'], [])

    def test_version_range_is_not_a_path(self):
        write(self.repo, 'README.md', 'Supports Python 3.11/3.12 and later.\n')
        self.assertEqual(self.graph(['README.md'])['edges'], [])


class TestInbound(GraphCase):
    def test_inbound_lists_referencing_files(self):
        write(self.repo, 'a.md', '[x](target.md)\n')
        write(self.repo, 'b.md', '[y](target.md)\n')
        write(self.repo, 'target.md', '# t\n')
        g = self.graph(['a.md', 'b.md', 'target.md'])
        self.assertEqual(sorted(g['inbound']['target.md']), ['a.md', 'b.md'])

    def test_self_reference_is_not_inbound(self):
        write(self.repo, 'a.md', '# H\n\n[jump](#h)\n')
        g = self.graph(['a.md'])
        self.assertEqual(g['inbound'].get('a.md', []), [])

    def test_every_file_has_an_inbound_key(self):
        write(self.repo, 'lonely.md', 'nothing\n')
        g = self.graph(['lonely.md'])
        self.assertEqual(g['inbound']['lonely.md'], [])


if __name__ == '__main__':
    unittest.main()
