---
name: dovetail
description: Check whether a repository agrees with itself and report what does not. Finds broken links, dangling heading anchors, orphaned files, duplicate and near-duplicate content, and translated documents that have fallen behind their base. Deterministic - no model calls, no network, and it never modifies the target. Reports findings; it does not fix them. Trigger on phrases like "dovetail", "check this repo", "does this repo agree with itself", "find contradictions", "repo coherence", "docs drift".
---

# dovetail

Checks whether a repository agrees with itself, and reports precisely where it does not.

The scan is deterministic: it makes no model calls, no network requests, and never modifies the target repository. It reports findings for a human to act on - it does not edit anything itself.

## Steps

1. **Scan.** Run the deterministic layer against the target repository:

   ```bash
   python3 ${CLAUDE_SKILL_DIR}/scripts/scan.py <repo-path> --format json
   ```

   It takes seconds, makes no network calls, and never modifies the target.

2. **Report.** Present findings grouped by severity (high, then medium, then
   low). For each, give the category, a one-line problem statement, and the
   evidence lines as `file:line`. State the total and how many were suppressed
   by prior decisions.

3. **Do not edit the repository.** dovetail reports; it does not fix. If the
   user wants a finding fixed, make the edit yourself in the normal way, using
   the evidence lines to find the right place.

## Requirements

- Python 3.11 or newer, and `git`.
- No API key, no network, no third-party packages.
