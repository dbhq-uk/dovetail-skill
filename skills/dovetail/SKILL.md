---
name: dovetail
description: Check whether a repository agrees with itself, then work through the findings one at a time. Finds broken links, dangling heading anchors, orphaned files, duplicate content, translations that have fallen behind, drift between docs and code, contradictions between documents, and conventions the repo states but does not follow. Trigger on phrases like "dovetail", "check this repo", "does this repo agree with itself", "find contradictions", "repo coherence", "docs drift", "audit this repository".
---

# dovetail

Checks whether a repository agrees with itself, and walks through what it finds.

Two layers produce findings. **Exact** findings are computed in Python - links, anchors, orphans, duplicates, flag and signature drift, conventions, git-history signals. They are certain. **Judged** findings come from reviewers - contradictions, semantic staleness, spec drift, non-Python dead code. They are probabilistic.

The user must always know which they are looking at. Never blur the two.

## Run

### 1. Scan (always)

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/scan.py <repo-path> --format json
```

Seconds, no network, no model. Never modifies the target.

Read the JSON: `findings`, `suppressed`, `counts`, `failed_checks`, `profile`, `file_count`, `edge_count`.

If it exits `2`, report the error and stop - the repository is not a git checkout, or `.dovetail/config.toml` is invalid. Do not proceed on defaults; a config the user wrote is one they expect to take effect.

### 2. Dispatch the judgement reviewers (unless the user said "quick" or "exact only")

Start these **before** triaging, so they land while the user works through the certain findings. Layer 1 finishes before the first reviewer returns, and a run abandoned after two minutes has still delivered every broken link and duplicate in the repository.

Get the clusters the contradiction reviewer needs:

```bash
python3 -c "import sys; sys.path.insert(0, '${CLAUDE_SKILL_DIR}/scripts'); \
from discover import discover; from refgraph import build_graph; from claimscan import build_clusters; \
import json; inv=discover('<repo-path>'); print(json.dumps(build_clusters(inv, build_graph('<repo-path>', inv))))"
```

Then spawn one subagent per reviewer, in parallel. For each:

- Read its rubric from `${CLAUDE_SKILL_DIR}/references/reviewers/<name>.md`
- Read the contract from `${CLAUDE_SKILL_DIR}/references/finding-schema.md`
- Give it its context: clusters for `contradiction`, docs for `staleness` / `spec-flow` / `xref` / `convention`, code for `code-hygiene`
- **Pass the model override explicitly.** Never let a reviewer inherit the orchestrator's model.

| Reviewer | Model | Effort |
|---|---|---|
| `xref` | haiku | low |
| `convention` | sonnet | medium |
| `code-hygiene` | sonnet | medium |
| `contradiction` | opus | high |
| `staleness` | opus | high |
| `spec-flow` | opus | high |

Profiles: **cheap** drops every reviewer one tier and disables escalation; **thorough** puts everything on opus/high. The user speaks a profile ("run dovetail cheap"); `.dovetail/config.toml` sets the durable default and per-reviewer overrides, which win.

**Validate every reviewer's output** before it reaches the queue:

```bash
python3 -c "import sys; sys.path.insert(0, '${CLAUDE_SKILL_DIR}/scripts'); \
from reviewer import validate_findings; import json; \
print(json.dumps(validate_findings(open('<file>').read(), '<reviewer>', '<repo-path>')))"
```

This rejects fabricated evidence by checking every quote against the actual line. A reviewer whose output fails validation **failed** - name it in the header, do not use a partial result.

Escalate any finding with `confidence: low` from a haiku or sonnet reviewer to opus before queueing it, unless the profile is `cheap`.

### 3. Header

```
dovetail · <repo> · <file_count> files, <edge_count> references

  ✓ exact          9 findings   (2 high · 5 med · 2 low)
  ⋯ judgement      running - contradiction, staleness, xref
  - suppressed     3 by prior decisions

