# dovetail

Checks whether a repository agrees with itself.

`dovetail` builds an inventory and a typed reference graph of a repository, then
reports findings that are certain: broken links, dangling heading anchors,
orphaned files, duplicate and near-duplicate content, unreferenced assets, and
translated documents that have fallen behind their base.

Everything in this layer is deterministic. There are no model calls, no network
access, and no third-party dependencies — only the Python 3.11+ standard library
and `git`.

## Usage

```bash
python3 ~/.claude/skills/dovetail/scripts/scan.py /path/to/repo --format json
```

Or ask in any session: *"run dovetail on this repo"*.

## Options

| Flag | Meaning |
|---|---|
| `--format json\|github` | JSON to stdout, or GitHub workflow annotations. |
| `--since <ref>` | Only report findings touching files changed since `<ref>`. |
| `--fail-on none\|low\|medium\|high` | Exit non-zero when a finding at or above this severity exists. |

## Suppressing a finding

Append a line to `.dovetail/decisions.jsonl` in the target repository:

```jsonl
{"id":"sha256:...","verdict":"intentional","reason":"why","at":"2026-07-28","summary":"human-readable echo"}
```

The file is committed, so a decision made once applies to everyone and to CI.

## Requirements

Python 3.11+, git. No credentials.
