# How a scan works

The mechanics: what runs, in what order, and where each part's certainty ends. For *why* the
tool is shaped this way, read the [design notes](design-notes.md).

## The two layers

```
                       ┌─ discover ─── inventory (one entry per file)
  repository ──────────┤
                       └─ refgraph ─── typed reference graph (edges, inbound, headings)
                                 │
                                 ├── graphcheck    broken links, anchors, orphans, duplicates
       LAYER 1        deterministic   exactcheck   flags, signatures, versions, dead code
       Python, seconds,  checks       convcheck    the repo's own stated conventions
       no model               │       cochange     git-history signals
                              │       plugins      .dovetail/checks/*.py
                              │
                       ┌──────┴──────┐
                       │  suppression│  .dovetail/decisions.jsonl
                       └──────┬──────┘
                              │
                          findings ──── json | github annotations
                              │
       LAYER 2                ├── claimscan ─── candidate clusters
       models, minutes,       └── reviewers ─── contradiction, staleness, spec-flow,
       metered                                  convention, code-hygiene, xref
                                          │
                                     validation ─── every quote checked against the line
```

Layer 1 finishes before the first reviewer returns. That ordering is the reason a run
abandoned after two minutes has still delivered every broken link and duplicate in the
repository.

## Layer 1, step by step

**`discover`** lists tracked files via git, applies the ignore globs, and records one entry
per file: path, modality and category from `classify`, size, SHA-256, and last commit time.
Entries that cannot be read as files - broken symlinks, submodule gitlinks - are skipped
rather than raised, so one odd entry cannot fail a whole scan. It reads every file once, to
hash it, and keeps the text of each text file in a cache (`textcache`) that every later step
reads from. Before that, one scan read each file about eight times.

**`refgraph`** builds a typed graph over those files. Every edge records where it came from
(`src`, `line`), how it was written, and what it resolved to. Markdown links, heading anchors,
image and asset references, and code imports resolved per language are four different kinds of
edge with four different resolution rules - which is exactly why grep cannot do this job in
either direction. A string that looks like a path in prose is a false positive; a relative
link, an anchor and an import are three things one pattern cannot express.

**The checks** run in a fixed order, each taking `(inventory, graph)`. A check that raises is
caught and named in `failed_checks` rather than taking down the run. Repo-local plugins run
last, so they can rely on everything above having completed. Each finding is stamped with the
check that made it and its tier, proven or heuristic. The seconds each step took go into
`timings`, so a slow check is visible.

**No check compares every file with every other.** `near_duplicates` finds its candidate pairs
by prefix filtering: each file's word shingles go in one order, rarest first, and two files are
compared only if the start of each list shares a shingle. That finds every pair the Jaccard
floor would keep, and no others need comparing. `dead_python_code` builds one index of the
words in each file and looks each definition up in it, rather than searching every file once
per definition. Both give exactly the findings the pairwise versions gave. On a generated
repository the whole scan took about 1.3 seconds of CPU for 1,000 files and about 4 for 3,000.
Before, those two checks alone took over two minutes on 1,000 files, and 3,000 did not finish.

**Triage facts** come next, from `fixes`. A broken link, a dangling anchor or a documented flag
with exactly one candidate target gets a `fix`: a unified diff against the file as it is on
disk. Every finding gets a `blast_radius`, the other files that cite a file in its evidence,
from the graph's inbound edges, and `batch_eligible` when its fix is mechanical and deletes
nothing. The triage loop orders and batches by these fields, so two runs on one repository
triage the same way.

**Suppression** drops findings whose fingerprint appears in the committed decisions ledger,
and reports the count. A ledger row that matches no finding the checks produced is listed in
`stale_decisions`, before `--since` narrows anything, so a moved file shows up as a stale row
rather than as a decision that silently stopped working. The ledger itself is left out of the
inventory's `files`: each row's summary names a file, and read as text it counted as a
reference to it. Then findings are sorted by severity, category, and first evidence
file, and printed as JSON or as GitHub annotations.

`--since` filters between the checks and suppression: a finding survives only if some evidence
item names a file changed since the ref, or, for a broken link or dangling anchor, the file the
link points at changed. The target matters because the change that breaks a link is usually a
delete, a rename or a heading edit in the target, and the linking file is untouched. Deleted
and renamed paths count, and paths come from `git diff --relative`, so a subdirectory scan
matches its own paths rather than silently matching none.

## Two supporting details