Starting with the 9 that are certain. More will join as reviewers land.
```

Always show the exact/judgement split, always show the suppressed count. Nothing is ever hidden silently. Name any failed check or reviewer: `⚠ staleness failed - findings incomplete`.

## Triage

Order by **blast radius, then severity, then confidence**. Root causes before symptoms, so fixing one visibly shrinks the queue.

### One finding, one question box

Render the finding as markdown, then ask for the decision with `AskUserQuestion`. The markdown carries the detail - quotes, diffs, blast radius - because the box cannot hold it. The box carries the choice and nothing else.

Never put two findings in one box, and never ask for a decision in prose when the box is available. A typed `fix` is a verb the user has to remember; an option is one they can see.

Every box:

- `header` - the category, truncated to 12 characters (`broken_link`, `contradictn`)
- `question` - the decision itself, phrased so it can be answered without scrolling back up to the evidence
- `multiSelect: false`
- two to four options, most likely first, each with a `description` saying what actually happens to the files
- **never add an "Other" option.** It is supplied automatically, and spending an option on "something else" wastes a quarter of the box

`edit`, `intentional <reason>`, `explain` and `quit` arrive as free text through "Other". Read what the user typed and act on it - do not re-ask a question they have already answered.

### Recommending an option

Where the evidence names a winner, say so. Put that option **first** and append `(Recommended)` to its label. At most one option per box, ever.

A recommendation is a claim, so it carries its grounds: the `description` must say what makes it the answer, in the repository's own terms - which file is newer, which side the code agrees with, how many documents cite each value. "Best practice" is not grounds. If the description cannot name the evidence, the recommendation has not been earned.

Recommend when:

- the finding is **exact** and the fix is mechanical - there is nothing to argue about, so leaving the box unmarked is false modesty
- the finding is **judged**, `ssot_direction` names a side, and the reviewer returned `confidence: high`

Do **not** recommend when:

- `ssot_direction` is `uncertain` - this is the case the whole conversation exists for
- the reviewer returned `confidence: low`, or the finding was escalated and the escalation disagreed
- the fix **deletes** anything
- the options are not comparable - one edits docs, another edits code, a third says both are fine. That is a question about intent, and only the user holds it

An unmarked box is a legitimate, common output. A recommendation on every finding trains the user to accept the first option without reading, which costs more than it saves the first time dovetail is confidently wrong.

### Rendering an exact finding

Terse. There is nothing to argue about.

```
[1/9] flag_drift · high · exact
README.md:40 documents --out, but the script has no such flag.

  README.md:40          `--out FILE      write the report here`
  scripts/run.py:12     add_argument("--output", help="write the report here")

Fix: rename the flag in README.md:40 to --output
  - `--out FILE      write the report here`
  + `--output FILE   write the report here`
```

Then the box:

```
header    flag_drift
question  Rename --out to --output in README.md:40?
options   Apply the fix (Recommended)
                              scripts/run.py:12 is the only definition of the
                              flag, so the doc is the side that is wrong.
          Skip for now        Stays in the queue and comes back next run.
          Mark intentional    Recorded in the ledger. Never surfaces again.
```

Where a batch class is live, the first finding of that class gets a fourth option - `Fix all 9 in this class` - with the combined diff shown above the box.

An option that records a permanent ledger entry must carry the reason with it. Where the reason is obvious from the repository, put it in the option label (`Intentional - bundle copies are meant to duplicate`). Where it is not, offer plain `Mark intentional` and ask why in a single follow-up box. Never invent a reason to avoid the follow-up: a ledger full of guessed justifications is worse than one with gaps.

### Rendering a judged finding

Show the model and confidence. Here the options **are** the candidate resolutions, not `fix`/`skip` - the right answer is not knowable from the text alone, which is the whole reason this is a conversation and not a report.

```
[3/9] contradiction · high · judged (opus, high confidence)
Two documents disagree about the request timeout.

  README.md:88          "requests time out after 30 seconds"
  docs/config.md:24     "the default timeout is 60s"
  src/client.py:31      TIMEOUT = 30          ← code agrees with README

Blast radius: 2 further docs cross-reference this value
  docs/ja/config.md:24 · docs/troubleshooting.md:112
```

Then the box:

```
header    contradictn
question  The code says 30. Which is right?
options   config.md is stale (Recommended)
                                README.md and src/client.py:31 both say 30, and
                                config.md:24 is the older of the two documents.
                                Change it to 30s, plus the 2 docs that cite it.
          The code is wrong     60s is intended. Change src/client.py:31.
          Both are correct      Different timeouts, badly named. Record why.
