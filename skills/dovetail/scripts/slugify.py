#!/usr/bin/env python3
"""
GitHub-compatible anchor ids.

GitHub gives every heading an id made from its *rendered* text: lowercase it,
drop every character that is not a letter, mark, digit, `_`, hyphen or space,
then turn each space into a hyphen. Nothing is trimmed, so a heading of
``## `--offline` flag`` is `#--offline-flag`, and one that starts with an emoji
starts with a hyphen. Repeated ids get `-1`, `-2` and so on in document order.

"Rendered" is the part that is easy to get wrong. Link text stays and its URL
goes, code and emphasis lose their markers, an image adds nothing, and HTML
tags vanish. HTML headings (`<h2>`) get an id the same way, and an `id` or
`name` attribute on any HTML element is an anchor too.

Every rule here is checked against ids GitHub itself rendered:
`tests/fixtures/github_anchors.json`, and the script beside it that made it.

Fenced and indented code, HTML comments and front matter are skipped, so a
`# comment` inside an example is not mistaken for a heading.
"""

from __future__ import annotations

import bisect
import html
import re
import unicodedata

_FENCE = re.compile(r'^ {0,3}((?:`{3,})|(?:~{3,}))')
_ATX = re.compile(r'^ {0,3}(#{1,6})(?:[ \t]+(.*?))?[ \t]*$')
_ATX_CLOSE = re.compile(r'(?:^|[ \t]+)#+[ \t]*$')
_SETEXT = re.compile(r'^ {0,3}(?:=+|-+)[ \t]*$')
_THEMATIC = re.compile(r'^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$')
_QUOTE = re.compile(r'^ {0,3}>[ \t]?')
_LIST_ITEM = re.compile(r'^ {0,3}(?:[-+*]|\d{1,9}[.)])[ \t]+')
_HTML_BLOCK = re.compile(r'^ {0,3}</?[A-Za-z][A-Za-z0-9-]*(?:[\s/>]|$)')
_REFDEF = re.compile(r'^ {0,3}\[([^\]]+)\]:[ \t]*\S')
_COMMENT = re.compile(r'<!--.*?(?:-->|\Z)', re.S)
_HTML_HEADING = re.compile(r'<h([1-6])(?:\s[^>]*)?>(.*?)</h\1\s*>', re.I | re.S)
_TAG = re.compile(r'<[A-Za-z][^<>]*>|</[A-Za-z][^<>]*>')
_TAG_ATTRS = re.compile(r'<[A-Za-z][A-Za-z0-9-]*(\s[^<>]*)>')
_ID_ATTR = re.compile(
    r'(?:^|\s)(?:id|name)\s*=\s*(?:"([^"]*)"|\'([^\']*)\'|([^\s"\'=<>`]+))', re.I)
_AUTOLINK = re.compile(
    r'<([A-Za-z][A-Za-z0-9+.-]{1,31}:[^\s<>]*'
    r'|[A-Za-z0-9.!#$%&\'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*)>')
_IMAGE = re.compile(r'!\[[^\]]*\](?:\((?:[^()]|\([^()]*\))*\)|\[[^\]]*\])?')
_INLINE_LINK = re.compile(r'\[([^\]]*)\]\((?:[^()]|\([^()]*\))*\)')
_REF_LINK = re.compile(r'\[([^\]]*)\]\[([^\]]*)\]')
_CODE_SPAN = re.compile(r'(`+)(.+?)(?<!`)\1(?!`)')
_ASCII_PUNCT = frozenset('!"#$%&\'()*+,-./:;<=>?@[\\]^_`{|}~')

# What survives into an id, besides hyphen and space. GitHub keeps Ruby's
# \p{Word}: letters, marks, decimal digits, letter numbers (Roman numerals)
# and connector punctuation such as `_`. Python's \w is not the same class -
# it keeps `½` and `²`, which GitHub drops, and drops the combining marks in
# Devanagari, which GitHub keeps.
_KEEP = frozenset({'Lu', 'Ll', 'Lt', 'Lm', 'Lo', 'Nl', 'Mn', 'Mc', 'Me', 'Nd', 'Pc'})

