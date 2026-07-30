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
rather than raised, so one odd entry cannot fail a whole scan.

**`refgraph`** builds a typed graph over those files. Every edge records where it came from
(`src`, `line`), how it was written, and what it resolved to. Markdown links, heading anchors,
image and asset references, and code imports resolved per language are four different kinds of
edge with four different resolution rules - which is exactly why grep cannot do this job in
either direction. A string that looks like a path in prose is a false positive; a relative
link, an anchor and an import are three things one pattern cannot express.

**The checks** run in a fixed order, each taking `(inventory, graph)`. A check that raises is
caught and named in `failed_checks` rather than taking down the run. Repo-local plugins run
last, so they can rely on everything above having completed.

**Suppression** drops findings whose fingerprint appears in the committed decisions ledger,
and reports the count. Then findings are sorted by severity, category, and first evidence
file, and printed as JSON or as GitHub annotations.

`--since` filters between the checks and suppression: a finding survives only if some evidence
item names a file changed since the ref.

## Two supporting details

**Slugs.** `slugify` implements GitHub's heading-anchor algorithm, because GitHub is what
actually renders the documents. An anchor checker that uses a *reasonable* slug algorithm
reports links that work perfectly well.

**Globs.** `globmatch` exists because Python's `fnmatch` treats `**` as `*`, which would make
`vendor/**` match across path separators incorrectly. Ignore patterns behave the way you
expect them to.

**Fingerprints.** `store.fingerprint` hashes the category, the sorted file set, and a
normalised claim - through `json.dumps` rather than string concatenation, so a filename
containing a delimiter cannot collapse two different findings onto one key. Line numbers are
excluded on purpose: a finding must keep its identity when unrelated edits move it down the
file.

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
not be trusted at all - which silently lost good findings, the worse failure.

## Two dispatch paths, one contract

Reviewers are dispatched two ways: in-session subagents driven by `SKILL.md`, and the headless
`ci_dispatch.py` used by the scheduled job. Both validate against the same written contract in
`references/finding-schema.md`, and both read the roster and tiering from `reviewer.py`.

A schema change that broke one path while the other stayed green is precisely what having one
written contract prevents. The same instinct produced the repo-local check that asserts
`SKILL.md`'s model table still matches the roster declared in code.

## Write safety

The scan has no write path into the target repository at all. Fixes happen only in the triage
loop, only one at a time, and only on approval - and the loop captures `git status --porcelain`
before the first write and re-checks after each fix. If anything changed that dovetail did not
apply, the run stops: something else is writing to the tree, and continuing risks conflicting
edits.

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
