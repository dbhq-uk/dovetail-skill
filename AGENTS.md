# AGENTS.md

Guidance for AI agents (and people) working in this repository.

## What this is

**dovetail** - a repository coherence checker for AI coding agents. It follows the [Agent Skills](https://agentskills.io) layout (`skills/<name>/SKILL.md`) and ships as a [Claude Code plugin](https://code.claude.com/docs/en/plugins).

## Layout

```
.claude-plugin/plugin.json     # plugin manifest
skills/dovetail/SKILL.md       # the skill: the conversation rules and the verbs it calls
skills/dovetail/scripts/       # python, standard library only; dovetail.py drives a run
skills/dovetail/references/    # the finding contract and one rubric per reviewer
skills/dovetail/ci/            # workflow templates users copy into their own repo
skills/dovetail/tests/         # offline, no model calls
install.sh / install-codex.sh  # local symlink installers (Claude / Codex)
docs/design-notes.md           # why the tool is shaped this way
```

### The three layers

**Layer 1, deterministic** - [`scan.py`](skills/dovetail/scripts/scan.py) runs
the graph queries, the exact checks, the convention checks, the git-history
signals and any repo-local plugins. Seconds, no model, no network.

**Layer 2, judgement** - [`reviewer.py`](skills/dovetail/scripts/reviewer.py)
holds the roster, tiering and the shared validator;
[`claimscan.py`](skills/dovetail/scripts/claimscan.py) narrows contradiction
candidates into clusters. Reviewers run as in-session subagents interactively,
one per shard, or through [`ci_dispatch.py`](skills/dovetail/scripts/ci_dispatch.py)
for the scheduled job. Both paths take their shards from `ci_dispatch.plan_shards`,
so they cannot shard the same repository differently. Rubrics live in `skills/dovetail/references/reviewers/`, loaded by name
at dispatch time.

**Layer 3, triage** - the conversation is in [`SKILL.md`](skills/dovetail/SKILL.md);
the state is in [`dovetail.py`](skills/dovetail/scripts/dovetail.py). No Python
TUI: findings are rendered into the session as markdown, which makes the loop a
conversation that can be interrupted and questioned rather than a modal
application. But the agent never reads the scan's JSON or the clusters: on a
repository of about 900 files they fill most of its context before the first
question. `dovetail.py` keeps the run on disk and each verb (`scan`, `next`,
`decide`, `check`, `rescan`, `prepare-review`, `wave`, `import-review`) prints
only what the next step needs. Every value reaches it as an argument, never
pasted into code.

CI templates: [`dovetail-pr.yml`](skills/dovetail/ci/dovetail-pr.yml)
(deterministic, gates a merge) and
[`dovetail-scheduled.yml`](skills/dovetail/ci/dovetail-scheduled.yml)
(full audit, reports via [`issue.py`](skills/dovetail/scripts/issue.py), never
gates).

## The constraints that define this tool

Break any of these and it stops being the thing people can trust:

1. **The exact layer is deterministic.** No model calls, no network, no third-party imports. An exact finding must follow from the structure of the repository. A check whose findings intent can explain (an orphan may be an entry point) is **heuristic**: its findings carry `tier: heuristic` and do not fail `--fail-on` unless `[gate]` in the config opts it in. Only **proven** findings gate by default. This is what makes the gate safe - a checker with false positives gets switched off within a week. The list lives in `HEURISTIC_CHECKS` in `config.py`. The judgement layer does call models, and is kept apart: its findings are labelled judged, and never gate a build in either CI job.
2. **The scan never writes to the scanned repository.** Only the triage loop writes, one approved fix at a time. The scan reads `.dovetail/decisions.jsonl` and never writes it; the only file it writes anywhere is `$GITHUB_STEP_SUMMARY`, and only when CI sets it. `store.append_decision` is called only from `dovetail.py decide`, on the user's say-so during triage, and CI fails if anything on the scan path calls it.
3. **Fail loudly, never silently pass.** `--since` against an unresolvable ref exits `2`. A check that reports success because it could not run is worse than no check.
4. **Never hand a reviewer more than it can finish.** Work is sharded into batches of 20 files. A reviewer given the whole repository and one turn budget reads a handful of files and skips the rest in silence - which is indistinguishable from thoroughness in the output. This was measured: unsharded, a 474-file repo produced 24 judged findings; sharded, 149.
5. **Never trust a quote, but do not confuse a moved one with an invented one.** Every piece of evidence a reviewer returns is checked against the actual line in the file. A fabricated quote at a plausible line reads exactly like a true finding, which makes it the most damaging failure available. The check resolves to four states - match, moved, stale, absent - because dovetail edits files during its own triage loop: a fix the user approved can rewrite the line a still-running reviewer quoted, and only the committed blob separates that from an invention. Calling it fabrication was measured costing ten sound findings in one run.

## Conventions

- Python floor is **3.11**, standard library only. CI asserts the no-third-party-imports property, so an added dependency fails the build rather than quietly breaking the install story.
- The floor binds the interpreter that *runs*, not `python3` specifically. [`bootstrap.py`](skills/dovetail/scripts/bootstrap.py) re-execs the original command line under the newest suitable interpreter on `PATH`, so a host with 3.10 as `python3` and 3.12 alongside is supported without touching the host. `bootstrap.ensure()` must be called before the first `tomllib` import on any path that reaches it - today that is `scan.py`, `dovetail.py`, `config.py` and `exactcheck.py`.
- SKILL.md references scripts via `${CLAUDE_SKILL_DIR}`, which Claude Code substitutes for personal, project and plugin installs alike. `install.sh` symlinks the whole skill directory with no rewrite; `install-codex.sh` rewrites the variable, since Codex does not substitute it.
- Tests are hermetic: no network, and they build throwaway git repositories in temp dirs rather than touching anything real.
- House style: British English, plain hyphens (no em or en dashes).

## Validating a change

```bash
python3 -m pytest skills/dovetail/tests/ -v     # 568 tests
python3 skills/dovetail/scripts/scan.py . --format json   # dogfood: scan this repo
claude plugin validate .
```

The repo carries three `.dovetail/checks/` plugins, each enforcing a rule no
general check could know:

- `roster_matches_skill.py` - the reviewer table in `SKILL.md` must match
  `ROSTER` in `reviewer.py`
- `documented_test_count.py` - every documented test count must match the suite
- `house_style_dashes.py` - no em or en dashes in this repository's markdown.
  It is a house rule, not a general one, because plenty of repositories use
  those dashes on purpose

The second exists because four documents here once carried four different
counts - 243, 359, 243 and 243 - against a suite of 394. A bare number in prose
is not checkable by any general rule, which is exactly what the plugin point is
for. (Those figures are written without the word that follows them in the docs,
because spelling them out in full would trip the very check being described.)

Dogfooding is not optional here. A coherence checker whose own repository is incoherent has refuted itself, and CI runs the scan against this repo on every push for exactly that reason.
