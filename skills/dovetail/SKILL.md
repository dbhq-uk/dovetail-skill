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
  – suppressed     3 by prior decisions

Starting with the 9 that are certain. More will join as reviewers land.
```

Always show the exact/judgement split, always show the suppressed count. Nothing is ever hidden silently. Name any failed check or reviewer: `⚠ staleness failed - findings incomplete`.

## Triage

Order by **blast radius, then severity, then confidence**. Root causes before symptoms, so fixing one visibly shrinks the queue.

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

fix · edit · skip · intentional <why> · explain · quit
```

### Rendering a judged finding

Show the model and confidence. Contradictions **end in a question**, because the right answer is not knowable from the text alone - that is the whole reason this is a conversation and not a report.

```
[3/9] contradiction · high · judged (opus, high confidence)
Two documents disagree about the request timeout.

  README.md:88          "requests time out after 30 seconds"
  docs/config.md:24     "the default timeout is 60s"
  src/client.py:31      TIMEOUT = 30          ← code agrees with README

Blast radius: 2 further docs cross-reference this value
  docs/ja/config.md:24 · docs/troubleshooting.md:112

The code says 30. Which is it?
  1  config.md is stale → change it to 30s (and the 2 docs above)
  2  the code is wrong → 60s is intended, change src/client.py
  3  both correct - different timeouts, badly named → record why
  4  something else - tell me
```

Never present a guess as a decision. Where `ssot_direction` is `uncertain`, ask.

### Actions

| Action | Effect |
|---|---|
| `fix` | apply the proposed edit |
| `edit` | user describes a different fix; apply that |
| `skip` | defer within this run |
| `intentional <reason>` / `wontfix <reason>` | append to the ledger; never surfaces again |
| `explain` | graph neighbourhood, git history, reviewer reasoning |
| `all <category>` | batch-approve a class (see below) |
| `quit` | stop; say how many remain |

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

`all <category>` applies every finding in a class with exactly one mechanically correct fix - a relative link where precisely one file matches the basename, an anchor where precisely one heading slugifies to it. Show the combined diff, confirm once.

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
