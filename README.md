<div align="center">

<img src="assets/logo.svg" alt="dovetail skill for Claude Code, by DBHQ" width="420">

# dovetail

**Does your repository still agree with itself?**

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Claude Code](https://img.shields.io/badge/Claude_Code-Plugin-blueviolet)](https://code.claude.com/docs/en/plugins)
[![Platform](https://img.shields.io/badge/Platform-Linux%20%7C%20macOS%20%7C%20WSL-lightgrey)]()

A free, open-source tool by [DBHQ](https://dbhq.uk) - documented at [skills.dbhq.uk](https://skills.dbhq.uk/dovetail/)

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
network, no third-party packages. Seventeen checks, and the same repository gives the same
findings every time, so a finding can be reproduced by anyone who clones it.

**Which means you can fail a build on it.** A checker that produces false positives gets
switched off within a week - the triage costs more than the drift. So every exact finding
carries a tier. A **proven** finding follows from the structure alone - a link to nothing, a
code block that does not parse - and fails `--fail-on`. A **heuristic** finding is a likely
problem that intent can explain - a file nothing links to may be an entry point - and it
reports without gating unless your config opts that check in. The scan takes seconds, and the
time grows in line with the size of the repository: about 1.3 seconds of CPU for 1,000 files,
and about 4 for 3,000. So it costs you nothing to run on every pull request. Judged findings never gate a build, in either
CI job.

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
in the triage loop, only one at a time, and only on your say-so - and dovetail compares a
content hash of every file between edits, so if anything changes that it did not apply, the run
stops.

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
credentials. A 3.11+ interpreter anywhere on your `PATH` is enough - if `python3` itself is
older, dovetail re-execs under the newer one rather than asking you to change your machine.

## Requirements

**Python 3.11 or newer, and `git`.** Standard library only - no virtualenv, no
packages, no credentials, and nothing to install beyond the interpreter. A
3.11+ interpreter anywhere on your `PATH` is enough; `python3` itself does not
have to be that version.

The exact layer needs no model and no network either, which is what lets it
run on every pull request.

## What it finds

One finding from running the exact layer on this repository, in the format the pull-request
job uses:

```
$ python3 skills/dovetail/scripts/scan.py . --format github
::warning file=AGENTS.md,line=1,title=decoupled::AGENTS.md and skills/dovetail/tests/test_reviewer.py changed together in 5 of their last commits (83%25 coupling), but have changed apart 16 times since. Check whether the recent changes to one should have been mirrored in the other.
```

`--format github` prints one GitHub workflow annotation per finding (`%25` is how an annotation
escapes `%`). `--format json`, the default, prints the same findings as a JSON object, with the
evidence, the suggestion and a fingerprint for each.

No linter finds that one. Both files are valid, neither has a broken link, and nothing about
either is wrong on its own - the signal is entirely in the history, which is that two files
behaved as a pair for several commits and then stopped. It is the kind of drift you only notice
when the document is already wrong. The counts move as the history does, so your run of this
command will not print exactly this line.

Nothing here is a judgement call: the exact layer runs in Python with no model and no network,
so this finding is reproducible by anyone who clones the repo at the same commit.

## Usage

Ask in any session: *"run dovetail on this repo"*. It scans, shows you what is certain first,
and starts working through it while the judgement reviewers are still running.

Say *"run dovetail cheap"* to drop every reviewer a tier, or *"thorough"* to put them all on
the strongest model. *"quick"* skips the reviewers entirely and gives you the exact findings
only, for free.

Each finding arrives as a question box: apply the fix, skip it, mark it intentional, or batch
the whole class in one confirmation.

Where the evidence names a winner, one option is marked
**(Recommended)** and says what makes it the answer. Where it does not, nothing is marked and
you get an open question - a tool that recommends on every finding teaches you to stop reading.

Or drive the scan directly:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/scan.py /path/to/repo --format json
```

| Flag | Meaning |
|---|---|
| `--format json\|github` | JSON to stdout, or GitHub workflow annotations |
| `--since <ref>` | Only report findings touching files changed since `<ref>`, including the target of a broken link |
| `--fail-on none\|low\|medium\|high` | Exit non-zero when a proven finding at or above this severity exists |
| `--ignore <glob>` | Exclude a glob; repeatable |
| `--no-plugins` | Skip `.dovetail/checks/*.py`, which is code from the scanned repository |

The whole tutorial is [docs/getting-started.md](docs/getting-started.md).

## In CI

Two templates in [`skills/dovetail/ci/`](skills/dovetail/ci/), because the two jobs want
opposite things. [`dovetail-pr.yml`](skills/dovetail/ci/dovetail-pr.yml) runs on every pull
request, deterministic only, and fails the build only on proven findings.
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
python3 -m pytest skills/dovetail/tests/ -v      # 645 tests, no model calls, no network
```

Hacking on it, or running from source with live edits: [docs/dev-setup.md](docs/dev-setup.md),
[CONTRIBUTING.md](CONTRIBUTING.md), and [AGENTS.md](AGENTS.md) if you are an AI agent doing so
- it states the constraints that must not be broken.

The skill itself is [`skills/dovetail/SKILL.md`](skills/dovetail/SKILL.md).

## Also from DBHQ

Every DBHQ agent skill is free, open source and installable from the same
marketplace, and all of them are documented at
**[skills.dbhq.uk](https://skills.dbhq.uk)**. The marketplace itself is
[dbhq-uk/marketplace](https://github.com/dbhq-uk/marketplace) - one
`/plugin marketplace add` and every one of them is available.

| Skill | What it does |
|---|---|
| [outlook](https://skills.dbhq.uk/outlook/) | Microsoft 365 mail and calendar, from the terminal |
| [trello](https://skills.dbhq.uk/trello/) | Your boards, run from your agent |
| [legwork](https://skills.dbhq.uk/legwork/) | Research that settles a decision, and says when it cannot |
| [verve](https://skills.dbhq.uk/verve/) | Strips AI tells from prose and puts a voice back |
| [vela](https://skills.dbhq.uk/vela/) | Compiler-exact code search, in any language you index |
| [garmin](https://skills.dbhq.uk/garmin/) | Your Garmin data, answered in the terminal |
| [imager](https://skills.dbhq.uk/imager/) | Images from GPT Image 2, costed before it spends |
| [gitview](https://skills.dbhq.uk/gitview/) | Which branches are finished, and which only look like it |
| [atlassian](https://skills.dbhq.uk/atlassian/) | Jira issues and Confluence pages |
| [pennyblack](https://skills.dbhq.uk/pennyblack/) | A physical letter, posted from the terminal |
| [buildwork](https://skills.dbhq.uk/buildwork/) | Your open issues, run as parallel agents |
| [deskwork](https://skills.dbhq.uk/deskwork/) | What an agent noticed, tracked as real work |
| [groupwork](https://skills.dbhq.uk/groupwork/) | A second agent on the work, adversary or partner |
| [headwork](https://skills.dbhq.uk/headwork/) | One decision at a time, with a recommendation |

Plus [heliograph](https://skills.dbhq.uk/heliograph/), for a machine you cannot log into.

## Licence

[MIT](LICENSE) © 2026 DBHQ Consulting Ltd
