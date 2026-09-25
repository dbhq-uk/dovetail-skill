# dovetail

Checks whether a repository agrees with itself.

`dovetail` builds an inventory and a typed reference graph of a repository, then
reports where it has stopped agreeing with itself. Findings come from two layers.

The **exact layer** is `scan.py`, documented here. It runs seventeen checks:
broken links, dangling heading anchors, orphaned files, duplicate and
near-duplicate content, translations that have fallen behind, flag and
signature drift, conventions and git-history signals among them. Each finding
is **proven**, following from the structure alone, or **heuristic**, a likely
problem that intent can explain. Only proven findings fail `--fail-on` unless
the config opts a heuristic check in. It makes no
model calls and no network requests, and needs only the Python 3.11+ standard
library and `git`. The scan reads the repository and never writes to it.

Only links inside the repository are checked: external URLs are skipped unless
you pass `--external-links`, which runs lychee if it is installed. Links are
read from `.md` and `.markdown` files, so `.mdx` pages are not checked.

The **judgement layer** sends files to model reviewers, for contradictions and
documentation the code no longer matches. Then the triage loop in `SKILL.md`
walks through the findings, and edits the repository only when you approve a
fix. See `SECURITY.md` at the repository root for what each part runs and
sends.

## Usage

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/scan.py /path/to/repo --format json
```

Or ask in any session: *"run dovetail on this repo"*.

## Options

| Flag | Meaning |
|---|---|
| `--format json\|github` | JSON to stdout, or GitHub workflow annotations. |
| `--since <ref>` | Only report findings touching files changed since `<ref>`, including the target of a broken link. |
| `--fail-on none\|low\|medium\|high` | Exit non-zero when a proven finding at or above this severity exists. Heuristic findings count only for checks `[gate]` in the config names. |
| `--ignore <glob>` | Exclude a glob. Repeatable. |
| `--no-plugins` | Skip `.dovetail/checks/*.py`, which is code from the scanned repository. |
| `--external-links` | Also check external URLs with lychee, which must be on `PATH`. Uses the network, and never gates. |

## Suppressing a finding

Append a line to `.dovetail/decisions.jsonl` in the target repository:

```jsonl
{"id":"sha256:...","verdict":"intentional","reason":"why","at":"2026-07-28","summary":"human-readable echo"}
```

The file is committed, so a decision made once applies to everyone and to CI.

## Requirements

Python 3.11+ and `git`. No credentials, no packages and no network for the exact layer. The judgement layer needs a model.
