# AGENTS.md

Guidance for AI agents (and people) working in this repository.

## What this is

**dovetail** - a repository coherence checker for AI coding agents. It follows the [Agent Skills](https://agentskills.io) layout (`skills/<name>/SKILL.md`) and ships as a [Claude Code plugin](https://code.claude.com/docs/en/plugins).

## Layout

```
.claude-plugin/plugin.json     # plugin manifest
skills/dovetail/SKILL.md       # the skill: dispatch and the triage loop
skills/dovetail/scripts/       # python, standard library only
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
or through [`ci_dispatch.py`](skills/dovetail/scripts/ci_dispatch.py) for the
scheduled job. Rubrics live in `skills/dovetail/references/reviewers/`, loaded by name
at dispatch time.

**Layer 3, triage** - entirely in [`SKILL.md`](skills/dovetail/SKILL.md). No
Python TUI: findings are rendered into the session as markdown, which makes the
loop a conversation that can be interrupted and questioned rather than a modal
application.

CI templates: [`dovetail-pr.yml`](skills/dovetail/ci/dovetail-pr.yml)
(deterministic, gates a merge) and
[`dovetail-scheduled.yml`](skills/dovetail/ci/dovetail-scheduled.yml)
(full audit, reports via [`issue.py`](skills/dovetail/scripts/issue.py), never
gates).

## The constraints that define this tool

Break any of these and it stops being the thing people can trust:

1. **Deterministic only.** No model calls, no network, no third-party imports. A finding must follow from the structure of the repository. This is what makes it safe to fail a build on - a checker with false positives gets switched off within a week.
2. **Never write to the scanned repository.** dovetail reports; it does not fix. The scan reads `.dovetail/decisions.jsonl` and never writes it; the only file it writes anywhere is `$GITHUB_STEP_SUMMARY`, and only when CI sets it. `store.append_decision` exists as a helper and is deliberately not called from the scan path.
3. **Fail loudly, never silently pass.** `--since` against an unresolvable ref exits `2`. A check that reports success because it could not run is worse than no check.
4. **Never hand a reviewer more than it can finish.** Work is sharded into batches of 20 files. A reviewer given the whole repository and one turn budget reads a handful of files and skips the rest in silence - which is indistinguishable from thoroughness in the output. This was measured: unsharded, a 474-file repo produced 24 judged findings; sharded, 149.
5. **Never trust a quote.** Every piece of evidence a reviewer returns is checked against the actual line in the file. A fabricated quote at a plausible line reads exactly like a true finding, which makes it the most damaging failure available.

## Conventions

- Python floor is **3.11**, standard library only. CI asserts the no-third-party-imports property, so an added dependency fails the build rather than quietly breaking the install story.
- SKILL.md references scripts via `${CLAUDE_SKILL_DIR}`, which Claude Code substitutes for personal, project and plugin installs alike. `install.sh` symlinks the whole skill directory with no rewrite; `install-codex.sh` rewrites the variable, since Codex does not substitute it.
- Tests are hermetic: no network, and they build throwaway git repositories in temp dirs rather than touching anything real.
- House style: British English, plain hyphens (no em or en dashes).

## Validating a change

```bash
python3 -m pytest skills/dovetail/tests/ -v     # 399 tests
python3 skills/dovetail/scripts/scan.py . --format json   # dogfood: scan this repo
claude plugin validate .
```

The repo carries two `.dovetail/checks/` plugins, both enforcing rules no
general check could know:

- `roster_matches_skill.py` - the reviewer table in `SKILL.md` must match
  `ROSTER` in `reviewer.py`
- `documented_test_count.py` - every documented test count must match the suite

The second exists because four documents here once carried four different
counts - 243, 359, 243 and 243 - against a suite of 394. A bare number in prose
is not checkable by any general rule, which is exactly what the plugin point is
for. (Those figures are written without the word that follows them in the docs,
because spelling them out in full would trip the very check being described.)

Dogfooding is not optional here. A coherence checker whose own repository is incoherent has refuted itself, and CI runs the scan against this repo on every push for exactly that reason.
