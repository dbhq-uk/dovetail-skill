# Design notes

Why dovetail is shaped the way it is. The code says what it does; this says why.

## The name

A dovetail is the joint where two pieces interlock so precisely they cannot pull apart - and "does that dovetail?" is already the English idiom for "do those two things agree?" That is the whole tool in one word: it asks whether the parts of a repository still agree with each other.

## Deterministic first

dovetail has two layers, and the exact layer comes first. Every check in it is deterministic. It walks the repository, builds a typed reference graph, and reports only findings that follow from the structure - a link that resolves to nothing, a heading anchor that no longer exists, a file nothing points at.

That constraint is doing real work. A checker that produces false positives gets switched off within a week, because the cost of triaging noise exceeds the cost of the drift it finds.

Deterministic is not the same as certain, though. A link to nothing is broken on every reader's screen. A file nothing links to may be an entry point, and two files that stopped changing together may simply be finished. Both are computed exactly and both can be wrong about intent. On real repositories the second kind went red often enough that owners switched those checks off, and a switched-off check catches nothing. So every exact finding carries a tier. **Proven** findings follow from the structure alone and can **fail a build**, which is what makes the pull-request template in `skills/dovetail/ci/` safe to adopt. **Heuristic** findings are reported but do not gate, unless the repository opts a check in with `[gate]`, because only its owners know whether an orphan there is a mistake.

It also means the scan costs nothing and takes seconds: no model calls, no API key, no network. You can run it on every pull request without thinking about the bill.

The judgement layer is the other half, and it does call models. It is kept apart so it cannot weaken the first: its findings are labelled judged wherever they appear, and they never gate a build. [The judgement layer](#the-judgement-layer) below says how it is kept honest.

## The scan reports; you decide the fix

The scan never modifies the repository it is scanning. It reads the decisions ledger and never writes it; the only file it writes anywhere is the GitHub step summary, and only when CI provides `$GITHUB_STEP_SUMMARY`.

Fixes happen in the triage loop, one finding at a time, and only after you choose one. The line sits where certainty ends. The mechanical half of coherence - *what disagrees* - is decidable, and that is what a deterministic scan is good at. The other half - *which side is right* - usually is not. A broken link might mean the link is wrong or that the target was deleted in error, and nothing in the file tree distinguishes those. Guessing produces confident, wrong edits in exactly the documents people trust most.

So the tool shows you the finding and the evidence lines, offers the fixes, and edits only what you approve. Before each edit it checks that nothing else has changed the tree, and it will not write at all outside a git repository, where there is no undo.

## A reference graph, not a text search

Coherence checking by grep gets the easy cases and quietly misses the rest. dovetail instead builds a typed graph of what refers to what - markdown links, heading anchors, image and asset references, code imports resolved per language - and asks questions of the graph.

The difference shows up in both directions. Grep produces false positives, because a string that looks like a path in prose is not a reference. It also produces false negatives, because a relative link, an anchor and an import are three different kinds of edge with three different resolution rules, and one pattern cannot express all of them.

## Decisions are committed, not remembered

Some findings are intentional. A duplicated file is sometimes a deliberate vendored copy; an orphan is sometimes an entry point nothing links to by design.

Rather than a local ignore list that every contributor rebuilds and CI never sees, dovetail records suppressions in `.dovetail/decisions.jsonl` in the target repository, keyed by a content fingerprint of the finding:

```jsonl
{"id":"sha256:...","verdict":"intentional","reason":"why","at":"2026-07-28","summary":"human-readable echo"}
```

The triage loop appends a line when you mark a finding intentional, and you can append one yourself. The scan only ever reads it. Because it is committed, a judgement made once applies to everyone and to CI. Because the key is a fingerprint of the finding rather than a line number, it survives edits that push the finding down the file - but *not* the finding materially changing, which is the behaviour you want: if the thing you approved has become a different thing, it should surface again. The file's path is part of the fingerprint, so a move or a rename does change the key. The scan cannot follow a decision to a new path without guessing, so it reports the old row under `stale_decisions` instead, and the user re-records it. A decision that stops matching is counted, never silently ignored.

The `summary` field is redundant to the machine and load-bearing for the human: without it, the ledger is an unreadable list of hashes and nobody can audit their own past decisions.

## `--since`, so a repository can adopt it

A repository with existing drift cannot turn on a whole-repository check without every unrelated pull request going red, so nobody turns it on at all.

`--since <ref>` scopes findings to files the change actually touches. Existing debt stays visible in a scheduled whole-repository run, while the per-PR job only holds you responsible for what you changed. That is what makes adoption possible on a real codebase rather than only on a new one.

`--since` resolves the base ref from history, so it fails loudly (exit 2) on a shallow clone rather than passing silently. A check that reports success because it could not run is worse than no check.

## Python 3.11+, standard library only

No third-party dependencies at all, so there is nothing to install, no virtualenv, no lockfile to drift, and no supply chain beyond the interpreter. 3.11 is the floor.

The floor is `tomllib`, and it binds the interpreter that *runs* dovetail - not the one `python3` happens to name. Those come apart more often than they should: Debian and Ubuntu carry 3.12 alongside a 3.10 default, so a machine can have everything dovetail needs and still fail every documented command with `ModuleNotFoundError: tomllib`.

[`bootstrap.py`](../skills/dovetail/scripts/bootstrap.py) closes that gap by re-executing the original command line under the newest suitable interpreter on `PATH`. The two alternatives were both worse. Rewriting `python3` in SKILL.md at install time would break the live-symlink install, which is the property that lets an edit to SKILL.md take effect without reinstalling. Asking the user to repoint `python3` is a global change to their machine to satisfy one skill, and it strands whatever was pinned to the older interpreter - on a typical box that is every `pip install --user` package they already have.

Re-exec keeps both properties: the host is untouched, and SKILL.md stays literal about what it runs. When no suitable interpreter exists it exits 2 naming the fix, because a checker that reports success when it could not run is the failure mode this tool exists to avoid.

## The judgement layer

Everything above describes the deterministic half. The other half is six
reviewers, and the design rule governing them is a single sentence: **nothing
reaches a model that Python can compute exactly.**

That is why each rubric names the categories it must not report. A reviewer
restating a check the exact layer already performed is offering a guess where
there was a certainty, and it costs money to do it.

### Sharding is not an optimisation

Handing a reviewer a whole repository and one turn budget does not get the
repository reviewed. It gets a few files read and the rest skipped in silence -
and the output looks identical either way, which is what makes it dangerous.

This was measured rather than assumed. On a 474-file repository each reviewer
was receiving 172 files in one prompt and the judgement layer produced 24
findings. Sharded into batches of 20, the same reviewers on the same repository
produced 149. Nothing else changed.

### Extraction and adjudication are different jobs

Reading files is high-volume and near-mechanical; deciding whether two claims
conflict is low-volume and high-judgement. Splitting them is what lets the
expensive model see a handful of candidate clusters instead of a corpus, and it
is why `claimscan.py` exists at all.

### Validation is the boundary, not a formality

A reviewer's output arrives from a model, so it is checked before it is
believed:

- the category must not be one the deterministic layer owns
- a contradiction must carry evidence from both sides
- **every quote must appear at the line it cites**

The last one is the important one. A model that invents a plausible quote at a
plausible line produces a finding indistinguishable from a true one, and it
fired on the very first live run against a real repository.

An unsound finding is dropped and named; the reviewer's other findings survive.
The original design discarded the whole batch, on the theory that a reviewer
producing one bad finding could not be trusted at all. That cost every finding
from a reviewer whose remaining output was sound, and silently losing good
findings is the worse failure.

