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

### Resuming, and the verdict cache

With `--store` and `--per-test`, each test is claimed from the store before it
runs and retired after, and its verdict is cached under the project commit plus
the CrossHair, plugin and Python versions. Two things follow:

- `--resume RUN_ID` continues a run that was killed, skipping the tests it
  already has verdicts for and reporting them alongside the new ones. The run
  id is printed when the run finishes.
- A later run over the same commit and versions is served from the cache
  instead of being executed. A CrossHair or plugin upgrade changes every key,
  so the first run after a release re-executes everything and is the regression
  suite. `--refresh` forces re-execution without one.

A test claimed three times without ever producing a verdict is abandoned, so a
test that reliably destroys its worker cannot stop a run from finishing.

### Triage

`--triage-command` hands each failure cluster to an external program, one
invocation per cluster. The program receives the cluster as JSON on stdin --
exception, frame, normalized message, node ids, examples, and one unscrubbed
traceback -- and must print a JSON object:

```json
{"category": "project_bug", "confidence": 0.9, "reasoning": "...", "evidence": ["src/pkg/a.py:10"]}
```

`category` is one of `project_bug`, `overstrong_property`, `crosshair_artifact`
or `unclear`. An answer that does not parse, a command that exits non-zero, and
a command that hangs past its timeout each abandon that one cluster and leave
the rest of the batch alone; nothing unvalidated is ever recorded.

The decider lives outside this tool so that a model can answer here without the
pipeline depending on one. `--triage-budget` bounds how many clusters one
invocation will decide.

### Where a triaged cluster goes

| triage says | and the classifier says | result |
| --- | --- | --- |
| `project_bug` | `trophy_candidate` | a trophy **draft**, for a person to read |
| `project_bug` | anything else | **withheld**, with the verdicts that blocked it |
| `crosshair_artifact` | anything | a CrossHair defect record |
| `overstrong_property` | anything | dismissed |
| `unclear` | anything | sent to a person |

A trophy needs both: the clean-room replay shows the example failing without
the plugin, and triage read the code and called it a real bug. Either alone
has a failure mode the other covers -- triage can misread code, and a replay
cannot tell a real defect from an over-strong property. In particular
`pending_validation` means the replay was *inconclusive*, which is not
evidence, so triage cannot stand in for the replay that did not run.

A trophy draft is a record. This tool never reports anything to a third-party
project.

### Scoring a decider

`python -m discovery.evaluate_cli --cases cases/triage.jsonl --decider "<cmd>"`
runs a decider over clusters whose answer is known and reports what it got
right and, separately, what kind of wrong it got.

Accuracy alone is the wrong measure, because the errors do not cost the same:

- **reaching a stranger** -- calling someone else's correct code, or one of our
  own defects, a project bug. The only error that can put a draft in front of a
  third party.
- **lost findings** -- a real project bug routed to a stream nothing revisits.
- **wrong stream** -- our own defect confused with an over-strong property.
- **deferred** -- answered `unclear` where an answer existed. Safe, costs time.
- **unusable** -- no answer that parsed. Scored as neither right nor wrong.

The command exits non-zero if anything reached a stranger or was unusable.

`cases/triage.jsonl` holds clusters taken from real runs, each labelled by hand
with the reason recorded. It is a regression asset: a decider change that
starts calling a CrossHair artifact a project bug fails here first. A case
whose `project` is a path inside this repository is resolved on load, so a
decider can read that source; one naming a project checked out elsewhere is
left unresolved and the decider sees only the cluster.

### The shipped decider

`deciders/claude_decider.py` is a decider backed by the `claude` CLI. It reads
a cluster on stdin and prints a verdict, so it plugs into `--triage-command`
and `--decider` unchanged:

```
python -m discovery.cli ... --triage-command "python deciders/claude_decider.py"
```

It gives the model read-only access to the project under test -- `Read`, `Grep`
and `Glob`, nothing that writes or executes -- because the project is
third-party code this pipeline treats as untrusted and nothing in this judgment
needs to run it. `HCD_DECIDER_MODEL` and `HCD_DECIDER_TIMEOUT` override the
model and the per-cluster timeout.

The prompt tells it that calling a project's correct code a bug is the most
costly error available and that `unclear` is always safe. That is the same
asymmetry the scorecard measures.

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

**`--hypothesis-seed` disables the example database.** Every arm is seeded —
the baseline for cross-seed stability, and both solver tiers with the *same*
seed so that a tier A/B disagreement is attributable to observability rather
than to a different search. Since seeding rules the database out, validation
replays the reported example explicitly against the test's undecorated body
rather than relying on a saved choice sequence, which also keeps the check
independent of Hypothesis internals matching across two environments.

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
| `cluster.py` | Groups failures by defect: exception, frame, scrubbed message |
| `triage.py` | Triage queue, the answer schema, and the decider seam |
| `outcomes.py` | Routes a triaged cluster, and refuses unsupported promotions |
| `validate.py` | Clean-room replay of a reported example |
| `pipeline.py` | Stage orchestration |
| `store.py` | SQLite durable state: work queue, verdicts, version-keyed cache |
| `provenance.py` | The commit and versions a cached verdict belongs to |

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
