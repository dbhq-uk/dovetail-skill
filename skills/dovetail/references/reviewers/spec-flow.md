# Reviewer: spec-flow

**Model tier:** Opus, high effort. **Category:** `spec_drift`.

The prior tool's `spec_flow`: diagrams and specifications versus the implementation.
This is genuinely hard and genuinely valuable, because a Mermaid diagram is the
artefact most likely to be written once and never revisited.

## Look for

- A Mermaid, PlantUML or DOT diagram whose nodes or edges no longer match the
  code's actual control flow
- A documented state machine with a state or transition the code does not have,
  or missing one it does
- A sequence diagram whose ordering the code contradicts
- An API or schema specification that disagrees with the implementation
- A documented data flow that skips a step the code performs, or vice versa

## Method

Parse the diagram carefully - node by node, edge by edge - then trace the code
path it claims to describe. Report the specific node or edge that is wrong, not
"this diagram is out of date". A finding that does not name the element is not
actionable.

## Direction

A diagram is often the *intent* and the code the drift. Do not assume the
diagram is stale. Where the diagram describes something more coherent than the
code does, that is worth saying: `ssot_direction: a`, diagram first.

## Confidence

Diagrams are abstractions and are allowed to omit detail. An omission is not a
contradiction. Only report where the diagram states something the code
**contradicts**, not where it is merely simpler than reality.
