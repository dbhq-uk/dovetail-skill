# References

Everything the judgement layer reads at dispatch time. Both dispatch paths load
these same files - the in-session subagents driven by [`../SKILL.md`](../SKILL.md),
and the headless [`../scripts/ci_dispatch.py`](../scripts/ci_dispatch.py) shim
used by the scheduled CI job.

They are listed here rather than only loaded by name because a file nothing
links to is indistinguishable from a file nobody needs - which is a finding
dovetail itself reports.

## The contract

- [`finding-schema.md`](finding-schema.md) - what every reviewer must return,
  and the two rules carried over from upkeep: do not assume which side is the
  source of truth, and the repository's own conventions outrank the rubric.
- [`config.md`](config.md) - `.dovetail/config.toml`, the deterministic check
  names, and how to write a repo-local check.

## Reviewers

One rubric each, ordered by model tier. Extraction is high-volume and near
mechanical, so it runs cheap; adjudication is low-volume and high-judgement, so
the expensive model sees clusters rather than a corpus.

| Rubric | Model | What is left after Python has run |
|---|---|---|
| [`claim-extract.md`](reviewers/claim-extract.md) | haiku | Extract factual claims; feeds `contradiction` |
| [`xref.md`](reviewers/xref.md) | haiku | Rank missing cross-references against a high bar |
| [`convention.md`](reviewers/convention.md) | sonnet | The repo's own stated rules, where Python cannot check them |
| [`code-hygiene.md`](reviewers/code-hygiene.md) | sonnet | Non-Python dead code, duplicated logic that has diverged |
| [`contradiction.md`](reviewers/contradiction.md) | opus | Adjudicate clusters - the finding dovetail exists for |
| [`staleness.md`](reviewers/staleness.md) | opus | Semantic doc/code divergence |
| [`spec-flow.md`](reviewers/spec-flow.md) | opus | Diagrams and specifications vs the implementation |

Every rubric names the deterministic categories it must **not** report. A
reviewer restating a check Python already did exactly is offering a guess in
place of a certainty, and a contract test asserts each rubric says so.
