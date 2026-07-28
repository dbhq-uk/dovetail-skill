#!/usr/bin/env python3
"""Tests for classify.py."""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from classify import classify  # noqa: E402


class TestModality(unittest.TestCase):
    def test_raster_image(self):
        self.assertEqual(classify('a/logo.png', b'\x89PNG')[0], 'raster_image')

    def test_vector_diagram(self):
        self.assertEqual(classify('a/flow.mmd', b'graph TD')[0], 'vector_diagram')

    def test_binary_detected_by_nul_byte(self):
        self.assertEqual(classify('a/blob.dat', b'abc\x00def')[0], 'binary')

    def test_text(self):
        self.assertEqual(classify('a/README.md', b'# Hello')[0], 'text')

    def test_nul_sniff_only_reads_first_8000_bytes(self):
        content = b'a' * 8000 + b'\x00'
        self.assertEqual(classify('a/big.txt', content)[0], 'text')


class TestCategory(unittest.TestCase):
    def test_icon_by_extension(self):
        self.assertEqual(classify('a/app.ico', b'\x00\x00\x01')[1], 'icon')

    def test_icon_by_name_only_for_images(self):
        self.assertEqual(classify('a/icon-home.png', b'\x89PNG')[1], 'icon')

    def test_text_file_named_icon_is_not_an_icon(self):
        # Guards the bug where visual_icon.md was classified as an icon
        self.assertEqual(classify('reviewers/visual_icon.md', b'# rubric')[1], 'doc')

    def test_raster_is_visual(self):
        self.assertEqual(classify('a/screenshot.png', b'\x89PNG')[1], 'visual')

    def test_spec_by_path_segment(self):
        self.assertEqual(classify('spec/api.md', b'# api')[1], 'spec')
        self.assertEqual(classify('docs/specs/api.md', b'# api')[1], 'spec')

    def test_dot_spec_filename_is_not_a_spec(self):
        # `client.spec.ts` is a test file, not a specification
        self.assertEqual(classify('src/client.spec.ts', b'test()')[1], 'code')

    def test_flow_by_name(self):
        self.assertEqual(classify('docs/login-flow.md', b'x')[1], 'flow')
        self.assertEqual(classify('docs/flow_diagram.md', b'x')[1], 'flow')

    def test_svg_is_visual(self):
        self.assertEqual(classify('assets/banner.svg', b'<svg/>')[1], 'visual')

    def test_mmd_is_flow(self):
        self.assertEqual(classify('docs/states.mmd', b'graph TD')[1], 'flow')

    def test_code(self):
        self.assertEqual(classify('scripts/run.py', b'x = 1')[1], 'code')

    def test_doc(self):
        self.assertEqual(classify('README.md', b'# hi')[1], 'doc')

    def test_config(self):
        self.assertEqual(classify('config.yml', b'a: 1')[1], 'config')

    def test_other(self):
        self.assertEqual(classify('LICENSE', b'MIT')[1], 'other')


if __name__ == '__main__':
    unittest.main()
