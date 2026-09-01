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
| **V** (validation) | default, **fresh process, plugin not installed, no tracers** | Does X's finding survive without CrossHair in the room? |

`V` is the trophy gate. CrossHair drives user code with symbolic proxies; a
proxy that leaks or mis-realizes can produce a failure that does not exist in
real execution. Without `V` the loop will file false bugs on other people's
repositories, which is the single worst outcome available to it.

`V` must run in a process where `hypothesis-crosshair` is **not installed at
all** — not merely unselected. The plugin registers an entry point and CrossHair
patches builtins on import, so "same venv, different backend" is not a clean room.
`V` also runs with observability off and no coverage tracer attached, for the
reasons in section 5.

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

**Egress on the reporting side — a hard invariant.** The loop MUST NOT open an
issue, pull request, or comment on any repository outside the CrossHair project's
own. Not gated, not rate-limited: no automated write path to third-party
repositories exists at all. The loop drafts and queues for human review, and a
human posts. An automated bug-report firehose burns maintainer goodwill and, with
it, the credibility of the trophy list. Auto-filing into your *own* CrossHair and
plugin trackers is acceptable, deduplicated.

Enforce this structurally rather than by policy: the sandbox holds no credentials
(above), and the orchestrator's GitHub token should be scoped so that the
third-party write path is impossible even if some future agent decides it would
be helpful.

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

- **Cut off ancestor pytest configuration.** pytest walks upward for both its
  ini file and its `conftest.py` files. A checked-out project therefore inherits
  collection hooks and settings from whatever happens to sit above it in the
  harness's own directory tree, which can silently deselect the entire suite.
  Pass `--confcutdir` at the project root, and supply an empty `-c` when the
  project has no config of its own.

Also pin `PYTHONHASHSEED`, and record the interpreter version — CrossHair
support varies across Python versions and a version-specific failure is itself a
useful Goal-2 datapoint.

---

## 5. Telemetry, and the observer effect

### Two-tier execution

Observability mode perturbs CrossHair (measured below). Telemetry therefore
never decides a verdict. Every test runs in two tiers:

- **Tier A — verdict run.** No observability, no coverage tracer, nothing else
  attached. Produces only pass/fail and the falsifying example. **All
  classification in section 1 uses Tier A exclusively.**
- **Tier B — telemetry run.** Observability on. Produces completion histograms,
  SMT message trails, and coverage deltas. Used for steering, prioritization,
  and Goal-2 mining — never as the sole basis of a published result.

This split is what makes the concern manageable rather than fatal: the expensive,
introspective run informs *what to work on next*, and the clean run decides *what
is true*.

**Divergence is itself a finding.** If Tier A and Tier B disagree on the outcome
of the same test at the same seed, that is an observer-effect bug in CrossHair or
the plugin — file it under Goal 2. The loop should therefore detect the exact
failure mode that motivates the two-tier split, rather than merely tolerating it.

### Measured observer effect

Checked against the current plugin on Python 3.11, over nine properties
constructed so that only a solver can satisfy them (magic constants, a narrow
float window, structured strings, a `sum(map(ord, s))` checksum, `a*a == 1522756`
conjoined with a modular constraint):

- **Realization is real and visible.** Under observability the JSONL `arguments`
  field holds *concrete* values (`{"x": 123456789}`) while `representation` still
  reads `<symbolic>`. Draws are being realized to populate the observation.
- **The search path does shift.** One property's witness changed between modes
  (`a=1234` unobserved, `a=-1234` observed) — both valid, but the exploration
  order genuinely differs.
- **No capability loss observed.** All nine properties were solved in both modes,
  including the marginal ones.
- **Cost is material:** ~18s unobserved vs ~26s observed on the harder set,
  roughly 40% overhead, on top of the ~16x over the default backend.

So the risk is confirmed in mechanism but bounded in consequence *at the
difficulty tested*. Treat that as a bound, not a guarantee: harder constraints
than these are exactly where premature realization would first cost a finding,
and the corpus will contain harder constraints than these. The two-tier split and
the divergence check keep that from silently corrupting results.

### Coverage and tracer interaction

CrossHair installs a bytecode tracer, so any second tracer is a hazard. Running
the same suite under `coverage run` on Python 3.11 showed no conflict — all
findings survived, with and without observability, no crashes. **This is a
single-version result.** Python 3.12+ moved coverage to `sys.monitoring`, a
different mechanism entirely, and the loop tests across 3.9–3.14; re-verify per
interpreter rather than generalizing from 3.11. Tier A carries no external tracer
in any case, so a conflict degrades telemetry, never a verdict.

### What Tier B collects

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
| `ignored due to non determinism detected` | Usually nondeterminism in *user code* | **Quarantine the test** — not a CrossHair bug by default |
| `ignored due to use of Python features not yet supported by CrossHair` | — | **coverage gap**; mine for roadmap |
| `ignored due to path timeout` / `excessive solver costs` | — | **performance bug** |
| `ignored due to lazily-detected path impossibility` | Normal pruning | healthy; high rates suggest over-constraining |
| `forwarded hypothesis UnsatisfiedAssumption exception` | `assume()` rejected the input | healthy — ordinary filtering |
| `forwarded hypothesis X exception` (any other) | Provider leaked a Hypothesis error | **API drift** |

