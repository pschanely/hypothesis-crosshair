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
