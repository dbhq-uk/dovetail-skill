# Contributing

Thanks for your interest - contributions are welcome.

## Ways to help

- Report a bug or request a feature via [issues](https://github.com/dbhq-uk/dovetail-skill/issues)
- Improve the checks, the skill instructions or the CI template via a pull request

## Local development

```bash
git clone https://github.com/dbhq-uk/dovetail-skill.git
cd dovetail-skill
./install.sh          # symlinks into ~/.claude/skills (edits are live)
```

The whole skill directory is symlinked, so edits - including to `SKILL.md` - are live immediately. For Codex, re-run `./install-codex.sh` after editing a `SKILL.md`, since that path is rewritten at install time.

## Before opening a PR

- `python3 -m pytest skills/dovetail/tests/ -v` - all 652 tests pass, no network needed
- `python3 skills/dovetail/scripts/scan.py . --fail-on low` - this is what CI runs. It fails on any proven finding, and on any finding from this repo's own `.dovetail/checks/` plugins, which the config opts into the gate. Heuristic findings from its own history, such as the one in the README, only warn
- `claude plugin validate .` - the plugin validates

## The bar for a new check

A check earns its place only if it is **deterministic**. If it needs a judgement call, it does not belong in the scan. A check that gates a build must also be **false-positive free** - that is the property that lets people gate a build on the output, and one noisy check costs more trust than a whole class of findings is worth. A check whose findings intent can explain, such as an orphan that is really an entry point, is **heuristic**: add it to `HEURISTIC_CHECKS` in `config.py`, and it reports without gating unless a repository opts it in.

Two related rules: a check may never write to the scanned repository, and it must fail loudly rather than silently pass when it cannot run.

## Licence

By contributing you agree your work is licensed under the [MIT licence](LICENSE).
