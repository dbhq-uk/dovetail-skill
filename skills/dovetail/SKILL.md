---
name: dovetail
description: Check whether a repository agrees with itself, then work through the findings one at a time. Finds broken internal links, dangling heading anchors, orphaned files, duplicate content, translations that have fallen behind, drift between docs and code, contradictions between documents, and conventions the repo states but does not follow. Not for code review or security audits, and it checks external URLs only when asked. Trigger on phrases like "dovetail", "does this repo agree with itself", "find contradictions in the docs", "repo coherence", "docs drift", "check the docs for broken links".
---

# dovetail

Checks whether a repository agrees with itself, and walks through what it finds.

Two layers produce findings. **Exact** findings are computed in Python - links, anchors, orphans, duplicates, flag and signature drift, conventions, git-history signals. They come in two tiers. **Proven** ones follow from the structure alone, such as a link to nothing. **Heuristic** ones are likely problems that intent can explain, such as a file nothing links to. `next` prints the tier. **Judged** findings come from reviewers - contradictions, semantic staleness, spec drift, non-Python dead code. They are probabilistic.

The user must always know which they are looking at. Never blur the two.

**What it does not check.** Only links inside the repository are resolved: external URLs are skipped unless the user asks for `--external-links`. Links are read from `.md` and `.markdown` files, so `.mdx` pages are not checked. Say so if the user asks for either.

## Running outside Claude Code

The commands below use `${CLAUDE_SKILL_DIR}`. Claude Code sets it, and `install-codex.sh` writes the real path in its place. If it is neither set nor replaced, use the directory this `SKILL.md` is in.

- **No question box** (Codex, or an agent installed through skills.sh): ask in plain text instead. Print the same header, question and numbered options, one finding per message, then stop and wait for the answer.
- **No model choice per subagent:** run each shard on the model you have, and say in the header that the reviewer tiering was not applied.
- **No background subagents:** skip the reviewers and say the run is exact only.

## How a run is driven

One script drives the whole run and keeps its state on disk, in `~/.dbhq/dovetail/`. Each verb prints only what the next step needs:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dovetail.py <verb> --repo <repo-path>
```

**Never read the scan JSON, the clusters or a shard file yourself.** Everything you need arrives through the verbs.

### 1. Scan (always)

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dovetail.py scan --repo <repo-path>
```

Run it in the foreground; it takes seconds. It starts a new run, prints a summary of about eight lines (counts, failed checks, suppressed findings, stale decisions, findings by category) and snapshots every file for write safety. `--since REF`, `--ignore GLOB` and `--no-plugins` work as they do for `scan.py`. Pass `--no-plugins` when the user does not trust the repository: `.dovetail/checks/*.py` is code from it, and runs on every scan. Pass `--external-links` only when the user asks for external URLs to be checked: it runs lychee over the network, and exits `2` if lychee is not installed.

If it exits `2`, report the error and stop. git is not installed, the repository is not a git checkout, `.dovetail/config.toml` is invalid, or `--since` did not resolve. Do not carry on with defaults.

### 2. Start the reviewers (unless the user said "quick" or "exact only")

Start them **before** triage, so they land while the user works through the exact findings.

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dovetail.py prepare-review --repo <repo-path>
```

This writes one prompt file per shard: 20 files, or 25 contradiction clusters. Never merge shards. The user's profile goes here as `--profile cheap` or `--profile thorough`.

Then hand the shards out in waves:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dovetail.py wave --repo <repo-path>
```

A wave lists up to 4 shards (`--size 5` at most). For each one, spawn one background subagent with the model the wave names and the one-line prompt it prints. **Pass the model explicitly.** Never let a reviewer inherit yours. Never have more than one wave running.

| Reviewer | Model | Effort |
|---|---|---|
| `xref` | haiku | low |
| `convention` | sonnet | medium |
| `code-hygiene` | sonnet | medium |
| `contradiction` | opus | high |
| `staleness` | opus | high |
| `spec-flow` | opus | high |

Profiles: **cheap** drops every reviewer one tier and turns escalation off; **thorough** puts everything on opus/high. `.dovetail/config.toml` sets the durable default and per-reviewer overrides, which win.

When a wave's agents have finished, import what they wrote, then hand out the next wave:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dovetail.py import-review --repo <repo-path>
```

Every quote is checked against the file:

- at its line, or moved within the file: **kept**
- only in the committed file: **stale**, because dovetail's own fix rewrote the line
- in neither, or evidence that cannot be checked: **fabricated**

A bad finding is dropped and named; the rest of that shard is queued. A shard whose output is not a JSON array goes out once more in the next wave, with the contract restated. If that fails too, the shard has **failed** and its findings are missing. Low-confidence findings from haiku or sonnet come back in a later wave on opus, unless the profile is cheap.

**Report what was dropped, and say which kind.** Name a reviewer that fabricated in the header. Never present a filtered list as if it were complete.

### 3. Header

```
dovetail · <repo> · <file_count> files, <edge_count> references

  ✓ exact          9 findings   (2 high · 5 med · 2 low) · 6 proven, 3 heuristic
  ⋯ judgement      running - 107 shards, 4 out
  - suppressed     3 by prior decisions

Starting with the 9 exact findings. More will join as reviewers land.
```

Always show the exact/judgement split and the suppressed count. If the summary has a `stale` line, show it too: those decisions match no current finding, usually because a file moved. Name any failed check or shard: `⚠ staleness-03 failed - findings incomplete`.

## Triage

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dovetail.py next --repo <repo-path>
```

