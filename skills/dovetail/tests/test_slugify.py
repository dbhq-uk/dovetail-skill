#!/usr/bin/env python3
"""Tests for slugify.py - GitHub-compatible anchor ids."""

from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

from slugify import anchor_ids, heading_slugs, html_ids, slugify  # noqa: E402

FIXTURE = os.path.join(os.path.dirname(__file__), 'fixtures', 'github_anchors.json')


class TestAgainstGitHub(unittest.TestCase):
    """Ids GitHub itself rendered, from `POST /markdown`.

    The fixture was made by fixtures/render_github_anchors.py, which holds the
    markdown for every case. A slug rule written from memory of how GitHub
    behaves is how this module used to get 15 of 21 ids wrong; this is the
    check that it no longer does.
    """

    @classmethod
    def setUpClass(cls):
        with open(FIXTURE, encoding='utf-8') as fh:
            cls.cases = json.load(fh)

    def test_the_fixture_is_not_empty(self):
        self.assertGreaterEqual(sum(len(c['headings']) for c in self.cases), 60)

    def test_heading_slugs_match_github_for_every_entry(self):
        for case in self.cases:
            with self.subTest(case=case['name']):
                self.assertEqual(heading_slugs(case['markdown']), case['headings'])

    def test_html_ids_match_github_for_every_entry(self):
        for case in self.cases:
            with self.subTest(case=case['name']):
                self.assertEqual(sorted(html_ids(case['markdown'])),
                                 sorted(case['ids']))


class TestSlugify(unittest.TestCase):
    def test_lowercases(self):
        self.assertEqual(slugify('Getting Started'), 'getting-started')

    def test_spaces_become_hyphens(self):
        self.assertEqual(slugify('a b c'), 'a-b-c')

    def test_drops_punctuation(self):
        self.assertEqual(slugify('What is it?'), 'what-is-it')
        self.assertEqual(slugify('Setup (advanced)'), 'setup-advanced')

    def test_keeps_existing_hyphens(self):
        self.assertEqual(slugify('well-known values'), 'well-known-values')

    def test_strips_inline_code_markers(self):
        self.assertEqual(slugify('the `--out` flag'), 'the---out-flag')

    def test_keeps_leading_and_trailing_hyphens(self):
        # GitHub trims nothing: ## `--offline` flag is #--offline-flag, and a
        # heading that starts with an emoji starts with a hyphen.
        self.assertEqual(slugify('--offline flag'), '--offline-flag')
        self.assertEqual(slugify('🚀 Launch'), '-launch')
        self.assertEqual(slugify('Trailing emoji 🎉'), 'trailing-emoji-')

    def test_lowercases_one_character_at_a_time(self):
        # str.lower() on the whole string applies the Greek final-sigma rule.
        # GitHub does not.
        self.assertEqual(slugify('ΣΑΣ'), 'σασ')

    def test_keeps_the_characters_github_keeps(self):
        self.assertEqual(slugify('हिन्दी Ⅻ a‿b'), 'हिन्दी-ⅻ-a‿b')
        self.assertEqual(slugify('A½ x²'), 'a-x')

    def test_keeps_underscores(self):
        self.assertEqual(slugify('snake_case name'), 'snake_case-name')


class TestHeadingSlugs(unittest.TestCase):
    def test_extracts_atx_headings(self):
        md = '# One\n\ntext\n\n## Two\n'
        self.assertEqual(heading_slugs(md), ['one', 'two'])

    def test_ignores_hashes_inside_fenced_code(self):
        md = '# Real\n\n```\n# Not a heading\n```\n\n## Also real\n'
        self.assertEqual(heading_slugs(md), ['real', 'also-real'])

    def test_ignores_tilde_fenced_code(self):
        md = '# Real\n\n~~~\n# Nope\n~~~\n'
        self.assertEqual(heading_slugs(md), ['real'])

    def test_duplicate_headings_get_numeric_suffixes(self):
        md = '# Setup\n## Setup\n### Setup\n'
        self.assertEqual(heading_slugs(md), ['setup', 'setup-1', 'setup-2'])

    def test_requires_space_after_hashes(self):
        md = '#NotAHeading\n# Yes\n'
        self.assertEqual(heading_slugs(md), ['yes'])

    def test_empty_document(self):
        self.assertEqual(heading_slugs(''), [])

    def test_literal_suffixed_heading_does_not_collide(self):
        # github-slugger registers emitted slugs, so a literal `Setup-1`
        # heading must not collide with the slug generated for a second
        # `Setup`.
        md = '# Setup\n## Setup\n### Setup-1\n'
        self.assertEqual(heading_slugs(md), ['setup', 'setup-1', 'setup-1-1'])

    def test_mismatched_fence_marker_does_not_close_the_block(self):
        # A tilde line inside a backtick block is content, not a closing fence.
        md = '```\n~~~\n# Not a heading\n```\n\n# Real\n'
        self.assertEqual(heading_slugs(md), ['real'])

    def test_shorter_closing_fence_does_not_close_the_block(self):
        md = '````\n```\n# Not a heading\n````\n\n# Real\n'
        self.assertEqual(heading_slugs(md), ['real'])

    def test_heading_indented_up_to_three_spaces(self):
        self.assertEqual(heading_slugs('   # Indented\n'), ['indented'])

    def test_heading_indented_four_spaces_is_a_code_block(self):
        # Four spaces makes it an indented code block, not a heading.
        self.assertEqual(heading_slugs('    # Not a heading\n'), [])

    def test_unclosed_fence_still_swallows_the_rest(self):
        self.assertEqual(heading_slugs('# Real\n\n```\n# Hidden\n'), ['real'])

    def test_slugs_the_rendered_text_not_the_markdown(self):
        self.assertEqual(heading_slugs('## See [the guide](guide.md)\n'),
                         ['see-the-guide'])

    def test_reads_setext_headings(self):
        self.assertEqual(heading_slugs('Setext heading\n===\n\nTwo\n---\n'),
                         ['setext-heading', 'two'])

    def test_a_rule_after_a_blank_line_is_not_a_heading(self):
        self.assertEqual(heading_slugs('Text\n\n---\n'), [])

    def test_front_matter_is_not_a_setext_heading(self):
        # GitHub's markdown API has no front matter support and renders this
        # as a heading, which is why it is not in the fixture. The file view,
        # which is what a link lands on, renders it as a table.
        md = '---\ntitle: x\nlayout: y\n---\n\n# Real\n'
        self.assertEqual(heading_slugs(md), ['real'])

    def test_a_heading_inside_a_comment_is_not_one(self):
        self.assertEqual(heading_slugs('<!--\n## Hidden\n-->\n\n## Shown\n'),
                         ['shown'])


class TestAnchorIds(unittest.TestCase):
    def test_html_anchors_are_collected(self):
        md = '<a id="custom"></a>\n\n<a name="legacy"></a>\n\n## Heading\n'
        self.assertEqual(sorted(anchor_ids(md)), ['custom', 'heading', 'legacy'])

    def test_html_written_as_code_is_not_an_anchor(self):
        self.assertEqual(html_ids('Write `<a id="x"></a>` to add one.\n'), [])


if __name__ == '__main__':
    unittest.main()
