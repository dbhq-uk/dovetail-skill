# Reference

Every flag, exit code, check name, output field and configuration key. For how to use them,
see the [guides](README.md#doing).

## scan.py

```
scan.py <repo-path> [--format json|github] [--since REF]
                    [--fail-on none|low|medium|high] [--ignore GLOB ...]
```

Read-only. It never modifies the repository it is scanning, and the only file it writes
anywhere is the GitHub step summary, and only when CI provides `$GITHUB_STEP_SUMMARY`.

| Flag | Default | Meaning |
|---|---|---|
| `--format json\|github` | `json` | JSON to stdout, or GitHub workflow annotations |
| `--since REF` | off | Only report findings that touch a file changed since `REF`: an evidence file, or the target of a broken link or anchor, deleted and renamed files included |
| `--fail-on none\|low\|medium\|high` | `none` | Exit non-zero when a finding at or above this severity exists |
| `--ignore GLOB` | none | Exclude a glob. Repeatable, and combines with `ignore` in the config |

Run it directly, or ask for it in a session:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/scan.py /path/to/repo --format json
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | No finding met the threshold, and no check failed |
| `1` | A qualifying finding exists, or a check raised while `--fail-on` was not `none` |
| `2` | The scan could not run: not a git repository, an invalid `.dovetail/config.toml`, or a `--since` ref that does not resolve |

Exit `2` is deliberately loud. On a shallow clone `--since` cannot resolve its base ref, and a
check that reports success because it could not run is worse than no check at all.

Two rules govern `1`:

- **Judged findings never gate.** Only sources `graph` and `check:*` are counted, so a
  reviewer's finding cannot fail a build even at `--fail-on low`. Plugin findings
  (`plugin:*`) are not counted either.
- **A failed check does gate**, whenever `--fail-on` is not `none`. An incomplete result must
  not read as a clean one.

### JSON output

```json
{
  "findings": [],
  "suppressed": 0,
  "counts": {"high": 0, "medium": 0, "low": 0},
  "failed_checks": [],
  "profile": "default",
  "file_count": 474,
  "edge_count": 3120
}
```

| Field | Meaning |
|---|---|
| `findings` | Sorted by severity, then category, then first evidence file |
| `suppressed` | How many the decisions ledger removed. Counted, never hidden |
| `counts` | Kept findings by severity |
| `failed_checks` | Checks that raised, and plugins that failed, with the reason |
| `profile` | The model profile in force |
| `file_count` | Files inventoried |
| `edge_count` | References resolved between them |

### A finding

```jsonc
{
  "id": "sha256:…",                 // content fingerprint, excludes line numbers
  "source": "graph",                // graph | check:<name> | plugin:<name> | reviewer:<name>
  "category": "broken_link",
  "problem": "One sentence stating what is wrong.",
  "evidence": [{"file": "README.md", "line": 40, "quote": "the real text on that line"}],
  "suggestion": "What to do about it.",
  "fix": {"kind": "none"},
  "blast_radius": [],
  "severity": "high",               // high | medium | low
  "confidence": "high",             // high | medium | low
  "ssot_direction": "n/a"           // a | b | uncertain | n/a
}
```

`severity` is how much it matters if the finding is real; `confidence` is how sure we are that
it is. They are different axes and both are needed.

`ssot_direction` names which side of a divergence is authoritative: `a` for the first evidence
item, `b` for the second, `uncertain` when the text cannot settle it, `n/a` when the finding is
not a divergence. `uncertain` is never a failure - it routes the finding to a human question
instead of an automatic fix.

The contract reviewers write against is
[`references/finding-schema.md`](../skills/dovetail/references/finding-schema.md), which ships
inside the skill because both dispatch paths validate against it.

## dovetail.py

The run driver. An interactive run calls these verbs rather than reading the scan's JSON: on a
repository of about 900 files the JSON and the contradiction clusters come to hundreds of
kilobytes, which is most of an agent's context before the first question. The driver keeps the
run on disk and each verb prints a few lines.

```
dovetail.py <verb> [--repo PATH] ...
```

| Verb | What it does | Prints |
|---|---|---|
| `scan [--since REF] [--ignore GLOB]` | Runs the scan, starts a new run, snapshots every file | A summary of about eight lines |
| `next [--json]` | The next finding in queue order | The finding as markdown, then its id, box header, options and whether one may be recommended |
| `decide ID skip` | Defers the finding for this run | One line |
| `decide ID intentional\|wontfix --reason TEXT [--summary TEXT]` | Appends to `.dovetail/decisions.jsonl` | One line |
| `decide ID fix --files PATH ...` | Records a fix and the files it changed | One line, or `STOP` and exit `3` if another file changed |
| `check` | Compares every file with the snapshot | `clean`, or the changed files and exit `1` |
| `rescan` | Scans again | What the last fix resolved, and anything it introduced |
| `prepare-review [--profile P] [--reviewer NAME]` | Writes one prompt file per reviewer shard | One line per reviewer |
| `wave [--size N]` | Hands out the next shards, 4 by default and 5 at most | One line per shard, with its model |
| `import-review` | Validates the shard results that have landed, and queues them | Counts, and every dropped or failed shard by name |

`ID` is the short id `next` prints, or the full `sha256:` id. Run state lives in
`~/.dbhq/dovetail/runs/<repo>-<hash>/`, readable by its owner only; `DOVETAIL_HOME` moves the
root. `scan` starts a new run and discards the old one's shards.

**Write safety.** `scan` stores a content hash of every file git can see, tracked or not.
`check` compares against it, so it sees a second edit to a file that was already modified,
which `git status --porcelain` cannot: the line reads ` M file` both times. dovetail's own
writes, the files named in `decide ... fix --files` and the ledger, are taken into the
snapshot as they happen.

**Shards.** `prepare-review` splits each reviewer's work the way `ci_dispatch.py` does: 20
files, or 25 contradiction clusters, per shard. A shard's prompt is the scheduled job's prompt
for the same batch, plus the path to write the JSON array to. `import-review` validates each
result with the lenient validator, dedupes on the finding id, drops anything the ledger already
suppresses, and holds a low-confidence finding from haiku or sonnet for an opus shard in a later
wave, unless the profile is `cheap`.

Exit codes: `0` done, `1` `check` found a change, `2` the verb could not run (no run started, an
unknown id, a missing `--reason` or `--files`, a path outside the repository, or anything `scan`
itself exits `2` on), `3` `decide ... fix` found a change it was not told about.

## Deterministic checks

Names are the check function names, which is what `[checks]` in the config takes, so a
disabled check is traceable to the code implementing it.

| Name | What it finds | Source |
|---|---|---|
| `broken_links` | Links whose target does not exist | `graph` |
| `dangling_anchors` | `#anchor` links to a heading or HTML anchor that is not there | `graph` |
| `orphans` | Files nothing references | `graph` |
| `exact_duplicates` | Byte-identical files | `graph` |
| `near_duplicates` | Files that are nearly identical | `graph` |
| `translation_lag` | Translations behind their base document | `graph` |
| `flag_drift` | Documented flags a script does not declare | `check:flags` |
| `unparseable_code_blocks` | ` ```python ` / ` ```json ` / ` ```toml ` blocks that do not parse | `check:codeblock` |
| `missing_paths` | Backticked repository paths in prose that do not exist | `check:paths` |
| `signature_drift` | Documented calls the real signature would reject | `check:signature` |
| `version_drift` | Manifests declaring different versions | `check:version` |
| `dead_python_code` | Public Python symbols nothing names | `check:deadcode` |
| `shell_scripts_exit_on_error` | Executable shell scripts without `set -e` | `check:convention` |
| `scripts_are_executable` | Shebangs without the executable bit | `check:convention` |
| `skill_frontmatter` | `SKILL.md` missing or malformed frontmatter | `check:convention` |
| `decoupled_pairs` | Files with a long shared history that stopped moving together | `check:cochange` |
| `stale_todos` | TODO markers older than six months | `check:todo` |

Categories the deterministic layer owns, and which reviewers must therefore never report:
`broken_link`, `dangling_anchor`, `orphan`, `duplicate`, `near_duplicate`, `flag_drift`,
`signature_drift`, `version_drift`, `parse_error`, `missing_path`, `decoupled`, `stale_todo`.

## Reviewers

Six are dispatched for findings; `claim-extract` feeds the contradiction reviewer rather than
reporting.

| Reviewer | Model | Effort | What it is left with |
|---|---|---|---|
| `xref` | haiku | low | Missing cross-references worth making |
| `convention` | sonnet | medium | The repository's own stated rules, where Python cannot check them |
| `code-hygiene` | sonnet | medium | Non-Python dead code, duplicated logic that has diverged |
| `contradiction` | opus | high | Two documents that cannot both be right |
| `staleness` | opus | high | Docs describing behaviour the code no longer has |
| `spec-flow` | opus | high | Diagrams and specs against the implementation |

Categories reviewers may report: `contradiction`, `missing_xref`, `staleness`, `convention`,
`dead_code`, `spec_drift`, `other`.

Rubrics are one file each, in
[`references/reviewers/`](../skills/dovetail/references/reviewers/).

### Profiles

| Profile | Effect |
|---|---|
| `default` | The tiering above |
| `cheap` | Every reviewer one tier down, escalation off |
| `thorough` | Everything on the strongest model |

Spoken during a run ("run dovetail cheap") for one run; set in the config as the durable
default. "Quick" skips the reviewers entirely. Per-reviewer config overrides beat the profile.

### Validation

Every reviewer's output is validated before it reaches the queue:

- the category must not be one the deterministic layer owns
- a contradiction must carry evidence from both sides
- **every quote must appear at the line it cites**

A quote found elsewhere in the same file has its line corrected and is kept. A quote that is
in the committed file but not the working one was edited during the run, and that finding is
dropped as stale. Anything else is dropped as fabricated, and that includes evidence that
cannot be checked: an empty quote, a missing or unreadable file, or a path outside the
repository.

An unsound finding is dropped and named, with the reason; the reviewer's other findings
survive. Only output that is not a JSON array at all fails the whole reviewer, and that
reviewer is named as failed rather than reported as clean.

## .dovetail/config.toml

Committed, per-repository, entirely optional. A file that is present but invalid stops the run
with exit `2` rather than falling back to defaults.

```toml
ignore = ["vendor/**", "*.generated.md"]     # globs excluded from the scan, ** works
profile = "default"                          # default | cheap | thorough

[checks]
stale_todos = false                          # disable by check function name

[reviewers.spec-flow]
enabled = false                              # bool
model = "opus"                               # haiku | sonnet | opus
effort = "high"                              # low | medium | high
```

Full commentary: [`references/config.md`](../skills/dovetail/references/config.md).

## .dovetail/decisions.jsonl

Committed, append-only, one JSON object per line. You append to it; dovetail only writes it
when you approve a suppression during triage.

```jsonl
{"at":"2026-07-30","id":"sha256:…","reason":"why","summary":"human-readable echo","verdict":"intentional"}
```

Later lines override earlier ones for the same `id`. A malformed line is skipped rather than
fatal. `id` is a fingerprint over category, files and a normalised claim - **not** line
numbers - so a decision survives the finding moving but not the finding changing.

## .dovetail/checks/*.py

Repo-local checks. A module exposing `check(inventory, graph)` and returning a list of
findings with at least `id`, `source`, `category`, `problem`, `evidence`, `suggestion` and
`severity`. `source` is rewritten to `plugin:<module>`. Names beginning with `_` are skipped.
A plugin that raises is named in `failed_checks` and skipped.

See [writing a repo-local check](guides/custom-checks.md).

## CI templates

| Template | Trigger | Layers | Gates |
|---|---|---|---|
| [`dovetail-pr.yml`](../skills/dovetail/ci/dovetail-pr.yml) | `pull_request` | deterministic | `--fail-on high` |
| [`dovetail-scheduled.yml`](../skills/dovetail/ci/dovetail-scheduled.yml) | weekly cron, `workflow_dispatch` | deterministic and judgement | never |

Both need `fetch-depth: 0`. The scheduled job needs `CLAUDE_CODE_OAUTH_TOKEN` for the
judgement layer and `issues: write` for the tracking issue; without the token it warns and
reports deterministic findings only.

## Requirements

Python 3.11 or newer, and `git`. No third-party packages, no virtualenv, no lockfile, no API
key and no network for the deterministic layer. The judgement layer needs a model; everything
else runs without one.

The floor applies to whichever interpreter runs dovetail, which need not be the one `python3`
names. If `python3` is older than 3.11, dovetail re-execs under the newest suitable interpreter
on `PATH` - so 3.10 as `python3` with 3.12 installed alongside works, and nothing on the host
has to change. If there is no such interpreter, it exits `2` and names the fix.

```bash
python3 -m pytest skills/dovetail/tests/ -q      # 531 tests, no model calls, no network
```
