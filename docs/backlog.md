# Backlog

Concerns deferred from the discovery-agent work, kept here so they are
evaluated on their own rather than folded into whatever change surfaced them.
Each entry says what was observed, why it was not acted on, and what acting on
it would involve.

---

## B1. `UnsatisfiedAssumption` is labelled as a forwarded error, not an ignored iteration

**Area:** provider (`crosshair_provider.py`) · **Kind:** completion vocabulary

`assume()` raises `UnsatisfiedAssumption`, which subclasses
`HypothesisException`, so it lands in the generic handler and is recorded as:

```
forwarded hypothesis UnsatisfiedAssumption exception
```

Forwarding the exception is correct — Hypothesis needs it to reject the input.
The *label* is the issue. The completion vocabulary otherwise splits cleanly
into `ignored due to ...` (the iteration was discarded, nothing was learned)
and `forwarded ... exception` (something escaped that arguably should not
have). A rejected input belongs to the first group: it is the same kind of
event as `IgnoreAttempt`, which is already reported as
`ignored due to lazily-detected path impossibility`.

Observed on `pypa/packaging`'s version property suite: 46 of 4619 solver
iterations (1%), from ordinary `assume(version.local is None)` calls in
otherwise healthy tests.

Why it matters beyond cosmetics: any consumer reading completions as a health
signal has to special-case this string, because "a Hypothesis exception was
forwarded" is exactly the shape of the API-drift defect that release 0.0.30
fixed. `tools/discovery` now carries that special case
(`telemetry.BENIGN_FORWARDED_EXCEPTIONS`). Fixing the label upstream would let
that special case go away.

**Proposed change:** catch `UnsatisfiedAssumption` ahead of the general
`HypothesisException` handler and record
`set_completion("ignored due to unsatisfied assumption")` while still
re-raising. Roughly the shape of the existing `InvalidArgument` branch.

**Not done because:** it changes an observable string that consumers may
already depend on, and the call is the maintainer's.

---

## B2. The CrossHair budget is per pytest invocation, not per test

**Area:** `tools/discovery` · **Kind:** design deviation

`docs/discovery-agent-design.md` specifies a per-test budget that escalates for
tests showing productive completions. The implementation puts
`crosshair_limits.wall_seconds` on the whole pytest run, so 161 tests shared
one clock. A single pathological test can starve every test after it, and the
resulting timeout is attributed to the batch rather than to the test that
caused it — which would misfile a `crosshair_timeout` verdict.

**Proposed change:** run the solver arm test-by-test, or in small batches, so
the budget and any timeout attach to a specific node id. This is also what the
design's escalation rule needs in order to work at all.

---

## B3. The Docker sandbox has never run against a real daemon

**Area:** `tools/discovery/sandbox.py` · **Kind:** unverified safety claim

`DockerSandbox` is asserted only at the argv level: `test_sandbox.py` checks
that the hardening flags are present in the constructed command line. No
container has ever been started, because the daemon was unusable in the
development environment. Every real run so far used `LocalSandbox`, which
provides no isolation, against code that had been read first.

**Proposed change:** run the fixture project end to end under a real daemon and
confirm the network is actually unreachable, the root filesystem is actually
read-only, and the memory and pid ceilings actually bite. Until then the
pipeline should not be pointed at an unread repository.

---

## B4. The observer-effect bound rests on nine hand-written properties

**Area:** `docs/discovery-agent-design.md` §5 · **Kind:** thin evidence

Observability demonstrably realizes symbolic draws (concrete values appear in
the JSONL `arguments` field) and shifts the search path (a witness changed
between modes). No finding was lost in either mode across nine
solver-dependent properties, and observed runs cost ~40% more wall clock.

Those nine properties were written to be solvable, by the same person reading
the result. They are not a corpus. The two-tier split and the A/B divergence
check exist precisely because that bound cannot be trusted at higher
difficulty, and the divergence check has not yet had real projects to disagree
on: the `packaging` runs produced no tier A/B disagreements, but also no
failures at all, so the check has never been exercised against a real finding.