# Private-use code points stand in for text that inline parsing must not touch
# (code spans, escaped characters) and are swapped back afterwards.
_MASK_BASE = 0xE000


def fence_delimiter(line: str) -> str | None:
    """The fence marker opening or closing a code block on this line, or None.

    CommonMark: up to three leading spaces, and a closing fence must use the
    same character and be at least as long as the opener.
    """
    match = _FENCE.match(line)
    return match.group(1) if match else None


def track_fence(fence: str | None, line: str) -> tuple[str | None, bool]:
    """Update fence state for one line.

    `fence` is the currently-open delimiter (or None if not inside a fence).
    Returns `(new_fence, is_fence_line)`: `is_fence_line` is True when this
    line is itself a fence marker (opening or closing) and should be skipped
    rather than scanned for content. This is the single implementation of the
    fence state machine - both `heading_slugs` and `refgraph` use it, so a
    fix here (or a bug) cannot diverge between the two.
    """
    marker = fence_delimiter(line)
    if marker is None:
        return fence, False
    if fence is None:
        return marker, True
    if marker[0] == fence[0] and len(marker) >= len(fence):
        return None, True
    return fence, True


def slugify(text: str) -> str:
    """Convert a heading's rendered text to its GitHub anchor id.

    Lowercased a character at a time, as GitHub does: Python's str.lower()
    applies the Greek final-sigma rule and GitHub does not.
    """
    out = []
    for char in text:
        for low in char.lower():
            if low in '- ' or unicodedata.category(low) in _KEEP:
                out.append(low)
    return ''.join(out).replace(' ', '-')


def _indent(line: str) -> int:
    width = 0
    for char in line:
        if char == ' ':
            width += 1
        elif char == '\t':
            width += 4 - width % 4
        else:
            break
    return width


def _norm_label(label: str) -> str:
    return ' '.join(label.split()).casefold()


def _code_lines(lines: list[str]) -> list[bool]:
    """Which lines are code: inside a fence, a fence marker, or indented code."""
    code = [False] * len(lines)
    fence: str | None = None
    in_paragraph = False
    for index, line in enumerate(lines):
        fence, is_fence_line = track_fence(fence, line)
        if is_fence_line or fence is not None:
            code[index] = True
            in_paragraph = False
            continue
        if not line.strip():
            in_paragraph = False
            continue
        if _indent(line) >= 4 and not in_paragraph:
            code[index] = True
            continue
        in_paragraph = True
    return code


# The two copies `_prepare` returns: code blanked, and inline code blanked too.
_Prepared = tuple[list[str], list[str]]


def _prepare(markdown: str) -> _Prepared:
    """The document's lines with code, comments and front matter blanked.

    Returns two copies. The first keeps inline code, for rendering heading
    text. The second also blanks inline code, for finding HTML, so that
    `<h2>` written as code is not read as a heading. Blanking rather than
    deleting keeps every character where it was, so positions still order
    headings correctly.
    """
    lines = markdown.split('\n')

    # Front matter. GitHub's file view renders it as a table, not a heading,
    # although its markdown API (which knows nothing of front matter) would
    # read the closing `---` as a setext underline.
    if lines and lines[0].rstrip() == '---':
        for end in range(1, len(lines)):
            if lines[end].rstrip() in ('---', '...'):
                lines[:end + 1] = [''] * (end + 1)
                break

    code = _code_lines(lines)
    plain = '\n'.join('' if is_code else line for line, is_code in zip(lines, code))
    masked = '\n'.join('' if is_code else _mask_code_spans(line)
                       for line, is_code in zip(lines, code))
    # Comments are found in the masked copy, so `<!--` written as code does
    # not open one, then blanked in both. Masking keeps lengths, so the
    # offsets agree.
    for match in reversed(list(_COMMENT.finditer(masked))):
        start, end = match.span()
        blank = re.sub(r'[^\n]', ' ', match.group(0))
        plain = plain[:start] + blank + plain[end:]
        masked = masked[:start] + blank + masked[end:]
    return plain.split('\n'), masked.split('\n')


