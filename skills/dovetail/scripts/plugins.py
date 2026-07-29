#!/usr/bin/env python3
"""
`.dovetail/checks/*.py` - repo-local checks.

The canonical case is a rule a repository states about itself in prose and
enforces nowhere: "these three README tables must stay in sync", "every skill
listed in the installer needs a dependency arm". Written as a plugin the rule
becomes exact and free. Left in a prompt, it is something a model might notice.

A plugin is a module exposing `check(inventory, graph) -> list[finding]`. It
runs in-process for the same reason everything else here is stdlib-only: no
subprocess, no dependency, no ceremony.

Plugins are *repo-local code being executed*, which is worth being explicit
about: dovetail is already running as your user against your checkout, and a
plugin is no more privileged than the scan itself. What it must never do is
take down a run, so a plugin that raises is caught, named and skipped.
"""

from __future__ import annotations

import importlib.util
import os
import traceback

CHECKS_REL = os.path.join('.dovetail', 'checks')

REQUIRED_KEYS = {'id', 'source', 'category', 'problem', 'evidence',
                 'suggestion', 'severity'}


class PluginResult:
    """What one plugin produced, including how it failed if it did."""

    def __init__(self, name: str, findings: list[dict], error: str | None = None):
        self.name = name
        self.findings = findings
        self.error = error


def _validate(name: str, findings: object) -> list[dict]:
    """Reject anything that is not a list of well-formed findings.

    A plugin returning junk must fail as *that plugin* rather than corrupt the
    run - a malformed finding downstream would crash rendering or, worse, be
    reported as a real defect in the user's repository.
    """
    if not isinstance(findings, list):
        raise TypeError(f'check() returned {type(findings).__name__}, expected list')
    out: list[dict] = []
    for index, finding in enumerate(findings):
        if not isinstance(finding, dict):
            raise TypeError(f'finding {index} is {type(finding).__name__}, expected dict')
        missing = REQUIRED_KEYS - finding.keys()
        if missing:
            raise ValueError(f'finding {index} is missing {", ".join(sorted(missing))}')
        if not isinstance(finding['evidence'], list) or not finding['evidence']:
            raise ValueError(f'finding {index} has no evidence')
        for item in finding['evidence']:
            if not isinstance(item, dict) or 'file' not in item or 'line' not in item:
                raise ValueError(f'finding {index} has malformed evidence')
        if finding['severity'] not in ('high', 'medium', 'low'):
            raise ValueError(f'finding {index} has severity {finding["severity"]!r}')
        finding.setdefault('fix', {'kind': 'none'})
        finding.setdefault('blast_radius', [])
        finding.setdefault('confidence', 'high')
        finding.setdefault('ssot_direction', 'n/a')
        out.append(finding)
    return out


def discover_plugins(repo_root: str) -> list[str]:
    """Absolute paths of repo-local check modules, in a stable order."""
    directory = os.path.join(repo_root, CHECKS_REL)
    if not os.path.isdir(directory):
        return []
    return [
        os.path.join(directory, name)
        for name in sorted(os.listdir(directory))
        if name.endswith('.py') and not name.startswith('_')
    ]


def run_plugins(repo_root: str, inventory: dict, graph: dict) -> list[PluginResult]:
    """Run every repo-local check, isolating failures to the plugin that caused them."""
    results: list[PluginResult] = []
    for path in discover_plugins(repo_root):
        name = os.path.splitext(os.path.basename(path))[0]
        try:
            spec = importlib.util.spec_from_file_location(f'dovetail_plugin_{name}', path)
            if spec is None or spec.loader is None:
                raise ImportError('could not be loaded as a module')
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            entry = getattr(module, 'check', None)
            if entry is None:
                raise AttributeError('defines no check(inventory, graph)')
            findings = _validate(name, entry(inventory, graph))
        except Exception as exc:  # a user plugin must not take down the run
            detail = traceback.format_exception_only(type(exc), exc)[-1].strip()
            results.append(PluginResult(name, [], error=detail))
            continue
        for finding in findings:
            finding['source'] = f'plugin:{name}'
        results.append(PluginResult(name, findings))
    return results