The last row is exactly the class of defect that release 0.0.30 fixed, which is
the argument for tracking it continuously rather than waiting for a user report.

**Exclude `UnsatisfiedAssumption` from that row.** `assume()` raises it to
reject an input, so the provider forwards it on every filtered iteration of a
perfectly healthy test — 1% of solver iterations on `pypa/packaging`'s version
suite. Folding it in with genuine drift would flag most real projects, which is
how a defect signal becomes noise and stops being read.

**Nondeterminism means skip, not bug.** `non determinism detected` usually means
the *user's* code is nondeterministic — time, hashing, iteration order, ambient
state — not that CrossHair misbehaved. The default action is to quarantine that
test and stop spending budget on it; a nondeterministic test can never produce a
trustworthy trophy anyway, since stage `V` will not reproduce reliably.

Crucially, **CrossHair's determinism check is deep**, and the baseline gate
cannot substitute for it. An internal memoization cache, an interned value, a
lazily-populated lookup table — anything whose second execution differs from its
first, even where observable behavior is identical — is enough to trip it. Such
code passes three identical baseline runs and still reports nondeterminism, so
"stable at baseline" is evidence of *observable* determinism only and must never
be used to promote a nondeterminism report into a CrossHair defect. Ordinary
well-behaved libraries cache things; expect a nonzero rate everywhere and treat
it as a routine cost of doing business.

Track the per-project rate: a project whose suite is broadly nondeterministic
should leave the corpus. Escalate an individual case only on manual inspection,
never automatically.

**Productivity metric.** Define productive = fraction of crosshair-phase
iterations completing as `completed normally` or `raised ...`. If a test spends
most of its budget on `ignored due to ...`, CrossHair is not actually exploring
it: demote the test for Goal 1, and record the dominant ignore-reason as a
ranked Goal-2 finding. The ignore-reason histogram, aggregated across the whole
corpus, is effectively a prioritized CrossHair roadmap derived from real code.

**Productivity is not sufficient, and on its own it misleads.** A realized
value is concrete for the remainder of the iteration, so the solver has stopped
steering it — but the iteration still finishes and still reports `completed
normally`. Search can therefore degenerate into random testing at 100%
productivity, and the completion histogram will show nothing wrong.

Measured on `pypa/packaging`: the version suite reported 100% productivity
while **91% of solver iterations contained a realization**, with only 8 of 161
tests searching symbolically throughout; the ranges suite reported 100%
productivity at a **99% realization rate**, with 1 of 95 tests clean. Both runs
found no failures, and on the strength of the productivity number alone that
was initially read as a meaningful negative result. It was not: those runs were
mostly random search wearing a solver's coat.

**Therefore: track the realization rate, from the `SMT realized symbolic`
entries in `metadata.backend.messages`, and treat a passing test above roughly
50% as carrying no information about whether a bug exists.** A `no_signal`
verdict is only evidence of absence when the search that produced it stayed
symbolic.

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
Run the canary in **both tiers** and compare: a canary that solves unobserved but
fails observed is the observer-effect regression from section 5, and it should
block the batch and file under Goal 2. The nine properties measured in section 5
are a reasonable seed for this suite.
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
plain `hypothesis`, no CrossHair, the falsifying input applied explicitly.
If that file does not fail on a clean machine, there is no trophy.

**Replay the example explicitly; do not lean on the example database.** Two
things make the database the wrong mechanism for stage `V`. Passing
`--hypothesis-seed` — which the baseline arm needs for reproducibility across
seeds — *disables the database entirely*, so a seeded run saves nothing and the
replay silently degrades into a fresh random search that finds nothing and
"refutes" a real bug. And a saved choice sequence is a low-level encoding whose
meaning depends on the Hypothesis version decoding it, which is a fragile
assumption across two separate environments. Calling the test's undecorated body
with the reported arguments avoids both problems and is self-evidencing: either
the example was applied or it was not.

**An inconclusive replay must never refute a finding.** If the replay could not
run — import failure, signature mismatch, unusable example text — the verdict is
*pending*, never *false positive*. This is the single most dangerous direction
for the classifier to be wrong in, because it discards real bugs silently and
leaves no trace to audit. Any validation step must produce positive evidence
that it exercised the reported example before its answer is allowed to count.

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

Stages 1-3 are implemented in [`tools/discovery/`](../tools/discovery/).
Deferred concerns are tracked in [`backlog.md`](backlog.md).

1. Sandbox + install + collect + baseline gate, on a hand-picked list of ten
   good-fit projects. No agent yet.
2. Two-tier execution plus telemetry parsing and the completion histogram.
   **This alone starts producing Goal 2 findings immediately** — ignore-reason
   and crash aggregates need no trophy validation and no human gate.
3. The differential classifier and stage `V`.
4. Canary suite (both tiers), the A/B divergence check, and the version-bump
   regression re-run.
5. Agent at the failure-triage point only — the highest leverage, and the easiest
   to evaluate against verdicts you already reviewed by hand.
6. Agent at candidate triage and harness repair; scale the corpus up.
7. Trophy drafting behind the human gate.

Goal 2 pays off from week one and needs no permission from anyone. Goal 1 is
slower, needs the full validation chain, and should stay hand-reviewed until the
false-positive rate over a few dozen findings is known.
