# Discovery pipeline (stages 1-3)

Runs a third-party project's Hypothesis tests under `backend="crosshair"` and
classifies what comes out. Implements stages 1-3 of
[`docs/discovery-agent-design.md`](../../docs/discovery-agent-design.md):
sandboxed collection, the baseline gate, two-tier execution with telemetry, and
the three-way differential classifier.

Deterministic end to end. No model is involved in any decision this code makes.

## Usage

```
python -m discovery.cli \
    --project /path/to/checkout \
    --sandbox docker --image python:3.12-slim \
    --crosshair-python "python" \
    --validation-python "/venvs/clean/bin/python" \
    --store verdicts.db
```

`--validation-python` must point at an interpreter where
`hypothesis-crosshair` is **not installed**. The plugin registers an entry
point and CrossHair patches builtins on import, so selecting a different
backend in the same environment is not a clean room. Without this flag,
findings are reported as `pending_validation` rather than claimed.

`--sandbox local` runs on the host with no isolation. It exists for developing
the pipeline against code you already trust; never point it at a repository you
have not read.

## What it reports

| Verdict | Meaning |
| --- | --- |
| `trophy_candidate` | Baseline passes, CrossHair fails, and the example reproduces with the plugin absent |
| `crosshair_false_positive` | CrossHair reported a failure that does not reproduce without it |
| `pending_validation` | The replay was inconclusive. **Not** a refutation |
| `shared_find` | Both arms fail; not attributable to CrossHair |
| `soundness_suspect` | Baseline fails after CrossHair reported the path space exhausted |
| `crosshair_false_negative` | Baseline fails, CrossHair does not |
| `crosshair_crash` / `crosshair_timeout` | The solver arm died or ran out of budget |
| `observer_effect` | Outcome differs between the verdict and telemetry tiers |
| `quarantined_unstable` | Baseline outcomes differed across seeds |
| `quarantined_nondeterministic` | Most solver iterations were discarded for nondeterminism |
| `no_signal` | Neither arm found anything |

Trophy candidates are drafts for a human. **This code has no write path to any
third-party repository and must never be given one.**

## Design points worth knowing before changing this code

**Verdicts come from tier A only.** Observability realizes symbolic draws and
shifts the search path, so `classify()` refuses a tier-B run outright. Tier B
supplies completion histograms and coverage deltas for steering. Where the two
tiers disagree on an outcome at the same seed, that disagreement is itself
reported as `observer_effect`.

**An inconclusive replay never refutes a finding.** A validation run that could
not apply the example — import failure, signature mismatch, no usable example
text — yields `pending_validation`, never `crosshair_false_positive`. Absence
of replay evidence is not evidence of absence, and the failure mode this guards
against is silently discarding real bugs.

**Explicit `@settings` beats a registered profile.** A third-party test
carrying its own `@settings(...)` ignores `--hypothesis-profile`, so a
profile-based approach would run the default backend while recording the result
as CrossHair's. The injected plugin rewrites the settings object during
collection instead, and records which node ids it actually forced.

**`--hypothesis-seed` disables the example database.** Only the baseline arm is
seeded. Validation therefore replays the reported example explicitly against
the test's undecorated body rather than relying on a saved choice sequence,
which also keeps the check independent of Hypothesis internals matching across
two environments.

**Ancestor pytest config is cut off.** pytest walks upward for both its ini file
and its conftest files, so runs pass `--confcutdir`, and a project with no
config of its own also gets an empty `-c`. Without this a project inherits
collection hooks from whatever happens to sit above it on disk.

**Nondeterminism means skip, not bug.** CrossHair's determinism check is deep:
an internal memoization cache that never changes observable behavior is enough
to trip it. A high rate quarantines the test; it is not counted as a CrossHair
defect.

## Layout

| Module | Role |
| --- | --- |
| `sandbox.py` | Docker and local execution backends, resource ceilings |
| `_injected_plugin.py` | Runs inside the target env: forces settings, reports outcomes |
| `runner.py` | One pytest invocation for a given arm and tier |
| `telemetry.py` | Observability JSONL parsing, completion histograms, coverage |
| `classify.py` | Baseline gate and the three-way differential |
| `validate.py` | Clean-room replay of a reported example |
| `pipeline.py` | Stage orchestration |
| `store.py` | SQLite durable state and the version-keyed verdict cache |

## Tests

```
PYTHONPATH=. python -m pytest
```

The end-to-end test against `tests/fixtures/demoproj` is skipped unless
`DISCOVERY_VALIDATION_PYTHON` points at an interpreter without the plugin:

```
DISCOVERY_VALIDATION_PYTHON=/venvs/clean/bin/python PYTHONPATH=. python -m pytest
```

The fixture carries one bug reachable only by the solver (a checksum collision),
one both arms find, one correct property, and one flaky test, so the run
exercises four classifier branches against real CrossHair.
