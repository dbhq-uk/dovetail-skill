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

- `python3 -m pytest skills/dovetail/tests/ -v` - all 410 tests pass, no network needed
- `python3 skills/dovetail/scripts/scan.py . --format json` - this repo scans clean
- `claude plugin validate .` - the plugin validates

## The bar for a new check

A check earns its place only if it is **deterministic and false-positive free**. If it needs a judgement call, it does not belong in the scan - that is the property that lets people gate a build on the output, and one noisy check costs more trust than a whole class of findings is worth.

Two related rules: a check may never write to the scanned repository, and it must fail loudly rather than silently pass when it cannot run.

## Licence

By contributing you agree your work is licensed under the [MIT licence](LICENSE).
