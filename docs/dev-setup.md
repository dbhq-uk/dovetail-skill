# Developer setup - dovetail

Set the skill up from source with a **live symlink install**, so your edits are active immediately in Claude Code (and Codex). End users don't need this - they install via the [DBHQ marketplace](../README.md#install).

## Prerequisites

- `git` (and the GitHub CLI `gh` if you'll push changes)
- `python3` 3.11 or newer - and nothing else. The skill is standard library only, so there is no virtualenv to create and no packages to install
- `pytest` if you want to run the test suite

## 1. Clone

```bash
git clone https://github.com/dbhq-uk/dovetail-skill.git ~/dbhq-dovetail
cd ~/dbhq-dovetail
```

## 2. Install (symlink)

```bash
./install.sh          # Claude Code: symlinks into ~/.claude/skills (edits are live)
./install-codex.sh    # Codex: installs into ~/.codex/skills
```

The committed skill references its scripts via `${CLAUDE_SKILL_DIR}` (the skill's own directory), which Claude Code substitutes for personal, project and plugin installs alike. So `install.sh` symlinks the **whole skill directory** into `~/.claude/skills/` - `SKILL.md`, `scripts/`, `ci/` and `tests/` are all live, and every edit takes effect with no re-run. Codex does not substitute `${CLAUDE_SKILL_DIR}`, so `install-codex.sh` rewrites it to the install path - **re-run `./install-codex.sh` after editing a `SKILL.md`** for Codex.

## 3. Verify

```bash
python3 -m pytest skills/dovetail/tests/ -v          # 399 tests, no network required
python3 skills/dovetail/scripts/scan.py . --format json   # this repo must scan clean
claude plugin validate .                             # the plugin metadata validates
```

Then, in Claude Code, try *"run dovetail on this repo"*.

## Working on the checks

Every check lives in `skills/dovetail/scripts/`. The bar for a new one is in [`CONTRIBUTING.md`](../CONTRIBUTING.md): **deterministic and false-positive free**, never writes to the scanned repository, and fails loudly rather than passing silently when it cannot run. [`AGENTS.md`](../AGENTS.md) states the same three constraints for an AI agent working here, and [`docs/design-notes.md`](design-notes.md) explains why the tool is shaped this way.

Because the scan is read-only and takes seconds, the fastest loop is to point it at a real repository while you work:

```bash
python3 skills/dovetail/scripts/scan.py ~/some-repo --format json | jq '.findings[] | {kind, severity, path}'
```

## Gating your own repositories

Copy [`skills/dovetail/ci/dovetail-pr.yml`](../skills/dovetail/ci/dovetail-pr.yml) into a repository's `.github/workflows/`. It scopes findings with `--since` so pre-existing drift does not block unrelated work, and fails the build on `high`. The workflow checks out with `fetch-depth: 0` because `--since` resolves its base ref from history; on a shallow clone the scan exits `2` and says so rather than passing silently.

## Working across machines

Editing anything under `~/dbhq-dovetail` (scripts or `SKILL.md`) is live immediately in Claude Code - the skill directory is symlinked whole. For Codex, re-run `./install-codex.sh` after a `SKILL.md` edit. If you develop on more than one machine, `git pull` before you start and `git push` when done to keep them in sync.
