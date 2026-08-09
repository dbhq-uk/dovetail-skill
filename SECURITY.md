# Security

## Reporting a vulnerability

Email <dan@dbhq.uk> rather than opening a public issue. Include what you found,
how to reproduce it, and what an attacker could do with it. You will get a first
response within 48 hours.

## What this skill does

Dovetail reads a repository and reports where it contradicts itself. It has two
layers, and they have different security properties.

### Network

**The deterministic layer makes no network requests.** Broken links, dangling
anchors, orphans, duplicates, flag and signature drift, conventions and
git-history signals are all computed locally, in Python, with no API key and no
third-party packages.

**The judgement layer sends repository content to a model.** Contradictions,
semantic staleness and spec drift are decided by reviewers, which means the
files under review are sent to whichever model your agent is configured to use.
If a repository contains material you cannot send to a model provider, run the
deterministic layer alone - it produces every broken link and duplicate without
a model.

### On disk

- Installs into `~/.claude/skills/dovetail` or `~/.codex`, depending on the agent
- Reads the repository you point it at
- **Never modifies the target repository.** Fixes are printed as diffs for you
  to apply, not written

### Subprocesses

The skill shells out to `git` for history signals - `git log` and
`git blame --line-porcelain`, both read-only, both with timeouts. It runs no
other external commands.

### Credentials

None. The skill has no concept of an account and reads no credential store.

## Requirements

Python 3.11 or newer and `git`. No API key and no third-party packages for the
deterministic layer.