def _mask_code_spans(line: str) -> str:
    """Replace inline code with spaces, so HTML written as code is not HTML."""
    return _CODE_SPAN.sub(lambda m: ' ' * len(m.group(0)), line)


def _strip_underscore_emphasis(text: str) -> str:
    """Drop `_` runs that CommonMark reads as emphasis, keep the rest.

    `*` and `~` markers are punctuation and fall out of the slug anyway, but
    `_` is a word character and stays - so `__init__` (bold "init") and
    `snake_case` (literal) differ only by CommonMark's flanking rules.
    """
    def kind(char: str | None) -> str:
        if char is None or char.isspace():
            return 'space'
        if unicodedata.category(char)[0] in 'PS':
            return 'punct'
        return 'other'

    remove: set[int] = set()
    openers: list[tuple[int, int]] = []
    for match in re.finditer(r'_+', text):
        start, end = match.span()
        before = kind(text[start - 1] if start else None)
        after = kind(text[end] if end < len(text) else None)
        left = after != 'space' and (after != 'punct' or before != 'other')
        right = before != 'space' and (before != 'punct' or after != 'other')
        can_open = left and (not right or before == 'punct')
        can_close = right and (not left or after == 'punct')
        if can_close and openers:
            open_start, open_end = openers.pop()
            remove.update(range(open_start, open_end))
            remove.update(range(start, end))
        elif can_open:
            openers.append((start, end))
    return ''.join(c for i, c in enumerate(text) if i not in remove)


def render_inline(text: str, refs: frozenset[str] = frozenset()) -> str:
    """The text GitHub renders for a heading's markdown, markup removed.

    `refs` holds the document's link reference labels: `[a][r]` is a link
    reading "a" only when `r` is defined, and literal text otherwise.
    """
    kept: list[str] = []

    def mask(value: str) -> str:
        kept.append(value)
        return chr(_MASK_BASE + len(kept) - 1)

    out: list[str] = []
    i = 0
    while i < len(text):
        char = text[i]
        if char == '\\' and i + 1 < len(text) and text[i + 1] in _ASCII_PUNCT:
            out.append(mask(text[i + 1]))
            i += 2
            continue
        if char == '`':
            run = len(text[i:]) - len(text[i:].lstrip('`'))
            match = re.compile(r'(?<!`)' + '`' * run + r'(?!`)').search(text, i + run)
            if match:
                content = text[i + run:match.start()].replace('\n', ' ')
                if (len(content) > 1 and content[0] == ' ' == content[-1]
                        and content.strip(' ')):
                    content = content[1:-1]
                out.append(mask(content))
                i = match.end()
            else:
                out.append(mask('`' * run))
                i += run
            continue
        out.append(char)
        i += 1
    rendered = ''.join(out)

    rendered = _AUTOLINK.sub(lambda m: mask(m.group(1)), rendered)
    rendered = _TAG.sub('', rendered)
    rendered = _IMAGE.sub('', rendered)
    rendered = _INLINE_LINK.sub(lambda m: m.group(1), rendered)

    def ref_link(match: re.Match) -> str:
        label = match.group(2) or match.group(1)
        return match.group(1) if _norm_label(label) in refs else match.group(0)

    rendered = _REF_LINK.sub(ref_link, rendered)
    rendered = _strip_underscore_emphasis(rendered)
    rendered = html.unescape(rendered)
    return ''.join(
        kept[ord(c) - _MASK_BASE] if _MASK_BASE <= ord(c) < _MASK_BASE + len(kept) else c
        for c in rendered)


