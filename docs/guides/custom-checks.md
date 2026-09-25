# Writing a repo-local check

Every repository states rules about itself that it enforces nowhere. "These three tables must
stay in sync." "Every skill in the installer needs a dependency arm." "The version in the
manifest matches the one in the README."

Written as a check, that rule becomes exact and free. Left in a prompt, it is something a
model might notice on a good day.

## The shape

Drop a module in `.dovetail/checks/` exposing one function:

```python
from store import make_finding


def check(inventory, graph):
    """Every skill directory must carry a README."""
    findings = []
    skills = {p.rsplit('/', 1)[0] for p in inventory['all_paths']
              if p.endswith('/SKILL.md')}
    for directory in sorted(skills):
        if f'{directory}/README.md' not in inventory['all_paths']:
            findings.append(make_finding(
                source='plugin:readme',
                category='convention',
                problem=f'{directory} has a SKILL.md but no README.md',
                evidence=[{'file': f'{directory}/SKILL.md', 'line': 1,
                           'quote': 'skill without a README'}],
                suggestion=f'Add {directory}/README.md',
                severity='low',
                claim='skill without a README',
            ))
    return findings
```

`make_finding` comes from dovetail's own `store` module, which is importable whenever a plugin
runs. It builds the `id` the way every built-in check does: a fingerprint over the category,
the evidence files and the `claim`. Keep line numbers out of `claim`. An `id` that holds a line
number changes whenever someone edits above that line, and a decision recorded against it
stops suppressing the finding.

Modules whose name starts with `_` are skipped, so shared helpers can live beside your checks.
Plugins run last, after every built-in check, so you can rely on the inventory and graph being
complete.

## What you are given

`inventory` is the file census:

| Key | Contents |
|---|---|
| `repo_root` | Absolute path, for opening files |
| `all_paths` | Every tracked path, sorted, including ignored ones |
| `files` | One entry per scanned file: `path`, `modality`, `category`, `size_bytes`, `sha256`, `last_commit_iso` |
| `generated_at_iso` | When the inventory was built |

`all_paths` includes files excluded by `ignore`; `files` does not. Check existence against
`all_paths` and read content from `files`.

`graph` has three keys: `edges` (each recording where a reference came from, how it was
written and what it resolved to), `inbound` (the reverse index), and `headings` (every anchor
id per markdown document: heading slugs, plus `id` and `name` attributes in its HTML). Use it when your rule is about references; `all_paths` is usually enough when it is
about existence.

## What you must return

A list of dicts, built with `make_finding` or by hand. These keys are required, and a missing
one fails **your plugin** rather than the run:

`id` · `source` · `category` · `problem` · `evidence` · `suggestion` · `severity`

- `evidence` must be a non-empty list of `{file, line}` (add `quote` - the render is much
  better with it)
- `severity` is `high`, `medium` or `low`
- `fix`, `blast_radius`, `confidence` and `ssot_direction` are filled in for you if absent. `batch_eligible` is always false for a plugin's finding, so its fixes are applied one at a time
- `source` is overwritten with `plugin:<module-name>` regardless of what you set, so findings
  are always attributable to the file that produced them

## Failure is isolated, never fatal

A plugin that raises, returns junk, or defines no `check` is named in `failed_checks` and
skipped. The rest of the scan completes.

```json
{"failed_checks": ["plugin:roster_matches_skill (ValueError: finding 0 has no evidence)"]}
```

That is not a silent skip: `failed_checks` is printed in the run header, and in CI a failed
check exits `1` whenever `--fail-on` is not `none`, because an incomplete result must not
read as a clean one.

Worth being explicit about what a plugin is: repo-local code, executed in-process. dovetail is
already running as your user against your checkout, so a plugin is no more privileged than the
scan itself - but it is code, and it should be reviewed like code.

## Three worked examples

This repository ships its own, in [`.dovetail/checks/`](../../.dovetail/checks/):

**[`roster_matches_skill.py`](../../.dovetail/checks/roster_matches_skill.py)** - the model
table in `SKILL.md` must match the `ROSTER` declared in `reviewer.py`. It reads the roster
with `ast.literal_eval` rather than importing the module, so a check cannot be tricked into
executing the code it is auditing. This is the ordinary drift: someone retunes a reviewer's
tier and the documented table quietly becomes a lie.

**[`documented_test_count.py`](../../.dovetail/checks/documented_test_count.py)** - the test
count quoted in the README must match the number of tests that exist.

**[`house_style_dashes.py`](../../.dovetail/checks/house_style_dashes.py)** - the repository
says it uses plain hyphens, so the check enforces it.

All three are the same class of defect dovetail exists to find, which is why it would be
embarrassing to ship them broken.

## When a rule belongs here

**Yes** when it is specific to your repository, mechanically decidable, and currently living
only in prose. That is the sweet spot: the rule becomes exact, costs nothing to run, and stops
being something a reviewer has to remember.

**No** when a built-in already covers it - check the [list](../reference.md#deterministic-checks)
first. And no when deciding it needs judgement: if your check would have to guess which side is
right, it is a question for the judgement layer or for a person, not for a plugin that will be
confidently wrong on every run.

## Letting a plugin fail a build

A plugin's findings carry tier `heuristic`. They appear in the JSON, in the annotations and in
the triage queue, but they do not fail `--fail-on` until you opt the plugin in:

```toml
[plugins.house_style_dashes]
gate = true
```

The name is the module's, without `.py`, and one that matches no plugin stops the run. A gated
plugin's findings count at their own severity, so a `low` finding fails `--fail-on low` and not
`--fail-on high`. This repository gates all three of its plugins and runs its own CI at
`--fail-on low`, so a stale test count fails the build.

## Plugins are code from the repository

A plugin runs on every scan, with your permissions. In CI, the per-PR template scans the pull
request's checkout, so a pull request can add or change a plugin and have it run there. Scan a
repository you do not trust with `--no-plugins`. It skips `.dovetail/checks/` and says so: the
JSON counts the skipped plugins in `plugins_skipped`, and the run header names them.

A plugin's `check` function is an entry point. dovetail calls it by name, so `dead_python_code`
never reports it.
