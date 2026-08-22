# Discovery Agent: design

A long-running loop that finds Hypothesis tests in third-party open source
projects, runs them under `backend="crosshair"`, and turns the results into two
output streams:

1. **Trophies** — bugs in third-party code that *only* the CrossHair backend finds.
2. **CrossHair bugs** — defects in CrossHair and this plugin, surfaced by realistic
   third-party usage rather than synthetic tests.

Both streams come from the *same* run. The loop is one pipeline with two
consumers, not two campaigns.

---

## 1. The central mechanic: a three-way differential

For each candidate test `T` in project `P` at commit `C`, produce a triple:

| Run | Backend | Purpose |
| --- | --- | --- |
| **B** (baseline) | default `hypothesis` | What random search finds on its own |
| **X** (crosshair) | `backend="crosshair"` | What the solver finds |
| **V** (validation) | default, **fresh process, plugin not installed** | Does X's finding survive without CrossHair in the room? |

`V` is the trophy gate. CrossHair drives user code with symbolic proxies; a
proxy that leaks or mis-realizes can produce a failure that does not exist in
real execution. Without `V` the loop will file false bugs on other people's
repositories, which is the single worst outcome available to it.

`V` must run in a process where `hypothesis-crosshair` is **not installed at
all** — not merely unselected. The plugin registers an entry point and CrossHair
patches builtins on import, so "same venv, different backend" is not a clean room.

### Classification matrix

| B | X | V | Verdict | Stream |
| --- | --- | --- | --- | --- |
| pass | fail | reproduces | **TROPHY candidate** | Goal 1 |
| pass | fail | does not reproduce | **CrossHair false positive** (proxy intolerance / realization) | Goal 2 — high |
| fail | fail | — | Shared find. Not a trophy. Compare shrink quality only | metrics |
| fail | pass | — | **False negative**; if X reported `exhausted all paths`, a **soundness bug** | Goal 2 — critical |
| pass | pass | — | No signal. Record coverage + completion metrics | metrics |
| any | internal error | — | **CrossHair crash** | Goal 2 |
| any | hang / OOM / timeout with no progress | — | **CrossHair performance bug** | Goal 2 |

The `fail | pass` row is the highest-value cell in the table and the easiest to
forget to instrument. A baseline failure that CrossHair misses is a soundness
report if CrossHair claimed the path space was exhausted, and a coverage report
otherwise.

---

## 2. Safety model

The loop executes arbitrary third-party test suites *and* arbitrary third-party
package build scripts. The realistic threat is not a repository targeting you;
it is an ordinary repository whose suite deletes a directory, posts to a live
API, fills the disk, or spawns a daemon.

**Isolation.** One fresh container per `(project, commit)`. Never reused across
projects. `--network=none` during execution, read-only root filesystem with a
tmpfs work directory, `--cap-drop=ALL`, `--security-opt=no-new-privileges`,
`--pids-limit`, `--memory`, `--cpus`. Never mount the container runtime socket.
gVisor or a microVM if the budget allows, since the workload is *designed* to
execute hostile-shaped inputs.

**Two-phase networking.** Network is enabled only during dependency
installation, and only to a pinned package index or local mirror. It is off for
collection, baseline, CrossHair, and validation runs. This single control
removes most exfiltration and side-effect risk, and it also improves result
quality — a test that silently depended on network access fails loudly at the
baseline gate instead of producing a phantom trophy.

**No credentials inside.** The sandbox never holds a GitHub token, SSH key, or
cloud credential. GitHub access lives in the orchestrator, outside. The sandbox
cannot file an issue, push a branch, or read another repository even if the code
it runs tries to.

**Install hardening.** Prefer `--only-binary=:all:`. An sdist build executes
arbitrary code at build time; allow it, but inside the same sandbox and with the
project flagged. Pin dependencies and check out by commit SHA, never by branch —
a moving target makes cached verdicts meaningless.

