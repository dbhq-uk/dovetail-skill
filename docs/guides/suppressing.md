# Suppressing a finding

Some findings are intentional. A vendored copy is meant to duplicate its original; an entry
point nothing links to is meant to be an orphan; a fictional path in a worked example is meant
not to exist. Those need recording once, not re-deciding every run.

## The ledger

Decisions live in `.dovetail/decisions.jsonl` in the repository being scanned, one JSON object
per line:

```jsonl
{"at":"2026-07-30","id":"sha256:190e815b…","reason":"Vendored copy, kept in sync deliberately","summary":"vendor/parser.js duplicates src/parser.js","verdict":"intentional"}
```

| Field | Purpose |
|---|---|
| `id` | The finding's fingerprint, from the scan output |
| `verdict` | `intentional` or `wontfix` |
| `reason` | Why. For the next person, including future you |
| `at` | `YYYY-MM-DD` |
| `summary` | Human-readable echo of what the finding was |

**Commit it.** That is the whole point: a judgement made once applies to your colleagues and
to CI, so a finding you have accepted never comes back to block someone else's pull request.
A local ignore list gives you none of that.

**You append it yourself.** The scan has no write path into your repository at all. During a
triage session dovetail offers to add the line for you, and that is the only circumstance in
which it writes.

Later lines override earlier ones for the same `id`, so changing your mind means appending,
not editing. A malformed line is skipped rather than fatal - a hand-edited ledger cannot break
a scan.

## Always fill in `summary`

It is redundant to the machine and load-bearing for the human. Without it the ledger is an
unreadable list of hashes and nobody - including whoever wrote it - can audit their own past
decisions. A ledger you cannot read is one you cannot revisit, and then dismissals accumulate
unchallenged.

The same goes for `reason`. If the reason is not obvious from the repository, dovetail asks
for it rather than inventing one, because a ledger full of guessed justifications is worse than
one with gaps.

## What the fingerprint is, and is not

The `id` is a SHA-256 over the finding's category, its files, and a normalised form of the
claim. Line numbers are deliberately excluded.

That gives you the behaviour you want in both directions:

- **The file moves, or unrelated edits push the finding down the page** - same fingerprint, so
  the decision keeps suppressing it.
- **The finding materially changes** - different fingerprint, so it surfaces again. If what
  you approved has become something else, you should be asked about it.

You cannot suppress a category wholesale from the ledger, by design. Turning off a whole check
is a configuration decision, not a per-finding one - see [configuring a
repository](configuring.md).

## Suppressed findings are counted, never hidden

Every run reports how many findings the ledger suppressed:

```
  - suppressed     3 by prior decisions
```

A tool that silently drops findings teaches you to trust a number that is not the whole
number. If that count starts climbing, the ledger is worth re-reading.

## When not to use it

Reach for something else when the finding is:

- **A whole check you do not want** - disable it in `[checks]` rather than suppressing every
  instance
- **A directory that should never be scanned** - use `ignore` globs
- **A repeated pattern with a real rule behind it** - write a [repo-local
  check](custom-checks.md) that encodes the rule, so the exception becomes explicit
