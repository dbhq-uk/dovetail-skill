"""
External URLs, checked through lychee - only when asked, and only if it is installed.

The deterministic layer never touches the network, and by default dovetail
does not check a URL at all: `refgraph` skips every external link. This is
the one exception. `--external-links` runs lychee (https://lychee.cli.rs)
over the repository's markdown, reads its JSON report and turns each URL it
could not reach into a finding.

Those findings are heuristic and can never gate a build, and `[gate]` cannot
opt them in. A URL that fails today may answer tomorrow, and a site that
blocks bots is not drift in this repository. A gate that goes red on
somebody else's outage gets switched off.

lychee reads a `lychee.toml` in the repository root on its own, so a
repository keeps its excludes and accepted status codes there.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from store import make_finding

CHECK = 'external_links'
SOURCE = 'check:external'
MARKDOWN = ('.md', '.markdown')

# lychee's exit codes: 0 every link answered, 2 at least one did not. Any
# other code means lychee itself failed, and its report cannot be trusted.
_OK_EXITS = (0, 2)


class LycheeError(RuntimeError):
    """lychee ran but did not produce a report dovetail can read."""


def available() -> bool:
    return shutil.which('lychee') is not None


def markdown_files(inventory: dict) -> list[str]:
    return sorted(entry['path'] for entry in inventory['files']
                  if entry['path'].lower().endswith(MARKDOWN))


def run_lychee(repo_root: str, files: list[str]) -> dict:
    """lychee's JSON report for `files`, checking only http and https URLs.

    Paths go in on stdin rather than the command line, so a large repository
    cannot overflow ARG_MAX.
    """
    proc = subprocess.run(
        ['lychee', '--format', 'json', '--no-progress', '--include-fragments',
         '--scheme', 'https', '--scheme', 'http', '--files-from', '-'],
        cwd=repo_root, input='\n'.join(files) + '\n',
        capture_output=True, text=True, errors='replace', check=False)
    if proc.returncode not in _OK_EXITS:
        detail = (proc.stderr.strip().splitlines() or ['no output'])[-1]
        raise LycheeError(f'lychee exited {proc.returncode}: {detail}')
    try:
        report = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise LycheeError(f'lychee printed no JSON report: {exc}') from exc
    if not isinstance(report, dict):
        raise LycheeError('lychee printed a JSON report of an unknown shape')
    return report


def _findings_from(report: dict, known: set[str]) -> list[dict]:
    findings: list[dict] = []
    for key, timed_out in (('error_map', False), ('timeout_map', True)):
        for path, entries in sorted((report.get(key) or {}).items()):
            if path not in known:
                continue  # lychee only reports what it was given, but be sure
            for entry in entries or []:
                url = entry.get('url')
                if not isinstance(url, str) or not url:
                    continue
                line = (entry.get('span') or {}).get('line')
                line = line if isinstance(line, int) and line > 0 else 1
                status = entry.get('status') or {}
                reason = status.get('text') or 'failed'
                if timed_out:
                    problem = f'{path} links to {url}, which did not answer in time.'
                    severity = 'low'
                elif 'fragment' in reason.lower():
                    problem = f'{path} links to {url}, and that page has no such anchor.'
                    severity = 'low'
                else:
                    problem = f'{path} links to {url}, which failed: {reason}.'
                    severity = 'medium'
                findings.append(make_finding(
                    source=SOURCE,
                    category='broken_link',
                    problem=problem,
                    evidence=[{'file': path, 'line': line,
                               'quote': f'link target: {url}'}],
                    suggestion=f'Open {url}. Update or remove the link if it has moved '
                               'or gone, or exclude it in lychee.toml if it only '
                               'refuses automated requests.',
                    severity=severity,
                    claim=f'{path} -> {url}',
                ))
    return findings


def external_links(repo_root: str, inventory: dict) -> list[dict]:
    """Findings for every external URL in the markdown that lychee could not reach."""
    files = markdown_files(inventory)
    if not files:
        return []
    return _findings_from(run_lychee(repo_root, files), set(files))