**Resource limits.** Wall clock per test and per repo, `RLIMIT_AS`, disk quota.
Timeouts must escalate to `SIGKILL`: CrossHair installs a bytecode tracer and a
wedged tracer may never service a `SIGTERM` handler.

**Egress on the reporting side.** The loop never opens an issue on a third-party
repository without human approval. It drafts and queues. An automated bug-report
firehose burns maintainer goodwill and, with it, the credibility of the trophy
list. Auto-filing into your *own* CrossHair and plugin trackers is acceptable,
deduplicated.

**Detecting escapes.** Snapshot and diff the writable layer after each run.
A suite that wrote outside its temp directory is both a safety signal and a
determinism problem; quarantine the project.

---

## 3. Candidate discovery and scoring

**Sources.** GitHub code search (`from hypothesis import`, `@given(` restricted
to test paths), reverse dependencies on `hypothesis`, the Hypothesis
documentation's user list, and the HypoFuzz ecosystem. Dev dependencies are
usually absent from PyPI metadata, so code search carries most of the weight.

**Fitness scoring.** CrossHair pays off on pure-Python, deterministic,
branch-dense code with small state. Score *up*: parsers, serializers, encoders
and decoders, datetime and timezone logic, decimal/fraction/unit arithmetic,
text normalization, path and URL manipulation, validation rules, state machines.
Score up for `@given` tests using plain strategies and few fixtures, a small
dependency closure, wheel-only installs, and a fast green suite.

Score *down* or exclude: numpy/pandas/torch-centric code, where CrossHair
realizes at the C boundary immediately and the run degenerates to slow random
testing; suites with network, database, or filesystem fixtures; anything marked
integration; suites already red or flaky at baseline; very slow tests.

**Static prefilter.** AST-scan for `@given` / `RuleBasedStateMachine` and for
the exclusion import set *before* provisioning a container. Cheap, and it keeps
the expensive stages fed with plausible work.

---

## 4. Test selection and the baseline gate

Only tests that are **green and stable at baseline** may ever become trophies.

1. `pytest --collect-only -q` inside the sandbox to enumerate test ids.
2. AST-map each id to whether it uses `@given` or a state machine.
3. Run the baseline three times with different seeds. Keep only tests that pass
   all three. Everything else is quarantined as pre-existing-red or flaky and is
   permanently ineligible for the trophy stream.

**Selecting the backend without patching their source.** Inject a `conftest.py`
or a small `-p` plugin that registers a profile, per the project README:

```python
settings.register_profile("crosshair", backend="crosshair", database=None)
```

Two gotchas that will otherwise corrupt the differential:

- **Explicit `@settings` wins over a profile.** A third-party test decorated with
  its own `@settings(...)` will ignore `--hypothesis-profile=crosshair`. The
  injected plugin must detect this and rewrite the settings object during
  collection, otherwise the loop will silently run the *default* backend and
  record it as a CrossHair result. Assert the backend actually took effect by
  checking `how_generated` in the telemetry (see below) — do not trust the flag.
- **Disable the example database.** A populated `.hypothesis/` directory replays
  previously-found failures and will make both arms "find" a bug that neither
  actually searched for. `database=None` in every arm.

Also pin `PYTHONHASHSEED`, and record the interpreter version — CrossHair
support varies across Python versions and a version-specific failure is itself a
useful Goal-2 datapoint.

---

## 5. Telemetry

Set `HYPOTHESIS_EXPERIMENTAL_OBSERVABILITY=1` and parse
`.hypothesis/observed/*_testcases.jsonl`. Per test case this yields `status`,
`status_reason`, `representation`, `how_generated`, `timing`, `coverage`, and —
from this plugin's `observe_test_case` — `metadata.backend.completion` and
`metadata.backend.messages` (the `SMT chose:` / `SMT realized symbolic` trail).

`how_generated` distinguishes the phases, which matters more than it first
appears:

- `during generate phase, using backend='crosshair'` — the only rows attributable
  to the solver, and the only rows carrying backend metadata.
