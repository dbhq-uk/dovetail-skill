# Gating a build

Two workflow templates ship in [`skills/dovetail/ci/`](../../skills/dovetail/ci/), and they
want opposite things. Copy both into `.github/workflows/` in your own repository.

| Template | Cadence | Layers | Fails the build |
|---|---|---|---|
| `dovetail-pr.yml` | every pull request | deterministic only | yes, on `high` |
| `dovetail-scheduled.yml` | weekly | deterministic and judgement | never |

## The per-PR job

Deterministic only: no model, no API key, no network, seconds to run. That is what makes it
safe to block a merge on - a gate with false positives is one people learn to override, and
then it catches nothing.

```yaml
- uses: actions/checkout@v4
  with:
    fetch-depth: 0            # --since needs the base ref in history

- uses: actions/setup-python@v5
  with:
    python-version: '3.12'

- name: Check out dovetail
  uses: actions/checkout@v4
  with:
    repository: dbhq-uk/dovetail-skill
    path: .dovetail-skill

- name: Scan
  run: |
    python .dovetail-skill/skills/dovetail/scripts/scan.py . \
      --since "origin/${{ github.base_ref }}" \
      --format github \
      --fail-on high
```

Three details in there are load-bearing.

**`fetch-depth: 0`.** `--since` resolves the base ref out of history. On GitHub's default
shallow clone that ref is absent, and the scan exits `2` and says so rather than passing
silently. A check that reports success because it could not run is worse than no check.

**`--since`.** This is what makes adoption possible. A repository with existing drift cannot
switch on a whole-repository check without every unrelated pull request going red, so nobody
switches it on. `--since` scopes findings to the files the pull request actually touched, so
contributors are only held to what they changed. Accumulated debt belongs in the weekly job.

**`--format github`.** Findings come back as workflow annotations, so they appear inline on
the diff rather than in a log nobody opens. `high` findings annotate as errors, everything
else as warnings, and a job summary table is written to the run page.

The template scopes itself to `pull_request` events with an `if:` guard, because
`github.base_ref` is empty on a manual dispatch and `--since "origin/"` would then exit `2`
by design.

## The weekly job

The judgement layer is slow, metered and probabilistic, so the scheduled job reports and
**never** gates. It runs whole-repository rather than diff-scoped, because that is where
accumulated debt lives, and it upserts a single tracking issue - found by label and rewritten
in place. A weekly job that opens a fresh issue every week gets muted within a month.

It runs the deterministic scan first and separately, so those findings exist even if
everything after it fails. The judgement step needs `CLAUDE_CODE_OAUTH_TOKEN` as a repository
secret, from `claude setup-token`; without it the job warns and carries on with deterministic
findings only, rather than failing.

`workflow_dispatch` takes a `profile` input - `default`, `cheap` or `thorough` - so you can
run a deeper audit on demand without editing the file.

## Why judged findings never fail a build

`exit_code()` only considers findings whose `source` is `graph` or `check:*`. A judgement
reviewer's finding cannot fail a build even if you set `--fail-on low`, and that is enforced
in the code rather than left to the workflow author.

A check that *raised* does count, though: whenever `--fail-on` is not `none`, a non-empty
`failed_checks` exits `1`. An incomplete result must not read as a clean one.

## Honouring decisions in CI

`.dovetail/decisions.jsonl` is committed, so CI reads the same ledger you triaged against. A
finding you marked intentional during a session does not come back to block a colleague's pull
request, and nobody has to maintain a separate CI ignore list. See [suppressing
findings](suppressing.md).

## Running it outside GitHub

`scan.py` is a plain Python script with a documented exit code, so any CI works:

```bash
python3 scan.py . --since "$MERGE_BASE" --fail-on high > dovetail.json
```

`--format github` is the only GitHub-specific part. `json` gives you the same findings for
whatever your platform renders. Exit codes: `0` clean, `1` findings at or above the threshold
(or a check that raised), `2` the scan could not run at all.
