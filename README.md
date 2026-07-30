<div align="center">

<img src="assets/logo.svg" alt="dovetail skill for Claude Code, by DBHQ" width="420">

# dovetail

**Does your repository still agree with itself?**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Plugin-blueviolet)](https://code.claude.com/docs/en/plugins)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20WSL-lightgrey)]()

A free, open-source tool by [DBHQ](https://dbhq.uk)

</div>

---

A dovetail is the joint where two pieces interlock so precisely they cannot pull apart - and
*"does that dovetail?"* is already the English idiom for *"do those two things agree?"*

dovetail builds an inventory and a typed reference graph of a repository, then reports where it
has stopped agreeing with itself - and walks you through fixing it, one finding at a time.

Findings come from two layers, and you always know which you are looking at.

**Exact**, computed in Python in seconds with no model and no network: links that resolve to
nothing, heading anchors that no longer exist, files nothing points at, duplicated and
near-duplicated content, translations that have fallen behind, flags a doc claims that a script
does not declare, documented calls the real signature would reject, code blocks that do not
parse, manifests that disagree about the version, dead code, conventions the repo states but
does not follow, TODOs that have been there for a year, and pairs of files that changed
together for months and have just stopped.

**Judged**, from reviewers that only ever see what Python could not settle: contradictions
between documents, documentation that describes behaviour the code no longer has, diagrams that
no longer match the implementation, and duplicated logic where one copy was fixed and the other
was not.

## What makes it different

**The exact layer is deterministic, and only deterministic.** No model calls, no API key, no
network, no third-party packages. Seventeen checks, every one following from the structure of
the repository, so there is nothing to triage and nothing to second-guess.

**Which means you can fail a build on it.** A checker that produces false positives gets
switched off within a week - the triage costs more than the drift. Exact findings are certain
enough to gate a pull request, and the scan takes seconds, so it costs you nothing to run on
every one. Judged findings never gate a build, in either CI job.

**Nothing reaches a model that Python can compute exactly.** Every rubric names the categories
it must not report, because a reviewer restating a check Python already did is offering a guess
in place of a certainty.

**Reviewers are given work they can finish.** Handing a reviewer a whole repository and one
turn budget does not get the repository reviewed - it gets a few files read and the rest
silently skipped, which looks exactly like thoroughness. Work is sharded into small batches,
dispatched in parallel and deduped on a content fingerprint.

**Fabricated evidence is rejected.** Every quote a reviewer returns is checked against the
actual line in the file. A model inventing a plausible quote at a plausible line is the most
damaging failure available, because the finding reads exactly like a true one.

**It never edits without asking.** The scan itself has no write path at all. Fixes happen only
in the triage loop, only one at a time, and only on your say-so - and dovetail checks `git
status` between edits, so if anything changes that it did not apply, the run stops.

**It does not guess which side is right.** *What disagrees* is decidable. *Which side is
correct* usually is not: a broken link might mean the link is wrong or the target was deleted
by mistake, and nothing in the file tree tells them apart. So a contradiction ends in a
question rather than an edit.

**`--since` makes it adoptable.** A repository with existing drift cannot turn on a whole-repo
check without every unrelated pull request going red, so nobody turns it on at all. `--since`
scopes findings to the files a change actually touches, so you are only held to what you
changed.

## Install

### As a Claude Code plugin (recommended)

```
/plugin marketplace add dbhq-uk/marketplace
/plugin install dovetail@dbhq
```

### Any agent (Cursor, Copilot, Windsurf, Gemini, Cline and more)

```bash
npx skills add dbhq-uk/dovetail-skill
```

The [skills.sh](https://skills.sh) CLI installs into whichever agent directories it finds, so
this works outside Claude Code and Codex too.

### Local install (Claude Code or Codex)

```bash
git clone https://github.com/dbhq-uk/dovetail-skill.git
cd dovetail-skill
./install.sh          # Claude Code: symlinks into ~/.claude/skills (edits are live)
./install-codex.sh    # Codex: installs into ~/.codex/skills
```

**Nothing to install beyond that.** Python 3.11+ and `git`; no virtualenv, no packages, no
credentials.

## Usage

Ask in any session: *"run dovetail on this repo"*. It scans, shows you what is certain first,
and starts working through it while the judgement reviewers are still running.

Say *"run dovetail cheap"* to drop every reviewer a tier, or *"thorough"* to put them all on
the strongest model. *"quick"* skips the reviewers entirely and gives you the exact findings
only, for free.

Each finding arrives as a question box: apply the fix, skip it, mark it intentional, or batch
the whole class in one confirmation. Where the evidence names a winner, one option is marked
**(Recommended)** and says what makes it the answer. Where it does not, nothing is marked and
you get an open question - a tool that recommends on every finding teaches you to stop reading.

Or drive the scan directly:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/scan.py /path/to/repo --format json
```

| Flag | Meaning |
|---|---|
| `--format json\|github` | JSON to stdout, or GitHub workflow annotations |
| `--since <ref>` | Only report findings touching files changed since `<ref>` |
| `--fail-on none\|low\|medium\|high` | Exit non-zero when a finding at or above this severity exists |
| `--ignore <glob>` | Exclude a glob; repeatable |

The whole tutorial is [docs/getting-started.md](docs/getting-started.md).

## In CI

Two templates in [`skills/dovetail/ci/`](skills/dovetail/ci/), because the two jobs want
opposite things. [`dovetail-pr.yml`](skills/dovetail/ci/dovetail-pr.yml) runs on every pull
request, deterministic only, and is safe to fail a build on.
[`dovetail-scheduled.yml`](skills/dovetail/ci/dovetail-scheduled.yml) runs weekly, adds the
judgement layer, upserts a single tracking issue, and never fails the build. Both are covered
in [gating a build](docs/guides/ci.md).

## Configuration and decisions

`.dovetail/config.toml` holds ignore globs, the model profile, per-check toggles and
per-reviewer overrides. `.dovetail/decisions.jsonl` records findings you have accepted as
intentional; it is committed, so a judgement made once applies to your colleagues and to CI.
Repo-specific rules go in `.dovetail/checks/*.py` as modules exposing `check(inventory, graph)`
- written there, "these three tables must stay in sync" is exact and free rather than something
a model might notice. This repository ships three as worked examples.

See [configuring a repository](docs/guides/configuring.md), [suppressing a
finding](docs/guides/suppressing.md) and [writing a repo-local
check](docs/guides/custom-checks.md).

## Documentation

**[The documentation index](docs/README.md)** reaches everything. Start with [getting
started](docs/getting-started.md) to learn it, the [guides](docs/README.md#doing) to run it in
CI and configure it, [the reference](docs/reference.md) to look something up, and [how a scan
works](docs/architecture.md) plus the [design notes](docs/design-notes.md) to understand it.

## Tests

```bash
python3 -m pytest skills/dovetail/tests/ -v      # 399 tests, no model calls, no network
```

Hacking on it, or running from source with live edits: [docs/dev-setup.md](docs/dev-setup.md),
[CONTRIBUTING.md](CONTRIBUTING.md), and [AGENTS.md](AGENTS.md) if you are an AI agent doing so
- it states the constraints that must not be broken.

The skill itself is [`skills/dovetail/SKILL.md`](skills/dovetail/SKILL.md).

## License

[MIT](LICENSE) © 2026 DBHQ Consulting Ltd