- `during generate phase` — Hypothesis mixes in default-backend generation.
- `during shrink phase` — **shrinking does not run under the CrossHair backend.**
- `minimal failing test case` — the reported example.

The shrink consequence is worth stating plainly: the minimal example CrossHair
reports has already been re-derived by concrete, non-symbolic machinery. That
raises confidence, but it does **not** replace stage `V` — the shrink still
happens inside a process where CrossHair is imported and its patches are live.

### Completion vocabulary → Goal 2 signals

The plugin's `set_completion` strings are the diagnostic vocabulary for
CrossHair's own health. Aggregate them per test and per project:

| `completion` | Meaning | Signal |
| --- | --- | --- |
| `completed normally` | Productive symbolic iteration | healthy |
| `exhausted all paths - nothing else to do` | Search space closed | if B later fails → **soundness bug** |
| `raised X exception` | User-code failure, concretely replayed | trophy candidate |
| `raised X exception, but unable to realize for concrete replay` | Failure could not be made concrete | **realization bug** |
| `ignored due to proxy intolerance` | Symbolic proxy leaked into user code | **fidelity gap** |
| `ignored due to non determinism detected` | Replay diverged | **plugin/CrossHair bug** |
| `ignored due to use of Python features not yet supported by CrossHair` | — | **coverage gap**; mine for roadmap |
| `ignored due to path timeout` / `excessive solver costs` | — | **performance bug** |
| `ignored due to lazily-detected path impossibility` | Normal pruning | healthy; high rates suggest over-constraining |
| `forwarded hypothesis X exception` | Provider leaked a Hypothesis error | **API drift** |

The last row is exactly the class of defect that release 0.0.30 fixed, which is
the argument for tracking it continuously rather than waiting for a user report.

**Productivity metric.** Define productive = fraction of crosshair-phase
iterations completing as `completed normally` or `raised ...`. If a test spends
most of its budget on `ignored due to ...`, CrossHair is not actually exploring
it: demote the test for Goal 1, and record the dominant ignore-reason as a
ranked Goal-2 finding. The ignore-reason histogram, aggregated across the whole
corpus, is effectively a prioritized CrossHair roadmap derived from real code.

**Coverage delta.** `coverage` is populated per file as executed line lists.
Comparing B's union against X's union answers "did the solver reach lines random
search never did?" That is both the marketing claim for a trophy and a
regression metric across CrossHair releases.

---

## 6. Budgets and scheduling

Measured in this environment on four simple pure-Python properties at 100
examples each: default backend **2.8s**, CrossHair **44.8s** — roughly **16×**.
Budget from that order of magnitude, not from optimism.

- Start each test at a 60s CrossHair budget. Escalate to ~300s only for tests
  whose productivity metric cleared threshold at the lower budget.
- Give the baseline arm a *generous* example count. A trophy's central claim is
  "random search does not find this," and the cheapest way to be wrong is to
  under-run the baseline. Before promoting, re-run B at 100× examples; if it
  then finds the bug, it is not a trophy.
- Schedule projects with a bandit over historical signal yield, with a floor of
  exploration budget so new projects still get sampled.
- Cache verdicts keyed by
  `(repo_sha, test_id, crosshair_version, plugin_version, python_version)`.

**Cache invalidation is the Goal-2 regression suite.** On every CrossHair or
plugin version bump, invalidate and re-run the corpus. Diffing the new verdict
matrix against the old one is how you catch soundness and performance
regressions before users do — a trophy that stops being found, or a project that
starts crashing, is a release blocker.

**Canary suite.** Before trusting any batch, run a small ground-truth set of
known-solvable properties (a magic constant, a narrow float window, a structured
string) where CrossHair is known to succeed. If the canary fails, the
environment is broken; discard the batch rather than attributing its results.
A long-running autonomous loop without a canary eventually reports confident
nonsense.

---

## 7. Triage and deduplication

Cluster failures by exception type, the topmost traceback frame *inside project
code*, and a normalized assertion message — with addresses, temp paths, and
timestamps scrubbed. Before promoting a trophy, check the project's issue
tracker for the exception and symbol, and check whether the test is already
marked `xfail`.

