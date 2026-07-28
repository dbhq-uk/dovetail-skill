"""The contract between the two dispatch paths.

There are two ways a reviewer gets run: in-session subagents driven by
SKILL.md, and the headless `ci_dispatch.py` shim for the scheduled job. A second
dispatch path is a genuine maintenance risk - the failure mode is a schema
change landing green because only one path was exercised.

These tests pin the shared surface, so that cannot happen quietly. No model is
called: the contract is about the prompt, the roster and the validator, all of
which are deterministic.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import ci_dispatch  # noqa: E402
from reviewer import ROSTER, ValidationError, validate_findings  # noqa: E402

REFERENCES = os.path.join(os.path.dirname(__file__), '..', 'references')

# A reviewer response as a model would actually emit it, fence and all.
SAMPLE_RESPONSE = '''```json
[
  {
    "category": "contradiction",
    "problem": "Two documents disagree about the request timeout.",
    "evidence": [
      {"file": "README.md", "line": 1, "quote": "requests time out after 30 seconds"},
      {"file": "docs/config.md", "line": 1, "quote": "the default timeout is 60s"}
    ],
    "suggestion": "Decide which is authoritative and align the other.",
    "severity": "high",
    "confidence": "high",
    "ssot_direction": "uncertain"
  }
]
```'''


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.repo = self._tmp.name
        subprocess.run(['git', 'init', '-q'], cwd=self.repo, check=True)
        for key, value in (('gc.auto', '0'), ('maintenance.auto', 'false')):
            subprocess.run(['git', '-C', self.repo, 'config', key, value],
                           check=True, capture_output=True)
        self.write('README.md', 'requests time out after 30 seconds\n')
        self.write('docs/config.md', 'the default timeout is 60s\n')

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, rel: str, body: str) -> None:
        path = os.path.join(self.repo, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as fh:
            fh.write(body)


class SharedValidator(Base):
    def test_both_paths_accept_the_same_response(self):
        # ci_dispatch validates through reviewer.validate_findings, and SKILL.md
        # instructs the session to call the same function. One response, one
        # verdict - that is the whole contract.
        out = validate_findings(SAMPLE_RESPONSE, 'contradiction', self.repo)
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]['source'], 'reviewer:contradiction')
        self.assertTrue(out[0]['id'].startswith('sha256:'))

    def test_ci_dispatch_uses_the_shared_validator(self):
        self.assertIs(ci_dispatch.validate_findings, validate_findings)

    def test_both_paths_reject_the_same_bad_response(self):
        with self.assertRaises(ValidationError):
            validate_findings('[{"category": "broken_link"}]', 'staleness', self.repo)


class SharedRubrics(Base):
    def test_ci_dispatch_reads_the_shipped_rubrics(self):
        for name in ROSTER:
            self.assertTrue(os.path.exists(ci_dispatch.rubric_path(name)),
                            f'{name} rubric is not where ci_dispatch looks')

    def test_prompt_carries_schema_and_rubric(self):
        prompt = ci_dispatch.build_prompt(
            'contradiction', self.repo, {'clusters': []})
        self.assertIn('Finding schema', prompt)
        self.assertIn('Reviewer: contradiction', prompt)
        self.assertIn('Return only the JSON array', prompt)

    def test_every_rubric_names_the_deterministic_boundary(self):
        # A reviewer that reports a category Python owns is duplicating exact
        # work with a guess. Each rubric has to say so in its own words.
        for name in ROSTER:
            if name == 'claim-extract':
                continue  # feeds another reviewer; emits no findings
            with open(ci_dispatch.rubric_path(name), encoding='utf-8') as fh:
                body = fh.read().lower()
            self.assertTrue('do not report' in body or 'deterministic' in body,
                            f'{name} does not state what it must not report')

    def test_schema_states_the_ssot_rule(self):
        with open(os.path.join(REFERENCES, 'finding-schema.md'),
                  encoding='utf-8') as fh:
            body = fh.read().lower()
        self.assertIn('source of truth', body)
        self.assertIn('uncertain', body)


class ContextRouting(Base):
    def _inputs(self):
        from discover import discover
        from refgraph import build_graph
        inventory = discover(self.repo)
        return inventory, build_graph(self.repo, inventory)

    def test_contradiction_gets_clusters_not_files(self):
        context = ci_dispatch._context_for('contradiction', *self._inputs())
        self.assertIn('clusters', context)

    def test_other_reviewers_get_files(self):
        context = ci_dispatch._context_for('staleness', *self._inputs())
        self.assertIn('files', context)

    def test_claim_extract_is_not_dispatched_for_findings(self):
        # It produces claims, not findings; the clustering it would serve is
        # already done in Python. Dispatching it would pay for nothing.
        self.assertEqual(ROSTER['claim-extract'].get('produces'), 'claims')


class NoTriageInTheShim(Base):
    def test_shim_has_no_write_path(self):
        # All interaction lives in SKILL.md, which CI never invokes. If the
        # shim grows a write path, the gated-write guarantee is gone.
        with open(os.path.join(os.path.dirname(__file__), '..', 'scripts',
                               'ci_dispatch.py'), encoding='utf-8') as fh:
            source = fh.read()
        # It must not touch the decisions ledger: recording a decision is a
        # triage act, and triage does not happen in CI.
        self.assertNotIn("append_decision", source)
        # Exactly one write, and it is the report `--out` names - a file
        # outside the repository, never the repository itself.
        writes = re.findall(r"open\([^)]*['\"][wa]['\"]", source)
        self.assertEqual(len(writes), 1, f"unexpected writes: {writes}")
        self.assertIn("args.out", source)

    def test_shim_never_fails_the_build(self):
        # Its output is a report, not a gate. A probabilistic merge gate is one
        # people learn to override.
        with open(os.path.join(os.path.dirname(__file__), '..', 'scripts',
                               'ci_dispatch.py'), encoding='utf-8') as fh:
            source = fh.read()
        self.assertIn('Never fails the build', source)


if __name__ == '__main__':
    unittest.main()
