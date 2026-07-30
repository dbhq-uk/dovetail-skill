# dovetail documentation

Does your repository still agree with itself? Start at the [project README](../README.md) if
you have not read it.

These pages are organised by what you are trying to do rather than by feature, and each one is
a single kind of document, so the heading tells you whether it will help.

## Learning

**[Getting started](getting-started.md)** &nbsp; A tutorial. Install it, scan a repository you
know, answer the first question and record one decision, in about ten minutes.

## Doing

**[Gating a build](guides/ci.md)** &nbsp; The per-PR job and the weekly one, why they want
opposite things, and what `--since` and `fetch-depth: 0` are actually for.

**[Suppressing a finding](guides/suppressing.md)** &nbsp; The committed decisions ledger, what
the fingerprint does and does not survive, and when to reach for config instead.

**[Configuring a repository](guides/configuring.md)** &nbsp; Ignore globs, turning a check
off, model profiles, and per-reviewer overrides.

**[Writing a repo-local check](guides/custom-checks.md)** &nbsp; Turning one of your
repository's own rules into an exact, free check, with three worked examples from this
repository.

## Looking things up

**[Reference](reference.md)** &nbsp; Every flag, exit code, output field, check name,
reviewer, config key and ledger field.

**[The finding schema](../skills/dovetail/references/finding-schema.md)** &nbsp; The contract
every judgement reviewer satisfies. Ships inside the skill, because both dispatch paths
validate against it.

**[`.dovetail/config.toml`](../skills/dovetail/references/config.md)** &nbsp; The annotated
configuration file, likewise shipped with the skill.

## Understanding

**[How a scan works](architecture.md)** &nbsp; The two layers, what runs in what order, why
sharding is not an optimisation, and where each part's certainty ends.

**[Design notes](design-notes.md)** &nbsp; Why the tool is shaped this way - deterministic
first, reports rather than fixes, a graph rather than a text search, and decisions committed
rather than remembered.

## For contributors

- [dev-setup.md](dev-setup.md), running dovetail from source with live edits
- [AGENTS.md](../AGENTS.md), the working brief for anyone changing the code, human or otherwise
- [CONTRIBUTING.md](../CONTRIBUTING.md)
