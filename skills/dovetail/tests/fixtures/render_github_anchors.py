#!/usr/bin/env python3
"""Regenerate github_anchors.json from GitHub's own renderer.

Not a test, and never run by one: it needs the network and an authenticated
`gh`. It exists so the fixture can be rebuilt, and so anyone can see exactly
how every expected id was produced - by GitHub, not by dovetail.

    python3 render_github_anchors.py > github_anchors.json

Each case is rendered with `POST /markdown`. `headings` is every id GitHub
put on a heading permalink, in document order. `ids` is every other `id` or
`name` attribute it kept. Both lose the `user-content-` prefix GitHub adds,
which links leave out.

One thing this API gets wrong for files: it has no front matter support, so a
YAML block at the top renders as a setext heading. The file view renders it as
a table. That rule is tested on its own in test_slugify.py rather than here.
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys

CASES = [
    ('atx', '# Getting Started\n\n## What is it?\n\n## Setup (advanced)\n'),
    ('closing-hashes', '## Closing hashes ##\n\n## foo#\n\n## C++ and C#\n'),
    ('code-spans', '## The `--out` flag\n\n## `--offline` flag\n\n'
                   '## Double ``a ` b`` ticks\n'),
    ('leading-and-trailing-hyphens', '## - party time\n\n## ---\n\n'
                                     '## 🚀 Launch\n\n## Trailing emoji 🎉\n'),
    ('links', '## See [the guide](guide.md)\n\n## Link to [a ref][r]\n\n'
              '## [**Bold link**](x.md) here\n\n## Ref [shortcut] link\n\n'
              '## Missing [undefined][nope] ref\n\n'
              '## [`code` in a link](x.md)\n\n[r]: x.md\n[shortcut]: y.md\n'),
    ('images', '## ![logo](logo.png) Title\n\n## Badge ![b][r] after\n\n[r]: b.svg\n'),
    ('emphasis', '## **Bold** and _italic_ and *star*\n\n## ~~strike~~ text\n\n'
                 '## _leading emphasis_\n\n## a __b__ c\n'),
    ('underscores', '## snake_case_name and __init__\n\n## Mixed_Case_Underscore\n\n'
                    '## foo_bar_ and _baz\n\n## ___triple___ and __a_b__\n\n## x_\n'),
    ('escapes', '## Escaped \\*star\\* and \\_under\\_\n\n## Not \\`code\\`\n'),
    ('entities', '## Entity &amp; more\n\n## Quotes "double" and \'single\' and ’curly’\n\n'
                 '## Less &lt;h2&gt; than\n'),
    ('inline-html', '## Title with <code>tag</code>\n\n## Autolink <https://example.com>\n\n'
                    '## <a name="inline"></a> Anchored heading\n'),
    ('unicode', '## Café\n\n## Ünïcödé Straße\n\n## 日本語の見出し\n\n## हिन्दी\n\n'
                '## CJK punctuation：全角\n\n## Roman Ⅻ and ǅ and a‿b\n\n'
                '## İstanbul ΣΑΣ\n\n## A½ superscript²\n'),
    ('whitespace', '## Multiple   spaces\n\n## Tab\there\n\n'
                   '## No break and zero​width\n'),
    ('numbers', '## Numbers 1.2.3 and v2.0\n\n## 1. Numbered\n'),
    ('setext', 'Setext heading\n===\n\nSetext two\n---\n\nLine one\nline two\n---\n\n'
               '   Indented setext\n   ---\n\nText\n    ===\n\n- item\n---\n\n'
               '> Quote\n---\n'),
    ('containers', '> ## Quoted heading\n\n- ## List heading\n\n1. ## Ordered heading\n'),
    ('duplicates', '# Setup\n## Setup\n### Setup-1\n## Setup\n'),
    ('empty', '#\n\n## 🚀\n\n## After empty\n'),
    ('html-attributes', '<a id="custom"></a>\n\n<a name="legacy"></a>\n\n'
                        '<div id="d">x</div>\n\n<span id="s">y</span>\n\n'
                        '<p name="n">z</p>\n'),
    ('html-headings', '<h2 id="foo">Bar baz</h2>\n\n<h3>No id here</h3>\n\n## Bar baz\n\n'
                      '<h1 align="center">\n  <img src="logo.png"><br>\n  My Project\n</h1>\n\n'
                      '<h2 align="center">Centred <em>title</em></h2>\n\n'
                      '<div align="center">\n<h4>Inside div</h4>\n</div>\n'),
    ('not-headings', '```\n# Fenced\n```\n\n    # Indented code\n\n'
                     'Use `<h2>code</h2>` inline.\n\n    <h3>indented html</h3>\n\n'
                     '<!-- ## commented -->\n\n<!--\n## hidden\n-->\n\n'
                     '<div>\n## Inside an HTML block\n</div>\n\n#NoSpace\n\n'
                     '\tTabbed\n---\n\n## Real\n'),
]


def render(markdown: str) -> str:
    return subprocess.run(
        ['gh', 'api', '-X', 'POST', '/markdown', '-f', 'mode=markdown',
         '-F', 'text=@-'],
        input=markdown, capture_output=True, text=True, check=True).stdout


def main() -> int:
    out = []
    for name, markdown in CASES:
        body = render(markdown)
        headings = [html.unescape(m) for m in re.findall(
            r'<a id="user-content-([^"]*)" class="anchor"', body)]
        every = [html.unescape(m) for m in re.findall(
            r'\b(?:id|name)="user-content-([^"]*)"', body)]
        # Headings are removed once each, so an HTML heading's own `id` that
        # happens to equal its slug still counts as an attribute id.
        ids = list(every)
        for slug in headings:
            ids.remove(slug)
        out.append({'name': name, 'markdown': markdown,
                    'headings': headings, 'ids': ids})
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1)
    sys.stdout.write('\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
