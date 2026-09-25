#!/usr/bin/env python3
"""
Each file's text, read once per scan and shared by every check.

Every check that needed a file's text used to open it itself, so one scan read
each file about eight times. `discover` already reads every file to hash it,
so it hands the text of each text file to this cache, and every check asks the
cache instead of the disk. A file the inventory did not cover is read on first
use and kept.

The cache lives in the inventory under `_text`, so it lasts exactly as long
as one scan. A rescan builds a new inventory and so reads the tree afresh.

Two ways of reading were in use, and both are kept exactly:

- strict: `open(path, encoding='utf-8')`. A file that is not valid UTF-8
  gives None.
- lenient (`strict=False`): the same with `errors='replace'`.

Both translate line endings as text-mode `open` does, so a CRLF file reads
the same from the cache as it did from the disk.
"""

from __future__ import annotations

import os

CACHE_KEY = '_text'


def decode(content: bytes) -> tuple[str, bool]:
    """(text, valid): the lenient decoding, and whether strict would succeed."""
    try:
        text, valid = content.decode('utf-8'), True
    except UnicodeDecodeError:
        text, valid = content.decode('utf-8', errors='replace'), False
    if '\r' in text:  # universal newlines, as text-mode open() gives
        text = text.replace('\r\n', '\n').replace('\r', '\n')
    return text, valid


def remember(inventory: dict, path: str, content: bytes) -> None:
    """Store a file's text from bytes already read, so no check reads it again."""
    inventory.setdefault(CACHE_KEY, {})[path] = decode(content)


def read(inventory: dict, path: str, *, strict: bool = True) -> str | None:
    """A file's text, from the cache when it is there and from disk once when not.

    None when the file cannot be read, or when `strict` and it is not UTF-8.
    """
    cache = inventory.setdefault(CACHE_KEY, {})
    entry = cache.get(path)
    if entry is None:
        try:
            with open(os.path.join(inventory['repo_root'], path), 'rb') as fh:
                entry = decode(fh.read())
        except OSError:
            entry = (None, False)
        cache[path] = entry
    text, valid = entry
    if text is None or (strict and not valid):
        return None
    return text
