#!/usr/bin/env python3
"""
Classify a repository file by modality and category.

modality: how the bytes should be read — text, vector_diagram, raster_image,
          or binary.
category: what role the file plays — code, doc, spec, visual, flow, icon,
          config, or other.

Two rules exist because their absence is a real bug:
  * the icon-by-name rule applies only to images, so a text file called
    `visual_icon.md` is a doc, not an icon;
  * `spec` is decided by a whole path segment, so `client.spec.ts` is code
    while `spec/api.md` is a specification.
"""

from __future__ import annotations

import posixpath
import re

RASTER = {'.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp', '.tiff', '.heic', '.ico', '.icns'}
VECTOR = {'.svg', '.mmd', '.dot', '.puml', '.plantuml'}
CODE = {
    '.ts', '.tsx', '.mts', '.cts', '.js', '.jsx', '.mjs', '.cjs', '.swift',
    '.py', '.go', '.rs', '.java', '.kt', '.rb', '.c', '.h', '.cpp', '.m', '.sh',
    # Component-file formats. Omitting these classified 34 of 47 JavaScript-family
    # files in a real repository as 'other', so the code reviewer never saw a
    # single Astro component - the whole of that site's source was invisible to
    # it. A component file is code by any useful definition.
    '.astro', '.vue', '.svelte',
}
DOC = {'.md', '.markdown', '.txt', '.rst', '.adoc'}
CONFIG = {'.yml', '.yaml', '.json', '.toml', '.plist', '.xml'}

NUL_SNIFF_BYTES = 8000
FLOW_NAME = re.compile(r'(?:^|[-_])flow(?:[-_.]|$)')


def _has_nul(content: bytes) -> bool:
    return b'\x00' in content[:NUL_SNIFF_BYTES]


def classify(path: str, content: bytes) -> tuple[str, str]:
    """Return (modality, category) for a repo-relative path and its bytes."""
    lower = path.lower()
    name = posixpath.basename(lower)
    dot = name.rfind('.')
    ext = name[dot:] if dot > 0 else ''

    if ext in RASTER:
        modality = 'raster_image'
    elif ext in VECTOR:
        modality = 'vector_diagram'
    elif _has_nul(content):
        modality = 'binary'
    else:
        modality = 'text'

    segments = lower.split('/')
    is_spec_path = any(s in ('spec', 'specs') for s in segments)
    is_image = ext in RASTER or ext == '.svg'

    if ext in ('.icns', '.ico') or (is_image and 'icon' in name):
        category = 'icon'
    elif modality == 'raster_image':
        category = 'visual'
    elif is_spec_path:
        category = 'spec'
    elif FLOW_NAME.search(name):
        category = 'flow'
    elif ext == '.svg':
        category = 'visual'
    elif modality == 'vector_diagram':
        category = 'flow'
    elif ext in CODE:
        category = 'code'
    elif ext in DOC:
        category = 'doc'
    elif ext in CONFIG:
        category = 'config'
    else:
        category = 'other'

    return modality, category
