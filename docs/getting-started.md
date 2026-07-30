# Getting started

Install dovetail, run it against a repository you know well, and work through the first few
findings. About ten minutes, and nothing is written to your repository unless you say so.

This is one happy path. Every flag, check name and configuration key is in the
[reference](reference.md).

## Before you start

Python 3.11 or newer and `git`. That is the whole dependency list - no packages, no
virtualenv, no API key. The deterministic scan makes no network calls at all.

The judgement layer needs a model, which you have if you are running this inside Claude Code
or Codex. Everything below works without it; you just get the certain findings only.

## 1. Install

```
/plugin marketplace add dbhq-uk/marketplace
/plugin install dovetail@dbhq
```

Or from a clone, with live edits: `./install.sh` for Claude Code, `./install-codex.sh` for
Codex. See [dev-setup](dev-setup.md).

## 2. Scan something you know

Pick a repository you have worked in for a while. A repository you know is the right test,
because you can tell a real finding from a wrong one.

```bash
python3 ~/.claude/skills/dovetail/scripts/scan.py /path/to/repo --format json
```

That is the deterministic layer on its own: seconds, no model, no network, and it never
modifies the repository. You get JSON:

```json
{
  "findings": [ ... ],
  "suppressed": 0,
  "counts": {"high": 2, "medium": 5, "low": 2},
  "failed_checks": [],
  "profile": "default",
  "file_count": 474,
  "edge_count": 3120
}
```

`file_count` and `edge_count` are the scale it worked at: files inventoried, and typed
references resolved between them. If `edge_count` looks implausibly low, something is being
excluded - check `ignore` in `.dovetail/config.toml`.

If it exits `2`, read the error and stop rather than working around it. There are only two
causes: the path is not a git checkout, or a `.dovetail/config.toml` is present and invalid.
A config you wrote is one you expect to take effect, so an invalid one halts the run instead
of quietly falling back to defaults.

## 3. Now ask for it properly

```
run dovetail on this repo
```

This is how you will actually use it. dovetail runs the same scan, dispatches the judgement
reviewers in the background, and starts you on the certain findings while they are still
working:

```
dovetail · my-repo · 474 files, 3,120 references

  ✓ exact          9 findings   (2 high · 5 med · 2 low)
  ⋯ judgement      running - contradiction, staleness, xref
  - suppressed     3 by prior decisions

Starting with the 9 that are certain. More will join as reviewers land.
```

Three things in that header matter every time:

- **exact against judgement.** Exact findings are computed in Python and are certain.
  Judged findings come from a model and are probabilistic. You always know which you are
  looking at, and they are never blurred together.
- **suppressed.** Findings dismissed by a prior decision. Counted, never hidden.
- **anything that failed.** A reviewer that errored or a check that raised is named here.
  Incomplete results never present themselves as clean ones.

## 4. Answer the first question

Findings arrive one at a time, as a question box with the evidence above it:

```
[1/9] flag_drift · high · exact
README.md:40 documents --out, but the script has no such flag.

  README.md:40          `--out FILE      write the report here`
  scripts/run.py:12     add_argument("--output", help="write the report here")

Fix: rename the flag in README.md:40 to --output
```

Your options are to apply the fix, skip it for now, or mark it intentional. You can also type
anything else - `edit` to describe a different fix, `explain` for the reference graph
neighbourhood and git history behind the finding, `quit` to stop.

One finding per box, always. And where the evidence names a winner, one option is marked
**(Recommended)** with the grounds in its description - which file is newer, which side the
code agrees with. Where nothing arbitrates, nothing is marked and you get a genuinely open
question. That asymmetry is the point: a tool that recommends on every finding teaches you to
stop reading.

After each applied fix the scan re-runs, which is free, and any queued findings that the fix
resolved drop out:

```
✓ applied. 2 queued findings resolved by this fix
  (docs/ja/config.md:24, docs/troubleshooting.md:112) - 4 remaining.
```

## 5. Record one decision

Some findings are intentional. A vendored duplicate is meant to be a duplicate; an entry
point nothing links to is meant to be an orphan. Marking one intentional appends a line to
`.dovetail/decisions.jsonl` in your repository:

```jsonl
{"id":"sha256:...","verdict":"intentional","reason":"vendored copy, kept in sync deliberately","at":"2026-07-30","summary":"vendor/parser.js duplicates src/parser.js"}
```

Commit that file. Because it is committed rather than local, a judgement made once applies to
your colleagues and to CI, and the finding never comes back to block someone else's pull
request.

## Where to go next

- [Gating a build](guides/ci.md) - the deterministic layer is trustworthy enough to fail a PR
- [Suppressing findings](guides/suppressing.md) - the ledger in more detail
- [Configuring a repository](guides/configuring.md) - ignores, profiles, per-reviewer models
- [Writing a repo-local check](guides/custom-checks.md) - turn one of your own rules into an
  exact, free check
- [Design notes](design-notes.md) - why the tool is shaped this way