Every promoted trophy ships with a standalone reproduction: pinned versions,
plain `hypothesis`, no CrossHair, the falsifying input as an `@example(...)`.
If that file does not fail on a clean machine, there is no trophy.

**Trophy record schema.** Project, commit, test id, the property in one line,
falsifying input, exception, upstream issue link, validation status, coverage
delta, date, CrossHair version — plus the column that actually matters:

> **Why random search misses it.** Quantified, not asserted: the baseline found
> nothing in N examples across M seeds.

That column is the difference between a bug list and a *CrossHair* trophy list.

---

## 8. Where the agent belongs in the loop

Most of this pipeline should not involve a model at all. Sandboxing,
installation, collection, execution, JSONL parsing, classification, caching, and
scheduling are deterministic and must stay deterministic — they are the part
whose correctness the results depend on.

An agent earns its place at exactly four decision points:

1. **Candidate triage.** Read a repository's README and tests and judge whether
   this is pure-Python computational code where the solver will shine. Hard to
   express as a rule, easy for a model, and cheap relative to a wasted container.
2. **Harness repair.** Installation and collection fail in endlessly
   idiosyncratic ways — a missing system library, an unsupported interpreter, a
   fixture that needs a service. An agent reads the error and patches the recipe,
   under a strict retry budget, then gives up and files the project as
   un-runnable.
3. **Failure triage.** The highest-value judgment in the system: is this a real
   bug in the project, an over-strong property in *their* test, or a CrossHair
   artifact that slipped past `V`? This requires reading the code and inferring
   intent.
4. **Report drafting.** The upstream issue, the trophy entry, the CrossHair bug
   report with a minimal repro.

Each of these takes a strict output schema and a budget. **State lives in a
durable store — SQLite or flat JSON — never in the agent's context.** Each agent
invocation is stateless: it reads a work item, returns a structured verdict,
and exits. That is what makes the loop resumable after a crash, auditable after
a bad batch, and safe to run unattended for weeks.

### Stage machine

```
 discover ──► prefilter ──► provision ──► install ──► collect
                 (AST)      (sandbox)    (net ON)   (net OFF)
                                                        │
                                                        ▼
                                             baseline gate (3 seeds)
                                                        │
                                               ┌────────┴────────┐
                                          unstable            stable
                                               │                 │
                                          quarantine             ▼
                                                          crosshair run
                                                                 │
                                                                 ▼
                                                            classify
                                                    (B, X, V + telemetry)
                                                                 │
                                    ┌────────────────────────────┼───────────────┐
                                    ▼                            ▼               ▼
                             validate (clean room)      CrossHair signals     metrics
                                    │                            │               │
                                    ▼                            ▼               ▼
                             dedupe + triage            auto-file (own repo)  dashboards
                                    │
                                    ▼
                          draft report ──► HUMAN GATE ──► upstream issue
```

Every arrow is a durable state transition. Nothing in the diagram requires the
previous stage's process to still be alive.

---

## 9. Suggested build order

Deliberately sequenced so the loop is producing Goal-2 value long before it is
trustworthy enough to talk to strangers.

1. Sandbox + install + collect + baseline gate, on a hand-picked list of ten
   good-fit projects. No agent yet.
2. Telemetry parsing and the completion histogram. **This alone starts producing
   Goal 2 findings immediately** — ignore-reason and crash aggregates need no
   trophy validation and no human gate.
3. The differential classifier and stage `V`.
4. Canary suite and the version-bump regression re-run.
5. Agent at the failure-triage point only — the highest leverage, and the easiest
   to evaluate against verdicts you already reviewed by hand.
6. Agent at candidate triage and harness repair; scale the corpus up.
7. Trophy drafting behind the human gate.

Goal 2 pays off from week one and needs no permission from anyone. Goal 1 is
slower, needs the full validation chain, and should stay hand-reviewed until the
false-positive rate over a few dozen findings is known.