**Proposed change:** treat the divergence count as a first-class metric once a
few hundred real tests have run, and revisit the claim then.

---

## B5. The classifier has only ever seen failures I wrote

**Area:** `tools/discovery/classify.py` · **Kind:** unvalidated against reality

Every branch of the verdict matrix is unit-tested, and four branches are
exercised end to end — but against `tests/fixtures/demoproj`, whose bugs were
authored specifically to land in those branches. The two real runs produced
161 + 95 tests of `no_signal`, so on real code the classifier has so far only
demonstrated the ability to say "nothing here".

In particular `crosshair_false_positive`, `soundness_suspect`, and
`observer_effect` have never fired outside a fixture.

**Proposed change:** keep this in mind when reading the first real trophy —
the classifier's confident verdicts are least tested exactly where they matter
most.

---

## B6. `pytest --collect-only` exits 2 on `packaging`, unexplained

**Area:** `tools/discovery/runner.py` · **Kind:** loose end

Collection against `pypa/packaging` enumerates all 389 property tests and then
exits 2. The most likely cause is its `filterwarnings = ["error"]` turning a
warning into an error during teardown. `collect()` tolerates a nonzero exit
when the inventory is populated, which is correct and deliberate, but the
underlying cause was never confirmed.

**Proposed change:** capture and read the collect-only stderr on a nonzero exit
so the reason is recorded rather than assumed. A project where collection
partially fails *and* still reports node ids would slip through today.

---

## B7. Stages 4 onward are not built

**Area:** pipeline · **Kind:** scope

The design's build order continues past what exists: the canary suite run in
both tiers, the version-bump regression re-run that turns the verdict cache
into a regression suite, and the agent decision points. Stage 4 is what makes
the loop safe to leave running unattended — without a canary, a broken
environment produces confident nonsense rather than an error.

---

## B8. The coverage-delta metric does not exist

**Area:** `tools/discovery` · **Kind:** design feature present but inert

`telemetry.coverage_delta()` is written and unit-tested, and nothing calls it.
It cannot be called as things stand: it compares the baseline arm's covered
lines against the solver arm's, but the baseline only ever runs in tier A,
which has observability off and therefore produces no coverage data.

The design leans on this metric twice — as the evidence behind a trophy's
"random search does not reach here" claim, and as a cross-release regression
signal. Neither is currently available. What the runs do produce is the solver
arm's coverage alone (1157 lines on the version suite, 2589 on ranges), which
says how much code was reached but not how much *more* than the baseline.

**Proposed change:** run the baseline once in tier B as well. It is the cheap
arm — roughly 2.8s versus 44s for the solver on comparable work — so the extra
observability pass costs little. Then wire `coverage_delta` into the report.

---

## B9. The verdict cache is never read or written

**Area:** `tools/discovery/store.py` · **Kind:** design feature present but inert

`cache_key()`, `Store.cached()`, `Store.put_cache()` and the `cache` table are
implemented and tested. No caller uses any of them. Every run therefore redoes
work it has already done, and design §6's central claim — that invalidating the
cache on a CrossHair or plugin version bump turns the re-run into the Goal-2
regression suite — has no implementation behind it.

Wiring it needs something the pipeline does not yet collect: the CrossHair,
plugin, and Python versions actually in use in the solver environment, plus the
project's commit SHA. `cache_key()` already takes exactly those five fields.

**Proposed change:** capture the version tuple during the clean-room preflight
(which already executes code in the target environments), then consult the
cache before running a test and record the verdict after.

---

## B10. Add a check for defined-but-unreferenced helpers

**Area:** `tools/discovery` · **Kind:** preventive

Three separate defects in this work shared one shape: a function was written,
unit-tested, and never called. `Validator.preflight` (the clean-room check),
selector expansion, `coverage_delta`, and the verdict cache all passed their
tests while doing nothing, because a unit test exercises a helper directly and
proves nothing about whether the pipeline invokes it.

