---
name: dovetail
description: Check whether a repository agrees with itself, then fix it one finding at a time. Finds contradictions between documents, missing cross-references, broken links, orphaned files, stale docs, and drift between documentation and code. Trigger on phrases like "dovetail", "check this repo", "does this repo agree with itself", "find contradictions", "repo coherence", "docs drift".
---

# dovetail

Checks whether a repository agrees with itself and walks you through fixing it.

Phase 1 scope: the deterministic scan. Run it and report the findings.

## Steps

1. **Scan.** Run the deterministic layer against the target repository:

   ```bash
   python3 ~/.claude/skills/dovetail/scripts/scan.py <repo-path> --format json
   ```

   It takes seconds, makes no network calls, and never modifies the target.

2. **Report.** Present findings grouped by severity (high, then medium, then
   low). For each, give the category, a one-line problem statement, and the
   evidence lines as `file:line`. State the total and how many were suppressed
   by prior decisions.

3. **Do not fix anything yet.** The interactive fix loop is Phase 2. If the user
   asks for a fix, explain that and offer to make the edit manually.

## Requirements

- Python 3.11 or newer (the tested floor; Phase 2's config reader will need
  `tomllib`, which arrived in 3.11), and `git`.
- No API key, no network, no third-party packages.
