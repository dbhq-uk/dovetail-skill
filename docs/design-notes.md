# Design notes

Why dovetail is shaped the way it is. The code says what it does; this says why.

## The name

A dovetail is the joint where two pieces interlock so precisely they cannot pull apart - and "does that dovetail?" is already the English idiom for "do those two things agree?" That is the whole tool in one word: it asks whether the parts of a repository still agree with each other.

## Deterministic first, and only deterministic

Every check dovetail ships is deterministic. It walks the repository, builds a typed reference graph, and reports only findings that follow from the structure - a link that resolves to nothing, a heading anchor that no longer exists, a file nothing points at.

That constraint is doing real work. A checker that produces false positives gets switched off within a week, because the cost of triaging noise exceeds the cost of the drift it finds. Deterministic findings can be trusted enough to **fail a build on**, which is what makes the CI template in `skills/dovetail/ci/` safe to adopt.

It also means the scan costs nothing and takes seconds: no model calls, no API key, no network. You can run it on every pull request without thinking about the bill.

## It reports; it does not fix

dovetail never modifies the repository it is scanning. The scan reads the decisions ledger and never writes it; the only file it writes anywhere is the GitHub step summary, and only when CI provides `$GITHUB_STEP_SUMMARY`.

This is deliberate rather than unfinished. The mechanical half of coherence - *what disagrees* - is decidable, and that is what a deterministic scan is good at. The other half - *which side is right* - usually is not. A broken link might mean the link is wrong or that the target was deleted in error, and nothing in the file tree distinguishes those. Guessing produces confident, wrong edits in exactly the documents people trust most.

So the tool draws the line where its certainty ends. It tells you the finding and the evidence lines; you decide.

## A reference graph, not a text search

Coherence checking by grep gets the easy cases and quietly misses the rest. dovetail instead builds a typed graph of what refers to what - markdown links, heading anchors, image and asset references, code imports resolved per language - and asks questions of the graph.

The difference shows up in both directions. Grep produces false positives, because a string that looks like a path in prose is not a reference. It also produces false negatives, because a relative link, an anchor and an import are three different kinds of edge with three different resolution rules, and one pattern cannot express all of them.

## Decisions are committed, not remembered

Some findings are intentional. A duplicated file is sometimes a deliberate vendored copy; an orphan is sometimes an entry point nothing links to by design.

Rather than a local ignore list that every contributor rebuilds and CI never sees, dovetail records suppressions in `.dovetail/decisions.jsonl` in the target repository, keyed by a content fingerprint of the finding:

```jsonl
{"id":"sha256:...","verdict":"intentional","reason":"why","at":"2026-07-28","summary":"human-readable echo"}
```

You append to this file yourself - consistent with the tool never writing to your repository. Because it is committed, a judgement made once applies to everyone and to CI. Because the key is a fingerprint of the finding rather than a line number, it survives the file moving - but *not* the finding materially changing, which is the behaviour you want: if the thing you approved has become a different thing, it should surface again.

The `summary` field is redundant to the machine and load-bearing for the human: without it, the ledger is an unreadable list of hashes and nobody can audit their own past decisions.

## `--since`, so a repository can adopt it

A repository with existing drift cannot turn on a whole-repository check without every unrelated pull request going red, so nobody turns it on at all.

`--since <ref>` scopes findings to files the change actually touches. Existing debt stays visible in a scheduled whole-repository run, while the per-PR job only holds you responsible for what you changed. That is what makes adoption possible on a real codebase rather than only on a new one.

`--since` resolves the base ref from history, so it fails loudly (exit 2) on a shallow clone rather than passing silently. A check that reports success because it could not run is worse than no check.

## Python 3.11+, standard library only

No third-party dependencies at all, so there is nothing to install, no virtualenv, no lockfile to drift, and no supply chain beyond the interpreter. 3.11 is the floor.

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