`next` prints the next finding in queue order: the size of its `blast_radius` (the other files that cite the file it is in), then severity, then confidence, exact before judged. The scan fills `blast_radius`, so never re-rank the queue yourself. Everything above the line `=== for the agent, not the user ===` is the finding, rendered as markdown. **Show it to the user exactly as printed, then the question box.** Nothing between them, nothing after, and never wrap the finding in a code block. Everything below the line is for you: the id, the box header, the options, whether one option may be recommended, whether the scan computed a `fix`, and whether the finding is `batch_eligible`.

`next --json` prints the raw finding, for `explain`.

### One finding, one question box

Ask for the decision with `AskUserQuestion`. The markdown carries the detail; the box carries the choice and nothing else. Never put two findings in one box, and never ask for a decision in prose when the box is available.

Every box:

- `header` - the one `next` prints (the category, cut to 12 characters)
- `question` - the decision itself, phrased so it can be answered without scrolling back up
- `multiSelect: false`
- two to four options, most likely first, each with a `description` saying what happens to the files
- **never add an "Other" option.** It is supplied automatically

`edit`, `intentional <reason>`, `explain` and `quit` arrive as free text through "Other". Act on what the user typed. Do not re-ask a question they have already answered.

### Recommending an option

`next` says whether a recommendation is allowed, and why. When it says `none`, mark nothing. When it says `allowed`, you may put that option **first** with `(Recommended)` on its label. At most one per box, ever.

A recommendation carries its grounds in the repository's own terms: which file is newer, which side the code agrees with, how many documents cite each value. "Best practice" is not grounds. For a judged finding, put the grounds in a **Why this side** block above the box.

Never recommend a fix that deletes anything, or between options that are not comparable: one edits docs, another edits code, a third says both are fine. Only the user can answer that. An unmarked box is a normal answer.

### Writing the box

For an exact finding the options are the actions. When `next` says the scan computed a fix, the diff above the line is that fix: apply exactly it. When it says none was computed, draft the edit and show it before applying it. For a judged one the options **are** the candidate resolutions, not fix and skip:

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

Keep option descriptions to what happens to the files. The evidence is already above the box.

An option that records a permanent ledger entry carries its reason. Where the reason is obvious from the repository, put it in the label (`Intentional - bundle copies are meant to duplicate`). Where it is not, offer plain `Mark intentional` and ask why in one follow-up box. Never invent a reason.

### Recording the decision

Every value is an argument, never code, so a reason may hold any quote mark. Quote it for the shell as you would any argument. Each command follows `python3 ${CLAUDE_SKILL_DIR}/scripts/dovetail.py`, and `next` prints the full line with the id filled in.

| Action | Command |
|---|---|
| skip for this run | `decide --repo <repo-path> <id> skip` |
| intentional or wontfix | `decide --repo <repo-path> <id> intentional --reason "<why>"` |
| fix, after applying it | `decide --repo <repo-path> <id> fix --files <every file the fix changed>` |
| a batch, after applying it | the `record` line `next --batch` prints: every id, then `fix --files` |
| `quit` | stop, and say how many remain (`next` shows `[k/N]`) |

`intentional` and `wontfix` append to `.dovetail/decisions.jsonl` and the finding never surfaces again. `--summary "<one line>"` overrides the default summary, which is the finding's problem sentence.

### Cascade

After each fix, rescan. It costs nothing:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dovetail.py rescan --repo <repo-path>
```

It says which queued findings the fix resolved, whether the fix resolved its own finding, and any new finding the fix introduced. Pass its line to the user.

### Batch-approve

A finding whose `batch_eligible` is true can be fixed together with the rest of its class. The scan sets it only for an exact finding with exactly one computed fix that deletes nothing, such as a link where exactly one file in the repository has the target's name. When `next` says a class is `batch_eligible`, run:

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dovetail.py next --batch --repo <repo-path>
```

It prints the combined diff, then the box header and options. Show the diff exactly as printed and ask once: one box, one confirmation, the whole class. If the user approves, apply every edit shown, then run the one `record` line it prints.

Never batch a finding whose `batch_eligible` is false. That covers a reviewer's finding, an uncertain `ssot_direction`, a finding with two or more candidate fixes, and any fix that deletes. Deletions are always individual and always confirmed.

## Write safety

**Non-negotiable. Read before the first edit.**

1. If the target is not a git repository, **refuse to write at all**. `scan` refuses to start there anyway.
2. Before each fix, run `check`. It compares a content hash of every file with the snapshot `scan` took.
3. If `check` exits `1`, or `decide ... fix` prints `STOP` and exits `3`, **stop the run and report it**. Something else is writing to the tree.
4. Only ever apply a fix the user approved. Never batch something ineligible. Never fix "while you are in there".

```bash
python3 ${CLAUDE_SKILL_DIR}/scripts/dovetail.py check --repo <repo-path>
```

The scan itself never writes. Only the triage loop does, and only on approval.

## Degradation

Everything degrades; nothing crashes.

- A shard that errors or returns malformed output → named, run continues
- A check or a `.dovetail/checks/` plugin that raises → named in the summary, and its findings are missing. Under `--fail-on` the scan exits `1`
- git not installed → **exit 2**, naming the missing binary. There is no scan without it
- `--since` against an unresolvable ref → **exit 2, loudly**

## Reference

`references/finding-schema.md` - the contract reviewers satisfy
`references/reviewers/*.md` - one rubric per reviewer
`references/config.md` - `.dovetail/config.toml`
`ci/` - workflow templates for the user's own CI: `dovetail-pr.yml` gates pull requests on proven findings, `dovetail-scheduled.yml` runs the reviewers weekly and never fails the build

## Requirements

Python 3.11 or newer, and `git`. No API key, no network, no third-party packages for the deterministic layer. The judgement layer needs a model; everything else runs without one. `--external-links` needs lychee and the network.
