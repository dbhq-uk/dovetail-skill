# AGENTS.md

Guidance for AI agents (and people) working in this repository.

## What this is

**dovetail** - a repository coherence checker for AI coding agents. It follows the [Agent Skills](https://agentskills.io) layout (`skills/<name>/SKILL.md`) and ships as a [Claude Code plugin](https://code.claude.com/docs/en/plugins).

## Layout

```
.claude-plugin/plugin.json     # plugin manifest
skills/dovetail/SKILL.md       # the skill (agent-facing instructions)
skills/dovetail/scripts/       # python, standard library only
skills/dovetail/ci/            # workflow template users copy into their own repo
skills/dovetail/tests/         # 243 tests, offline
install.sh / install-codex.sh  # local symlink installers (Claude / Codex)
docs/design-notes.md           # why the tool is shaped this way
```

## The three constraints that define this tool

Break any of these and it stops being the thing people can trust:

1. **Deterministic only.** No model calls, no network, no third-party imports. A finding must follow from the structure of the repository. This is what makes it safe to fail a build on - a checker with false positives gets switched off within a week.
2. **Never write to the scanned repository.** dovetail reports; it does not fix. The scan reads `.dovetail/decisions.jsonl` and never writes it; the only file it writes anywhere is `$GITHUB_STEP_SUMMARY`, and only when CI sets it. `store.append_decision` exists as a helper and is deliberately not called from the scan path.
3. **Fail loudly, never silently pass.** `--since` against an unresolvable ref exits `2`. A check that reports success because it could not run is worse than no check.

## Conventions

- Python floor is **3.11**, standard library only. CI asserts the no-third-party-imports property, so an added dependency fails the build rather than quietly breaking the install story.
- SKILL.md references scripts via `${CLAUDE_SKILL_DIR}`, which Claude Code substitutes for personal, project and plugin installs alike. `install.sh` symlinks the whole skill directory with no rewrite; `install-codex.sh` rewrites the variable, since Codex does not substitute it.
- Tests are hermetic: no network, and they build throwaway git repositories in temp dirs rather than touching anything real.
- House style: British English, plain hyphens (no em or en dashes).

## Validating a change

```bash
python3 -m pytest skills/dovetail/tests/ -v     # 243 tests
python3 skills/dovetail/scripts/scan.py . --format json   # dogfood: scan this repo
claude plugin validate .
```

Dogfooding is not optional here. A coherence checker whose own repository is incoherent has refuted itself, and CI runs the scan against this repo on every push for exactly that reason.
