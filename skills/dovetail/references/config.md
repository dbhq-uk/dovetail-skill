# `.dovetail/config.toml`

Committed, per-repository settings. Every key is optional; the file itself is optional.

Humans write TOML, the machine writes JSONL. That split is not a preference - `tomllib` reads TOML and there is no standard-library TOML *writer*, so making config machine-writable would mean hand-rolling serialisation. Config is human-owned: dovetail never writes it. `.dovetail/decisions.jsonl` is machine-written: the triage loop appends to it, and a line you add by hand in the same format works too.

**A config that exists but is invalid stops the run.** It is never silently ignored: a typo that quietly disabled half the checks is the worst available failure for a tool whose value is that its output can be trusted. Invalid covers names as well as types. A setting, check, reviewer or reviewer key that does not exist exits `2` with the nearest valid name, so `stale_todo = false` is caught rather than disabling nothing. So does a `model` or `effort` outside the lists below.

```toml
# Globs excluded from the scan entirely. `**` works.
ignore = ["vendor/**", "third_party/**", "*.generated.md"]

# Default model profile for this repository.
#   default   - the per-reviewer tiering below
#   cheap     - every reviewer one tier down, escalation off
#   thorough  - everything on the strongest model
# A profile spoken during a run ("run dovetail thorough") overrides this once.
profile = "default"

# Turn individual deterministic checks off by function name.
[checks]
stale_todos = false
decoupled_pairs = false

# Let a heuristic check fail `--fail-on`. Proven checks always can; heuristic
# ones report without gating unless named here. Naming a proven check, or a
# name that is not a check, stops the run.
[gate]
orphans = true

# Per-reviewer overrides. These beat the profile, because config is the
# durable setting and a spoken profile is for one run.
[reviewers.spec-flow]
enabled = false          # this repo has no diagrams

[reviewers.code-hygiene]
model = "opus"           # this repo is mostly shell, which needs the judgement
effort = "high"
```

## Deterministic check names

For `[checks]` and `[gate]`. These are the function names, so a disabled or gated check is traceable to the code that implements it. A **proven** check reports what the structure alone settles, and its findings fail `--fail-on`. A **heuristic** check reports a likely problem that intent can explain, and its findings fail `--fail-on` only when `[gate]` names it.

| Name | What it finds | Tier |
|---|---|---|
| `broken_links` | links whose target does not exist | proven |
| `dangling_anchors` | `#anchor` links to a heading or HTML anchor that is not there | proven |
| `orphans` | files nothing references | heuristic |
| `exact_duplicates` | byte-identical files | proven |
| `near_duplicates` | files that are nearly identical | heuristic |
| `translation_lag` | translations behind their base document | heuristic |
| `flag_drift` | documented flags a script does not declare | proven |
| `unparseable_code_blocks` | ```python / ```json / ```toml blocks that do not parse | proven |
| `missing_paths` | backticked repo paths in prose that do not exist | heuristic |
| `signature_drift` | documented calls the real signature would reject | proven |
| `version_drift` | manifests declaring different versions | proven |
| `dead_python_code` | public Python symbols nothing names | heuristic |
| `shell_scripts_exit_on_error` | executable shell scripts without `set -e` | heuristic |
| `scripts_are_executable` | shebangs without the executable bit | heuristic |
| `skill_frontmatter` | `SKILL.md` missing or malformed frontmatter | proven |
| `decoupled_pairs` | files with a long shared history that stopped moving together | heuristic |
| `stale_todos` | TODO markers older than six months | heuristic |

## Reviewers

For `[reviewers.<name>]`: `xref`, `convention`, `code-hygiene`, `contradiction`, `staleness`, `spec-flow`, `claim-extract`.

Keys are `enabled` (bool), `model` (`haiku` / `sonnet` / `opus`) and `effort` (`low` / `medium` / `high`).

## Repo-local checks

`.dovetail/checks/*.py` - a module exposing `check(inventory, graph)` returning findings. This is where rules specific to *your* repository belong, so they become exact and free instead of something a model might notice.

```python
def check(inventory, graph):
    """Every skill directory must carry a README."""
    findings = []
    skills = {p.rsplit('/', 1)[0] for p in inventory['all_paths']
              if p.endswith('/SKILL.md')}
    for directory in sorted(skills):
        if f'{directory}/README.md' not in inventory['all_paths']:
            findings.append({
                'id': f'local:readme:{directory}',
                'source': 'plugin:readme',
                'category': 'convention',
                'problem': f'{directory} has a SKILL.md but no README.md',
                'evidence': [{'file': f'{directory}/SKILL.md', 'line': 1,
                              'quote': 'skill without a README'}],
                'suggestion': f'Add {directory}/README.md',
                'severity': 'low',
            })
    return findings
```

Modules starting with `_` are skipped. A plugin that raises, returns junk, or defines no `check` is named in `failed_checks` and skipped - never fatal, and never allowed to report malformed findings as real defects.