**Slugs.** `slugify` implements GitHub's heading-anchor algorithm, because GitHub is what
actually renders the documents. An anchor checker that uses a *reasonable* slug algorithm
reports links that work perfectly well. The id comes from the heading's rendered text (link
text without its URL, code without its backticks), keeps leading and trailing hyphens, and
covers setext and HTML headings. `id` and `name` attributes count as anchors too, a fragment
is percent-decoded before it is compared, and `#top` and line anchors such as `#L10` always
exist. The rules are tested against ids GitHub itself rendered, kept in
`skills/dovetail/tests/fixtures/github_anchors.json`.

**Globs.** `globmatch` exists because Python's `fnmatch` treats `**` as `*`, which would make
`vendor/**` match across path separators incorrectly. Ignore patterns behave the way you
expect them to.

**Fingerprints.** `store.fingerprint` hashes the category, the sorted file set, and a
normalised claim - through `json.dumps` rather than string concatenation, so a filename
containing a delimiter cannot collapse two different findings onto one key. Line numbers are
excluded on purpose: a finding must keep its identity when unrelated edits move it down the
file. File paths are not, so a move changes the id; that is what `stale_decisions` reports.

## Layer 2, and why it is shaped this way

Six reviewers, each given only what Python could not settle. Every rubric names the categories
it must not report, because a reviewer restating a deterministic check is offering a guess
where there was a certainty - and charging for it.

**Sharding is not an optimisation.** Handing a reviewer a whole repository and one turn budget
does not get the repository reviewed; it gets a few files read and the rest skipped in silence,
and the output looks identical either way. On a 474-file repository, each reviewer receiving
172 files in one prompt produced 24 findings across the judgement layer. Sharded into batches
of 20, the same reviewers on the same repository produced 149. Nothing else changed.

**Extraction and adjudication are different jobs.** Reading files is high-volume and near
mechanical; deciding whether two claims conflict is low-volume and high-judgement. `claimscan`
does the first and hands the second a handful of candidate clusters rather than a corpus,
which is what lets the expensive model be used where it earns its cost.

**Validation is the boundary.** Output arrives from a model, so it is checked before it is
believed - and the load-bearing rule is that every quote must appear at the line it cites. A
model inventing a plausible quote at a plausible line produces a finding indistinguishable
from a true one, and it happened on the very first live run against a real repository.

An unsound finding is dropped and named; the reviewer's other findings survive. The original
design discarded the whole batch on the theory that a reviewer producing one bad finding could
not be trusted at all - which silently lost good findings, the worse failure. Only output that
is not a JSON array at all fails the whole reviewer, and it is named as failed.

A quote that is not at its line is not always invented. dovetail edits files during its own
triage loop, so a quote may have moved (kept, line corrected) or been edited away since the
reviewer read it (dropped as stale, found by reading the committed file). Evidence that cannot
be checked at all - an empty quote, a missing or unreadable file, a path outside the
repository - counts as fabricated, never as a pass.

## Two dispatch paths, one contract

Reviewers are dispatched two ways: in-session subagents driven by `SKILL.md`, and the headless
`ci_dispatch.py` used by the scheduled job. Both take their shards from one function,
`ci_dispatch.plan_shards`, so the same repository gets the same batches, the same prompts and
the same models either way. Interactively, `dovetail.py prepare-review` writes each shard to a
prompt file for a subagent, and `dovetail.py import-review` validates what the subagents write
back. Both retry unparseable output once and escalate with the same prompt. Both validate
against the same written contract in `references/finding-schema.md`, and both read the roster
and tiering from `reviewer.py`. A contract test runs both paths on one repository and asserts
they send the same prompts to the same models.

A schema change that broke one path while the other stayed green is precisely what having one
written contract prevents. The same instinct produced the repo-local check that asserts
`SKILL.md`'s model table still matches the roster declared in code.

## Write safety

The scan has no write path into the target repository at all. Fixes happen only in the triage
loop, only one at a time, and only on approval. `dovetail.py scan` snapshots a content hash of
every file git can see, and the loop compares against it before each fix and after it. If
anything changed that dovetail did not apply, the run stops: something else is writing to the
tree, and continuing risks conflicting edits.

It hashes content rather than comparing `git status --porcelain`, because porcelain cannot see a
second edit to a file that is already modified. The line reads ` M file` before and after.

If the target is not a git repository, dovetail refuses to write at all. There is no undo
without git.

## Everything degrades, nothing crashes

| What fails | What happens |
|---|---|
| A reviewer errors or returns malformed output | Named in the header, run continues |
| A `.dovetail/checks/` plugin raises | Named in `failed_checks`, skipped |
| A built-in check raises | Named in `failed_checks`; exits `1` if `--fail-on` is set |
| git is unavailable | Co-change and TODO age skipped, everything else runs |
| `--since` cannot resolve its ref | **Exit 2, loudly** |

The last row is the exception, and it is the right one. A check that reports success because
it could not run is worse than no check.