Two of those were caught only by reading output that looked wrong. That is not
a reliable detector.

**Proposed change:** a test that walks the package and fails on a public
module-level function with no call site outside its own tests — the audit that
found B8 and B9, made permanent. Attribute-style properties and pytest hooks
need exempting, so it wants a small allowlist rather than a blanket rule.
Pair it with the `test_pipeline_wiring.py` approach: assert against
`Pipeline.run` with fake collaborators, not against helpers in isolation.

---

## B11. Symbolic reasoning does not survive a regex-based parser

**Area:** CrossHair · **Kind:** reachability limit, found by fault injection

A fault was injected into `packaging.version._cmpkey` that skips trailing-zero
stripping when the release begins `(17, 3, 11)` — inside the tests' strategy
domain (`st.integers(0, 20)`), but roughly a 1-in-28,000 draw. CrossHair ran 49
solver iterations against the test that covers it and did not find it.

Bisecting the test's shape isolates where the constraint is lost:

| Test shape | Fault found |
| --- | --- |
| Three plain `st.integers(0, 20)` compared for equality | yes, 2.2s |
| A fixed-length list of the same | yes, 0.9s |
| A variable-length list | yes, 0.9s |
| The list joined into `"17.3.11"` and string-compared | yes, 2.3s |
| The same string passed through `Version()` and compared | **no** |

So symbolic reasoning survives list construction, `str()`, and `join` — and is
lost inside `Version()`, whose parse is a regex match with named groups. Every
one of the 49 iterations logged a realization.

**This is a cost problem, not a capability limit.** CrossHair can crack
regexes; this one is expensive. `packaging`'s pattern carries about ten
optional groups (`?+`), and each one forks the path space at least once, so
the fork count multiplies before any single path gets hard. The realizations
land on character arithmetic — `48 + int_01%10 == 48`, ASCII `'0'` plus a
digit — so digits are being made concrete during matching rather than carried
symbolically across that many branches.

Raising the per-path solver budget does not help, which is the useful part.
Deadlines of none, 5s and 20s (2.5s/10s/40s per path) all failed to solve
through the parse, and all finished in ~8-10s without consuming the extra
budget. The binding constraint is the *number* of paths, not time per path, so
`max_examples` is the lever rather than `deadline`.

**Proposed change:** none to CrossHair. For the harness, treat "asserts on
values parsed out of a generated string" as a cost signal during fitness
scoring, and prefer properties that assert on structured values directly.

---

## B12. Realization is invisible in the completion histogram

**Area:** provider / `tools/discovery` · **Kind:** missing signal

An iteration whose symbolic values were realized still completes and still
reports `completed normally`, so the productivity metric cannot distinguish a
solver-driven search from random testing. Both `packaging` sweeps reported 100%
productivity at 91% and 99% realization rates.

`tools/discovery` now derives a realization rate from the `SMT realized
symbolic` entries the provider already emits in `metadata.backend.messages`,
and warns when a test searched mostly concretely.

Deriving it from a free-text debug log is fragile — it depends on a log
string's wording, and `_IMPORTANT_LOG_RE` decides what reaches the JSONL at
all. **Proposed change (provider):** report realization as a structured count
in `observe_test_case`'s return value, next to `completion`, so consumers do
not have to parse messages to learn whether the search was symbolic.

---

## B13. Feed findings into `pschanely/crosshair-benchmark`

**Area:** outputs · **Kind:** additional consumer

Cases the loop turns up are candidate benchmark entries, and the corpus is a
natural source of realistic ones: the `packaging` regex parse is already a
concrete example of something measurable and currently out of budget.

One caveat to carry over: a native CrossHair example often has an advantage
over the equivalent Hypothesis example, so a case lifted from a Hypothesis test
is not directly comparable to a hand-written native one. Any entries this
produces should be marked with their provenance, and comparisons kept within
the same category.

**Proposed change:** decide what shape a benchmark entry takes (property, seed,
budget, expected outcome), then emit them as a by-product of classification
rather than as a separate pass.
