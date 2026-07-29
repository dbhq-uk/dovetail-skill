# Finding schema

The contract every judgement reviewer must satisfy. Both dispatch paths - the
in-session subagents driven by `SKILL.md`, and the headless `ci_dispatch.py`
shim used by the scheduled job - validate against this file. A schema change
that broke one path while the other stayed green is exactly what having one
written contract is meant to prevent.

Return **only** a JSON array of findings. No prose, no preamble, no code fence
around it. An empty array is a valid and common answer.

```jsonc
{
  "source": "reviewer:staleness",   // reviewer:<your own name>
  "category": "staleness",          // from the list below
  "problem": "One sentence stating what is wrong.",
  "evidence": [
    {"file": "README.md",      "line": 40, "quote": "requests time out after 30 seconds"},
    {"file": "src/client.py",  "line": 31, "quote": "TIMEOUT = 60"}
  ],
  "suggestion": "What to do about it, in one sentence.",
  "severity": "high",               // high | medium | low
  "confidence": "high",             // high | medium | low
  "ssot_direction": "uncertain"     // a | b | uncertain | n/a
}
```

`id`, `fix` and `blast_radius` are added by dovetail after you return. Do not
invent them.

## Categories

`contradiction` · `missing_xref` · `staleness` · `convention` · `dead_code` ·
`spec_drift` · `other`

The deterministic layer already owns `broken_link`, `dangling_anchor`,
`orphan`, `duplicate`, `near_duplicate`, `flag_drift`, `signature_drift`,
`version_drift`, `parse_error`, `missing_path`, `decoupled` and `stale_todo`.
**Never report one of those.** If you spot one, it means the exact check missed
it, which is a bug worth a `other`-category finding saying so - not a duplicate
of work Python already did.

## The two rules that are not negotiable

### 1. Do not assume which side is the source of truth

When two things disagree, report the divergence and attach the evidence. Most
audit tools assume the documentation is wrong. Sometimes the code is the
mistake - a refactor that changed a default nobody meant to change, and the
document is the only record of the intent.

Set `ssot_direction` honestly:

- `a` - the first evidence item is authoritative and the second should change
- `b` - the second is authoritative
- `uncertain` - **you cannot tell from the text.** Say so. This is the most
  useful answer you can give when it is true, because it routes the finding to
  a human question rather than an automatic fix
- `n/a` - the finding is not a divergence between two sources

`uncertain` is never a failure. A confident wrong direction produces an
automatic edit to the wrong file.

### 2. The repository's own conventions outrank this rubric

If `CLAUDE.md`, `AGENTS.md`, `CONTRIBUTING.md` or anything under `.claude/`
states a rule, that rule wins over anything written here. A repository that
documents "we deliberately keep British and American spellings apart in these
two files" has settled the question. Report nothing.

## Evidence

Every finding needs at least one evidence item, and a divergence needs at least
two - one per side. Each is `{file, line, quote}`:

- `file` is repo-relative, exactly as it appears in the inventory
- `line` is 1-indexed and must be the line the quote is actually on
- `quote` is the real text from that line, not a paraphrase

Structured evidence is what makes the render honest: the two conflicting lines
appear side by side and the reader judges for themselves, instead of reading a
paragraph asserting that they conflict. **A finding whose quote does not appear
at that line is a fabrication**, and the validator will reject it.

## Severity and confidence

They are different axes and both are needed.

| | |
|---|---|
| `severity` | how much it matters if the finding is real |
| `confidence` | how sure you are that it is real |

A high-severity, low-confidence finding is worth reporting - it gets escalated
to a stronger model before it reaches anyone. A low-confidence finding you
marked `high` confidence to seem useful is worse than no finding at all.

## Do not

- Do not report style preferences. A different way of writing something is not
  a defect.
- Do not report anything you cannot evidence with a file and a line.
- Do not report a category the deterministic layer owns.
- Do not pad. Ten real findings beat forty with thirty guesses in them.
- Do not fix anything. You are reading, not editing.
