#!/usr/bin/env python3
"""
`.dovetail/config.toml` - the committed, per-repository settings.

Humans write TOML; the machine writes JSONL. That split is not a style
preference, it falls out of the stdlib constraint: `tomllib` reads TOML but
there is no stdlib TOML *writer*, and hand-rolling serialisation to make a
config file machine-writable would be a smell. So config is human-owned and
the decisions ledger is machine-owned, and neither format has to do both jobs.

A malformed config is reported, never silently ignored: a typo that quietly
disabled half the checks would be the worst possible failure mode for a tool
whose whole value is that you can trust its output.
"""

from __future__ import annotations

import os

import bootstrap

# Must precede the tomllib import: tomllib is the 3.11 floor, and this re-execs
# under a newer interpreter when `python3` is older than that.
bootstrap.ensure()

import tomllib  # noqa: E402

CONFIG_REL = os.path.join('.dovetail', 'config.toml')

DEFAULTS: dict = {
    'ignore': [],
    'profile': 'default',
    'checks': {},      # check name -> bool, to disable an individual check
    'gate': {},        # heuristic check name -> bool, to let it fail --fail-on
    'reviewers': {},   # reviewer name -> {enabled, model, effort}
}

# Checks whose findings are likely problems rather than proven ones. A link
# that resolves to nothing is broken on every reader's screen; a file nothing
# links to may still be an entry point, and two files that stopped changing
# together may simply have finished. So these findings carry tier `heuristic`
# and do not fail `--fail-on` unless `[gate]` opts the check in. A gate that
# goes red on noise gets removed, and then it catches nothing.
HEURISTIC_CHECKS = frozenset({
    'orphans', 'near_duplicates', 'missing_paths', 'dead_python_code',
    'decoupled_pairs', 'stale_todos', 'shell_scripts_exit_on_error',
    'scripts_are_executable', 'translation_lag',
})

VALID_PROFILES = frozenset({'default', 'cheap', 'thorough'})


class ConfigError(ValueError):
    """The config file exists but cannot be used as written."""


def load_config(repo_root: str) -> dict:
    """Read `.dovetail/config.toml`, falling back to defaults.

    Raises ConfigError for a file that is present but unreadable or invalid,
    rather than proceeding on defaults: a user who wrote a config expects it to
    take effect, and silently ignoring it would hide findings they meant to see
    or show ones they meant to mute.
    """
    config = {key: (value.copy() if isinstance(value, (list, dict)) else value)
              for key, value in DEFAULTS.items()}
    path = os.path.join(repo_root, CONFIG_REL)
    if not os.path.exists(path):
        return config

    try:
        with open(path, 'rb') as fh:
            raw = tomllib.load(fh)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f'{CONFIG_REL} is not valid TOML: {exc}') from exc
    except OSError as exc:
        raise ConfigError(f'{CONFIG_REL} could not be read: {exc}') from exc

    if 'ignore' in raw:
        if not isinstance(raw['ignore'], list) or not all(
                isinstance(item, str) for item in raw['ignore']):
            raise ConfigError('`ignore` must be a list of glob strings')
        config['ignore'] = list(raw['ignore'])

    if 'profile' in raw:
        if raw['profile'] not in VALID_PROFILES:
            raise ConfigError(
                f"`profile` must be one of {', '.join(sorted(VALID_PROFILES))}; "
                f'got {raw["profile"]!r}')
        config['profile'] = raw['profile']

    checks = raw.get('checks')
    if checks is not None:
        if not isinstance(checks, dict):
            raise ConfigError('`[checks]` must be a table of name = true/false')
        for name, enabled in checks.items():
            if not isinstance(enabled, bool):
                raise ConfigError(f'`checks.{name}` must be true or false')
        config['checks'] = dict(checks)

    gate = raw.get('gate')
    if gate is not None:
        if not isinstance(gate, dict):
            raise ConfigError('`[gate]` must be a table of check name = true/false')
        for name, enabled in gate.items():
            if not isinstance(enabled, bool):
                raise ConfigError(f'`gate.{name}` must be true or false')
            if name not in HEURISTIC_CHECKS:
                raise ConfigError(
                    f'`gate.{name}`: only a heuristic check can be opted into the '
                    f"gate ({', '.join(sorted(HEURISTIC_CHECKS))}). Every other "
                    'check is proven and always gates.')
        config['gate'] = dict(gate)

    reviewers = raw.get('reviewers')
    if reviewers is not None:
        if not isinstance(reviewers, dict):
            raise ConfigError('`[reviewers]` must be a table of per-reviewer tables')
        for name, settings in reviewers.items():
            if not isinstance(settings, dict):
                raise ConfigError(f'`[reviewers.{name}]` must be a table')
        config['reviewers'] = {name: dict(settings)
                               for name, settings in reviewers.items()}

    return config


def check_enabled(config: dict, check_name: str) -> bool:
    """Whether a named deterministic check should run."""
    return config.get('checks', {}).get(check_name, True)


def gated_checks(config: dict) -> list[str]:
    """Heuristic checks the config lets fail `--fail-on`."""
    return sorted(name for name, on in config.get('gate', {}).items() if on)