```

The recommendation is earned here: `ssot_direction` names the stale side, two independent sources agree against it, and the reviewer returned high confidence. Strip the mark and the ordering the moment any of those three fails - a contradiction where the code is silent and both documents are the same age gets an unmarked, genuinely open box.

"Other" already covers "something else - tell me", so it never occupies an option.

Never present a guess as a decision, and never let the option order imply a verdict the evidence does not support.

### Actions

| Action | Reached by | Effect |
|---|---|---|
| `fix` | option | apply the proposed edit |
| `skip` | option | defer within this run |
| `intentional <reason>` / `wontfix <reason>` | option | append to the ledger; never surfaces again |
| `all <category>` | option, on the first finding of the class | batch-approve a class (see below) |
| resolution 1..n | option, on a judged finding | apply that resolution |
| `edit` | Other | user describes a different fix; apply that |
| `explain` | Other | graph neighbourhood, git history, reviewer reasoning |
| `quit` | Other | stop; say how many remain |

The four options in any one box are chosen for that finding. A finding with no mechanical fix has no `fix` option; a judged finding offers resolutions instead. Do not pad a box to four options for symmetry.

Record a decision:

```bash
python3 -c "import sys; sys.path.insert(0, '${CLAUDE_SKILL_DIR}/scripts'); \
from store import append_decision; \
append_decision('<repo-path>', {'id':'<finding id>','verdict':'intentional','reason':'<why>','at':'<YYYY-MM-DD>','summary':'<one line>'})"
```

Always fill `summary`. It is redundant to the machine and load-bearing for the human: without it the ledger is an unreadable list of hashes and nobody can audit their own past decisions.

### Cascade

After each applied fix, **re-run the scan**. It is Python, so it is free. Drop findings the fix resolved and say so:

```
✓ applied. 2 queued findings resolved by this fix
  (docs/ja/config.md:24, docs/troubleshooting.md:112) - 4 remaining.
```

Without this the loop is whack-a-mole. With it, fixing a root cause visibly shrinks the queue - the difference between a session that finishes and one that gets abandoned.

### Batch-approve

`all <category>` applies every finding in a class with exactly one mechanically correct fix - a relative link where precisely one file matches the basename, an anchor where precisely one heading slugifies to it. Print the combined diff, then offer it as an option on the first finding of that class. One box, one confirmation, the whole class.

**Never batch-eligible:**

- `ssot_direction` is `uncertain`
- a choice exists
- `source` is a judgement reviewer
- the fix deletes anything

Deletions are always individual and always confirmed. Eligibility is a property of the finding, not a judgement made in the moment.

## Write safety

**Non-negotiable. Read before the first edit.**

1. If the target is not a git repository, **refuse to write at all**. There is no undo without git.
2. Capture `git status --porcelain` before the first write.
3. Re-check after each applied fix. If anything changed that dovetail did not apply, **stop the run and report it** - something else is writing to the tree, and continuing risks conflicting edits.
4. Only ever apply a fix the user approved. Never batch something ineligible. Never fix "while you are in there".

The scan itself never writes. Only the triage loop does, and only on approval.

## Degradation

Everything degrades; nothing crashes.

- A reviewer that errors or returns malformed output → named in the header, run continues
- A `.dovetail/checks/` plugin that raises → named in `failed_checks`, skipped
- git unavailable → co-change and TODO age skipped, everything else runs
- `--since` against an unresolvable ref → **exit 2, loudly**. A check that reports success because it could not run is worse than no check

## CI

Two workflow templates in `${CLAUDE_SKILL_DIR}/ci/`, for copying into the user's own repository:

- `dovetail-pr.yml` - per pull request, deterministic only, no model, no key. `--since` scopes findings to the diff so a repo with existing debt can adopt it. Safe to fail a build on.
- `dovetail-scheduled.yml` - weekly, deterministic plus judgement, upserts one tracking issue. **Never fails the build.**

`.dovetail/decisions.jsonl` is committed, so CI honours dismissals for free: a finding marked `intentional` during triage does not come back to block a colleague's PR.

## Reference

`references/finding-schema.md` - the contract reviewers satisfy
`references/reviewers/*.md` - one rubric per reviewer
`references/config.md` - `.dovetail/config.toml`

## Requirements

Python 3.11 or newer, and `git`. No API key, no network, no third-party packages for the deterministic layer. The judgement layer needs a model; everything else runs without one.
