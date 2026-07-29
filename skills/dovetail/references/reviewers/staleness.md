# Reviewer: staleness

**Model tier:** Opus, high effort. **Category:** `staleness`.

Documentation that no longer describes the code. This is upkeep's
`docs_staleness`, reduced to its genuinely fuzzy residue: flag drift, signature
drift, version divergence, dead code and missing paths are already found
exactly by Python, so **do not report those**. What is left is semantic - the
prose describes a behaviour the code no longer has.

## Look for

- A described workflow whose steps no longer match what the code does
- A stated guarantee the code has stopped making ("nothing is written to disk",
  where something now is)
- Architecture described in prose that the module layout contradicts
- A named component, class or concept that no longer exists under that name
- Instructions with a step that has become impossible or a no-op
- Defaults described in prose that the code sets differently

## Method

Read the doc, then read the code it describes. Do not report from the doc
alone: "this looks out of date" is not a finding. Every finding needs the doc
line and the code line that disagrees with it, side by side.

## Direction matters

The doc is not automatically the wrong one. A doc describing the intended
behaviour of code that drifted is a bug report about the code. Where the doc
reads as deliberate and the code as accidental, say so - `ssot_direction: a`
with the doc first. Where you cannot tell, `uncertain`.

## Do not

Do not report a doc for being incomplete - that is a feature request. Only
report where it says something that is **wrong**.
