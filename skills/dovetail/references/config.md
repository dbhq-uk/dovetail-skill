# `.dovetail/config.toml`

Committed, per-repository settings. Every key is optional; the file itself is optional.

Humans write TOML, the machine writes JSONL. That split is not a preference - `tomllib` reads TOML and there is no standard-library TOML *writer*, so making config machine-writable would mean hand-rolling serialisation. Config is human-owned; `.dovetail/decisions.jsonl` is machine-owned.

**A config that exists but is invalid stops the run.** It is never silently ignored: a typo that quietly disabled half the checks is the worst available failure for a tool whose value is that its output can be trusted.

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

# Per-reviewer overrides. These beat the profile, because config is the
# durable setting and a spoken profile is for one run.
[reviewers.spec-flow]
enabled = false          # this repo has no diagrams

[reviewers.code-hygiene]
model = "opus"           # this repo is mostly shell, which needs the judgement
effort = "high"
```

## Deterministic check names

For `[checks]`. These are the function names, so a disabled check is traceable to the code that implements it.

| Name | What it finds |
|---|---|
| `broken_links` | links whose target does not exist |
| `dangling_anchors` | `#anchor` links to a heading that is not there |
| `orphans` | files nothing references |
| `exact_duplicates` | byte-identical files |
| `near_duplicates` | files that are nearly identical |
| `translation_lag` | translations behind their base document |
| `flag_drift` | documented flags a script does not declare |
| `unparseable_code_blocks` | ```python / ```json / ```toml blocks that do not parse |
| `missing_paths` | backticked repo paths in prose that do not exist |
| `signature_drift` | documented calls the real signature would reject |
| `version_drift` | manifests declaring different versions |
| `dead_python_code` | public Python symbols nothing names |
| `shell_scripts_exit_on_error` | executable shell scripts without `set -e` |
| `scripts_are_executable` | shebangs without the executable bit |
| `skill_frontmatter` | `SKILL.md` missing or malformed frontmatter |
| `decoupled_pairs` | files with a long shared history that stopped moving together |
| `stale_todos` | TODO markers older than six months |

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
