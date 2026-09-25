# Security

## Reporting a vulnerability

Email <dan@dbhq.uk> rather than opening a public issue. Include what you found,
how to reproduce it, and what an attacker could do with it. You will get a first
response within 48 hours.

## What this skill does

dovetail reads a repository and reports where it disagrees with itself. It has
two layers, and a triage loop that edits files when you approve a fix. Each has
different security properties.

### Network

**The exact layer makes no network requests.** Broken links, dangling anchors,
orphans, duplicates, flag and signature drift, conventions and git-history
signals are all computed locally, in Python, with no API key and no
third-party packages.

**The judgement layer sends repository content to a model.** Contradictions,
semantic staleness and spec drift are decided by reviewers, which read the
files under review:

- In an interactive run, each reviewer is a subagent of your coding agent, so
  the files go to whichever model provider that agent uses.
- In the scheduled CI job, each reviewer is a `claude -p` call made with the
  `CLAUDE_CODE_OAUTH_TOKEN` secret, so the files go to Anthropic.

If a repository contains material you cannot send to a model provider, run the
exact layer alone: `scan.py`, or ask for an "exact only" run. It produces every
broken link and duplicate without a model.

**The scheduled job also calls GitHub**, through `gh`, to keep one tracking
issue up to date.

### Code it runs

- **`.dovetail/checks/*.py` in the scanned repository runs on every scan.**
  These are repo-local checks. dovetail imports them and runs them as Python,
  with your permissions. So scanning a repository runs code from it,
  including code from a pull request's branch: the per-PR CI template scans
  the pull request's checkout, so a pull request can add or change a plugin
  and have it run in CI. Read a repository's `.dovetail/checks/` before you
  scan it, unless you trust it. Or pass `--no-plugins` to `scan.py` or to
  `dovetail.py scan`, which skips them and says so in its output.
- `git`, read-only: `rev-parse`, `ls-files`, `log`, `diff`, `blame` and `show`.
- Other Python interpreters on your `PATH`, when `python3` is older than 3.11.
  Each is run once to read its version, and dovetail then re-runs itself under
  the newest suitable one.
- `claude -p`, in the scheduled job only. Each reviewer may use the `Read`,
  `Glob` and `Grep` tools and nothing else.
- `gh issue` and `gh label`, in the scheduled job only.

It runs no other external commands.

### On disk

- Installs into `~/.claude/skills/dovetail` or `~/.codex/skills/dovetail`,
  depending on the agent
- Reads the repository you point it at
- **The scan never writes to the repository.** The only file it writes anywhere
  is `$GITHUB_STEP_SUMMARY`, and only when CI sets it
- **The triage loop writes to the repository, and only what you approve.** Each
  fix is applied on its own, after you choose it. Marking a finding intentional
  appends one line to `.dovetail/decisions.jsonl`. Before each fix, dovetail
  compares a content hash of every file with a snapshot, and stops the run if
  anything changed that it did not write. Outside a git repository it refuses
  to write at all, because there is no undo without git
- An interactive run keeps its state in `~/.dbhq/dovetail/runs/`, readable by
  you only: the scan result, the file hashes, and each reviewer's prompt and
  output

### Credentials

The skill reads no credential store. The scheduled job uses two secrets that
you configure in its workflow: `CLAUDE_CODE_OAUTH_TOKEN` for the reviewers, and
the workflow's own `GITHUB_TOKEN` for the tracking issue.

## Requirements

Python 3.11 or newer and `git`. No API key and no third-party packages for the
exact layer. The judgement layer needs a model.