def _headings(markdown: str, prepared: _Prepared | None = None) -> list[str]:
    """Rendered text of every heading GitHub gives an id, in document order."""
    lines, masked = prepared if prepared is not None else _prepare(markdown)
    refs = frozenset(_norm_label(m.group(1)) for line in lines
                     if (m := _REFDEF.match(line)))

    found: list[tuple[int, int, str]] = []   # (line, column, rendered text)
    paragraph: list[str] = []
    in_html = False
    for index, line in enumerate(lines):
        if not line.strip():
            paragraph, in_html = [], False
            continue
        if in_html:
            continue

        body = line
        contained = False
        while True:
            match = _QUOTE.match(body) or _LIST_ITEM.match(body)
            if not match:
                break
            body, contained = body[match.end():], True

        atx = _ATX.match(body)
        if atx:
            content = _ATX_CLOSE.sub('', atx.group(2) or '').strip()
            found.append((index, 0, render_inline(content, refs)))
            paragraph = []
            continue
        if contained:
            # Setext headings inside quotes and list items are rare enough
            # that a paragraph there is simply not one to underline.
            paragraph = []
            continue
        if paragraph and _SETEXT.match(line) and _indent(line) < 4:
            content = '\n'.join(paragraph).strip()
            found.append((index, 0, render_inline(content, refs)))
            paragraph = []
            continue
        if _THEMATIC.match(line) or line.lstrip().startswith('|'):
            paragraph = []
            continue
        if not paragraph and _HTML_BLOCK.match(line):
            in_html = True   # raw HTML until the next blank line
            continue
        if not paragraph and _REFDEF.match(line):
            continue
        paragraph.append(line.strip())

    text = '\n'.join(masked)
    starts = [0]
    for line in masked:
        starts.append(starts[-1] + len(line) + 1)
    for match in _HTML_HEADING.finditer(text):
        row = bisect.bisect_right(starts, match.start()) - 1
        inner = html.unescape(_TAG.sub('', match.group(2)))
        found.append((row, match.start() - starts[row], inner))

    found.sort(key=lambda item: (item[0], item[1]))
    return [rendered for _, _, rendered in found]


def heading_slugs(markdown: str, prepared: _Prepared | None = None) -> list[str]:
    """Ids GitHub gives the document's headings, in order, duplicates suffixed."""
    slugs: list[str] = []
    occurrences: dict[str, int] = {}
    for rendered in _headings(markdown, prepared):
        base = slugify(rendered)
        if not base:
            continue   # GitHub gives a heading with an empty slug no id
        # github-slugger's algorithm: every emitted slug is registered, and a
        # collision bumps the counter on the *base* until the result is unused.
        # This is what stops a literal `Setup-1` heading colliding with the
        # `setup-1` generated for a second `Setup`.
        result = base
        while result in occurrences:
            occurrences[base] = occurrences.get(base, 0) + 1
            result = f'{base}-{occurrences[base]}'
        occurrences[result] = 0
        slugs.append(result)
    return slugs


def html_ids(markdown: str, prepared: _Prepared | None = None) -> list[str]:
    """`id` and `name` attributes on HTML elements, which GitHub keeps as anchors."""
    masked = (prepared if prepared is not None else _prepare(markdown))[1]
    found: list[str] = []
    for tag in _TAG_ATTRS.finditer('\n'.join(masked)):
        for match in _ID_ATTR.finditer(tag.group(1)):
            value = next(g for g in match.groups() if g is not None)
            if value:
                found.append(html.unescape(value))
    return found


def anchor_ids(markdown: str) -> list[str]:
    """Every fragment a link into this document can land on."""
    # Both halves read the same prepared copy, and preparing it is most of
    # their cost, so it is done once. Neither changes it.
    prepared = _prepare(markdown)
    return heading_slugs(markdown, prepared) + html_ids(markdown, prepared)
