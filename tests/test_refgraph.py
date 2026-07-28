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
