# Configuring a repository

`.dovetail/config.toml`, committed alongside the decisions ledger. Every key is optional and
so is the file - dovetail runs with none of it.

The full key list is in [`references/config.md`](../../skills/dovetail/references/config.md),
which ships inside the skill. This page is about which knob to reach for.

```toml
ignore = ["vendor/**", "third_party/**", "*.generated.md"]
profile = "default"

[checks]
stale_todos = false

[reviewers.spec-flow]
enabled = false

[reviewers.code-hygiene]
model = "opus"
effort = "high"
```

## An invalid config stops the run

A config that is present but unparseable raises rather than falling back to defaults. That is
deliberate: a typo that quietly disabled half the checks is the worst available failure for a
tool whose entire value is that its output can be trusted. You get exit `2` and a message.

## Excluding files

```toml
ignore = ["vendor/**", "docs/generated/**"]
```

`**` works properly here - it crosses path separators, unlike Python's `fnmatch`. Ignored
files leave the inventory entirely, so nothing references them and they reference nothing.

`--ignore GLOB` on the command line does the same thing for one run and is repeatable. The
two combine rather than override.

Reach for `ignore` when a whole tree is out of scope - generated output, a vendored
dependency, a submodule you do not own. For one intentional finding inside a tree you do care
about, use the [decisions ledger](suppressing.md) instead, which records *why*.

## Turning a check off

```toml
[checks]
stale_todos = false
decoupled_pairs = false
```

Keys are the check function names, so a disabled check is traceable straight to the code that
implements it. The full list is in the [reference](../reference.md#deterministic-checks).

Those two are the usual candidates. `stale_todos` flags TODO markers older than six months,
and a repository that treats TODOs as a permanent backlog does not want it. `decoupled_pairs`
reports files with a long shared git history that have stopped moving together, which is a
genuinely interesting signal and a noisy one on a repository that has just been reorganised.

Prefer disabling a check to suppressing every finding it produces. One line of config beats
forty ledger entries, and it says what you actually mean.

## Model profiles

Three profiles set what the judgement layer costs:

| Profile | Effect |
|---|---|
| `default` | The per-reviewer tiering: haiku for extraction, sonnet for the middle, opus for adjudication |
| `cheap` | Every reviewer one tier down, escalation off |
| `thorough` | Everything on the strongest model |

`profile` in the config is the durable default for the repository. A profile spoken during a
run - *"run dovetail cheap"* - overrides it for that run only. *"run dovetail quick"* skips
the reviewers entirely and gives you the deterministic findings, which cost nothing.

Per-reviewer settings in `[reviewers.<name>]` beat the profile, because config is the durable
statement and a spoken profile is for one session.

## Per-reviewer overrides

```toml
[reviewers.spec-flow]
enabled = false          # this repo has no diagrams

[reviewers.code-hygiene]
model = "opus"           # mostly shell, which needs the judgement
```

Keys are `enabled`, `model` (`haiku`, `sonnet`, `opus`) and `effort` (`low`, `medium`,
`high`). Reviewer names: `xref`, `convention`, `code-hygiene`, `contradiction`, `staleness`,
`spec-flow`, `claim-extract`.

Disabling a reviewer your repository has no material for is the highest-value change here. A
repository with no diagrams pays `spec-flow` opus rates to confirm that every run.

## What dovetail's own config looks like

```toml
profile = "default"
```

Deliberately minimal, and worth copying as a starting point. This repository is the tool's
first user, and switching things off here would hide exactly the findings that dogfooding
exists to surface.
