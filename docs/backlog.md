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

## B2. The CrossHair budget is per pytest invocation, not per test (mitigated)

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

## B7. Stages 5 onward are not built

**Area:** pipeline · **Kind:** scope

Stage 4's canary is built and passing (see B18), single-tier. What remains is
running it in the telemetry tier as well, the version-bump regression re-run
that turns the verdict cache into a regression suite, and the agent decision
points.

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

**It is blocked more deeply than the missing baseline pass.** See B14: cases
generated by the CrossHair backend record `coverage: null`, so even with a
tier-B baseline to compare against there is no solver-side coverage to compare.
Both halves need fixing before the metric exists.

**Proposed change:** resolve B14 first; then run the baseline once in tier B as
well — it is the cheap arm — and wire `coverage_delta` into the report.

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

## B11. Symbolic reasoning stops at three unhandled regex constructs

**Area:** CrossHair `libimpl/relib.py` · **Kind:** capability gaps + one bug

A fault was injected into `packaging.version._cmpkey` that skips trailing-zero
stripping when the release begins `(17, 3, 11)` -- inside the tests' strategy
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

Two earlier readings of this were wrong, and both are corrected here. It is
not a capability limit on large regexes, and it is not a cost problem either
("CrossHair can crack regexes; this one is expensive"). CrossHair never starts
searching: `relib` rejects the pattern outright and falls back to
`re.Pattern.fullmatch(self, realize(string))` at `relib.py:802`. That is why
raising the per-path budget changed nothing -- deadlines of none, 5s and 20s
all finished in ~8-10s without consuming the extra budget. There was no search
to time out.

**Where realization actually happens.** `debug("Realized at", ch_stack())`
gives two independent sites in `Version.__init__`, and the regex is the second
of them:

1. `version.py:418`, `_SIMPLE_VERSION_INDICATORS.issuperset(version)`, where
   the set is `frozenset(".0123456789")`. A `frozenset.issuperset` of a
   symbolic string iterates and hashes each character, and hashing forces
   realization. This is a fast-path optimization and it runs on the first
   statement of `__init__`, before the regex is reached at all.
2. `version.py:446`, the `fullmatch`, via the `ReUnhandled` fallback below.

**Three distinct findings in `relib`, in the order they are hit:**

| # | Construct | Result | Status |
| --- | --- | --- | --- |
| 1 | `POSSESSIVE_REPEAT` (`a*+`, `)?+`) | `ReUnhandled`, realize | gap |
| 2 | `SUBPATTERN` with inline flags (`(?a:...)`) | `ReUnhandled`, realize | gap |
| 3 | `unicode_ignorecase_mask` on a metacharacter | `re.error` | **bug** |

Minimal reproductions, greedy vs possessive being one character apart:

```
greedy      'a*'         OK (symbolic)      possessive  'a*+'        ReUnhandled -> POSSESSIVE_REPEAT
greedy      '(?:ab)*'    OK (symbolic)      possessive  '(?:ab)*+'   ReUnhandled -> POSSESSIVE_REPEAT
greedy      '[a-z0-9]+'  OK (symbolic)      possessive  '[a-z0-9]++' ReUnhandled -> POSSESSIVE_REPEAT
'(?:[0-9]+)'  OK (symbolic)                 '(?a:[0-9]+)' ReUnhandled -> unsupported subpattern args
```

Finding 3 is a plain defect. `relib.py:127` builds a pattern by interpolating
a raw character:

```python
matches = re.compile(chr(cp), re.IGNORECASE).findall(chars)
```

`chr(cp)` is not escaped, so a metacharacter codepoint raises:
`'+'`, `'*'`, `'?'` give `nothing to repeat at position 0`; `'('` gives
`missing ), unterminated subpattern`; `'a'` is fine. `re.escape` is the fix.
It is currently masked -- findings 1 and 2 bail out before it is reached.
Reproduced at the `_match_pattern` level; **not** yet reproduced through the
Hypothesis path, where the string tends to realize before matching gets there,
so its user-facing impact is unproven.

**Which gap binds.** Not the one tried first. Removing all 12 possessive
quantifiers from `VERSION_PATTERN` barely moved the needle -- 17 to 20 code
locations, 5.2s to 5.6s over 149 iterations -- because finding 2 waits behind
it. `packaging` keeps a `_VERSION_PATTERN_OLD` for pre-3.11.5 interpreters
with no possessive quantifiers at all, and it is blocked too, on the same
`(?a:` groups. Finding 2 is therefore the one to fix first; fixing 1 alone
buys nothing.

**What fixing them would buy, measured indirectly.** Stripping both constructs
from the pattern changes the run's character completely:

| pattern | iters | wall | code locs | sec/iter |
| --- | --- | --- | --- | --- |
| as shipped | 149 | 6.5s | 17 | 0.044 |
| both constructs stripped | 19 | 239.4s | 27 | 12.6 |

A 286x rise in per-iteration cost is the signature of symbolic work actually
happening, which is the clearest evidence that realization -- not budget -- is
what makes the shipped pattern cheap and useless. Two caveats keep this from
being a clean result. The stripped pattern still contains `\+`, so it trips
finding 3, and the arm is contaminated to an unknown degree. And 17 to 27 code
locations is a modest gain for 286x the cost.

That suggests fixing these gaps converts "fast and useless" into "slow and
still limited", at which point this genuinely does become a budget problem --
the earlier cost framing was describing a second wall, behind the one CrossHair
actually hits today. Worth re-measuring once findings 1 and 2 are fixed, rather
than assuming either outcome.

**Proposed change:** report all three upstream to CrossHair. Escaping in
`unicode_ignorecase_mask` is a small fix. Possessive repeat is `(?>x*)` --
atomic, no backtracking -- and is arguably easier to encode symbolically than
the greedy form already supported. Inline-flag subpatterns need the flags
threaded through `_internal_match_patterns` rather than asserted to be zero.
Separately, `frozenset.issuperset` of a symbolic string is worth supporting as
a conjunction of character-membership constraints; for a charset as small as
`".0123456789"` that is well within reach, and it would unblock site 1.

---

## B12. Realization is invisible in the completion histogram (addressed)

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

---

## B14. No coverage is recorded under the CrossHair backend

**Area:** provider / Hypothesis interaction · **Kind:** missing signal

Every case generated by the CrossHair backend carries `coverage: null` in the
observability JSONL — 1225 of 1225 in one run checked directly. Concrete cases
in the same run record coverage normally. The likely cause is the tracer
conflict flagged early in the design: Hypothesis's line tracer cannot run
alongside CrossHair's, so it records nothing rather than failing.

Two consequences. First, the coverage figures these runs report describe the
concrete phases only; reading them as solver reach is wrong, and this
repository did exactly that for two runs before checking. Second, it rules out
what would otherwise be the better progress indicator: comparing symbolic
against concrete coverage would show directly whether the solver is exploring
new code, is immune to the small-domain realization problem in B15, and needs
no ground truth. It cannot be built while the symbolic half is null.

**Proposed change:** determine whether CrossHair can cooperate with
Hypothesis's coverage tracer, or expose reached-line information from its own
tracer for `observe_test_case` to report. Either would unblock B8 and give the
loop a progress signal that does not depend on planted faults.

---

## B15. Realization rate over-flags small domains

**Area:** `tools/discovery/telemetry.py` · **Kind:** metric refinement

The realization rate counts every realization equally. Realizing a bool costs
nothing — the domain has two values and both are cheap to explore — whereas
realizing an int or a string can end meaningful search. A test doing routine
bool realization can therefore look degraded while making fine progress.

The rate cannot currently be weighted, because it is derived from free-text
debug messages (`SMT realized symbolic: 48 + int_01%10 == 48`) that do not
reliably carry the realized value's type or domain size.

**Proposed change:** depends on B12 — if the provider reports realization as
structured data, include the type or domain size so small-domain realization
can be discounted. Until then treat the rate as a weak diagnostic rather than
a gate, and do not demote a test on it alone.

---

## B16. The pathing oracle already counts solver progress

**Area:** `hypothesis_crosshair_provider/crosshair_provider.py` ·
**Kind:** telemetry, and a proposed provider option

CrossHair's `max_uninteresting_iterations` is read only by `analyze_calltree`
and `path_search` in `crosshair/core.py`. The provider never builds an
`AnalysisOptions`, because the search loop here is Hypothesis's, so the option
is not reachable from the plugin.

The counter it gates on *is* reachable. The provider owns `self.search_root`,
whose `CoveragePathingOracle` maintains `visits` (distinct code locations at
which the solver forked) and `iters_since_discovery`. Both were confirmed live
and incrementing under the provider by sampling them per iteration.

This matters beyond throughput: `len(visits)` is solver-side coverage,
computed inside CrossHair. It is the progress signal B14 says is unavailable
through Hypothesis's tracer, which records `coverage: null` for every symbolic
case. It is per-test, continuous, free, and unaffected by B15's small-domain
problem, since realizing a value that opens no new branch does not increment
it.

**Measured, 300 iterations, `max_examples=300`:**

| case | wall | code locs | plateau | mui=5 | mui=10 | mui=25 |
| --- | --- | --- | --- | --- | --- | --- |
| toy regex `^(?:v?)(\d+)(?:\.(\d+))?(?:\.(\d+))?$` | 314s | 43 | iter 49 | stop@19, 35/43 | stop@49, 43/43 | stop@64, 43/43 |
| `packaging.Version(s)` | 15.9s | 9 | iter 9 | stop@9, 9/9 | stop@14, 9/9 | stop@29, 9/9 |
| `a + b == b + a` | 0.3s | 0 | — | never fires (paths exhausted at iter 2) | | |

Two readings. First, a threshold near 10-15 is right, and the cost is
asymmetric: at 5 (CrossHair's default when unset) the crackable regex loses 8
of 43 locations, while being generous costs `packaging` five extra iterations.
An adaptive rule also removes the need to guess how many optional clauses a
pattern holds — it stops once they stop being found.

Second, and more useful for Goal 2: `packaging` reaches only 9 code locations
and plateaus at iteration 9, against a `VERSION_PATTERN` of 1075 characters
with 35 `?` quantifiers, 22 `+`, 10 alternations and 13 named groups. The
solver is not reaching the optional clauses; it stalls at the doorway. The toy
pattern, by contrast, sustains 43 locations at ~1s per iteration of real
solver work. This is a sharper statement of B11 than the earlier bisection,
and the first number attached to it.

**Two parts, deliberately separate:**

1. **Built.** The injected pytest plugin wraps
   `per_test_case_context_manager` and reads `len(visits)`,
   `iters_since_discovery` and an iteration count per test, reported as
   `search` and carried through to each `Classification`. The wrapper is
   installed only when the run's backend is `crosshair`, so the baseline arm
   is untouched, and every read is guarded: instrumentation must never be able
   to fail a target project's run. Because it needs no observability and no
   tracer, it is collected in the verdict tier as well, which also makes a
   tier-A/tier-B gap in `code_locations` an independent observer-effect
   signal. `STALL_THRESHOLD` is 10, per the measurements above.
2. **Proposal only, not built.** Optionally let the provider stop a stalled
   search: when
   `iters_since_discovery` exceeds a threshold, `set_completion(...)` and
   raise `BackendCannotProceed("exhausted")`. Hypothesis's engine handles that
   scope by setting `_switch_to_hypothesis_provider` (`engine.py:541`), so the
   test spends its remaining budget on the concrete backend rather than
   aborting — a stalled test still gets random examples, at a fraction of the
   cost per example. This changes what a plain `backend="crosshair"` run does
   and should be opt-in rather than default-on.

---

## B17. Corpus sweep: the stall is specific, not general

**Area:** corpus / `tools/discovery` · **Kind:** measurement, plus two harness gaps

Seven projects, 213 Hypothesis tests measured under `backend="crosshair"` at
`max_examples=20`, capped at 40 tests per suite, scored by the B16 path-search
counters.

| suite | n | ended early | stalled | progressing | median locs |
| --- | --- | --- | --- | --- | --- |
| packaging/version | 40 | 0% | **50%** | 50% | 25 |
| packaging/ranges | 40 | 0% | **38%** | 62% | 75 |
| packaging/specifier | 40 | 8% | **58%** | 35% | 32 |
| attrs | 40 | 70% | 0% | 30% | 3 |
| bidict | 7 | 100% | 0% | 0% | 1 |
| cattrs | 40 | 30% | 0% | 70% | 43 |
| dateutil | 4 | 50% | 0% | 50% | 26 |
| pyrsistent | 2 | 0% | 0% | 100% | 48 |
| **total** | **213** | **24%** | **27%** | **48%** | |

"Ended early" is fewer than 10 solver iterations, "stalled" is more than 10
iterations with more than 10 since the last new code location.

**Every one of the 58 stalled tests is in `packaging`.** The other five
projects stall on nothing. So the reachability problem behind B11 is specific
to version-string parsing, not a general property of third-party Hypothesis
suites, and Goal 1 is not blocked corpus-wide.

**A correction to how B11 was generalized.** The "9 code locations, stalls at
the doorway" figure came from a synthetic test written here -- `st.text()` fed
to `Version(s)` -- not from `packaging`'s own tests. Its property suite uses
structured strategies and reaches a median of 25 and a maximum of 147 code
locations, with half its tests progressing. The relib gaps are real and they
bite, but they do not flatten the suite that exercises them.

**Reading the counter needs both dimensions.** `attrs` and `bidict` have the
lowest medians in the corpus (3 and 1) and are the healthiest results in it:
70% and 100% of their tests end early reporting `exhausted all paths -
nothing to do`, which is CrossHair proving the property over a small domain.
Read on `code_locations` alone they would look like the worst suites here.
Low reach plus few iterations is a proof; low reach plus many iterations is a
stall.

**Two harness gaps the sweep exposed:**

1. Stateful tests were never forced onto the backend -- fixed. All three in
   the corpus (two in `pyrsistent`, one in `bidict`) were listed in
   `forced_nodeids` while recording no solver iterations, so they would have
   been reported as CrossHair results without ever running under CrossHair.
2. Collection misses tests that a project's own config excludes. `dateutil`
   keeps its Hypothesis tests under `tests/property/` and sets `python_files`,
   so a bare run collects none of them; they appear only when the directory is
   named explicitly. `jsonschema`'s single Hypothesis file is an OSS-Fuzz
   harness with no pytest tests, which is a true negative. Candidate discovery
   should look for Hypothesis usage in files pytest would not collect by
   default and widen the invocation, or the corpus will silently under-report.


---

## B18. The canary passes, and the trophy path has fired once

**Area:** `tools/discovery/canary.py` · **Kind:** result

Two faults injected into `packaging`, run through the whole pipeline --
baseline gate, both arms, clean-room validation, classifier.

| fault | sited | expected | verdict |
| --- | --- | --- | --- |
| `packaging/release-negated` | `_validate_release`, behind the from-parts constructor | detected | `trophy_candidate` |
| `packaging/parsed-pre-shifted` | after the regex parse | not detected | `no_signal` |

The positive fault negates a release component when it begins `(73, 12)` --
in range for the strategy's 0-99 draws, roughly 1-in-12500. Three baseline
seeds at 150 examples did not find it. CrossHair did, and the clean room
confirmed it, which is what makes the verdict `trophy_candidate` rather than
`pending_validation`. The falsifying example is `Version('-73.12')` failing
`assert -73 >= 0`: the exact injected conjunction, from the solver arm alone.

**This is the first end-to-end evidence that the chain works.** Until now the
classifier had only ever seen failures written by hand (B5), and no run had
produced a trophy verdict on a real project. B5 is not closed -- an injected
fault is easier than a real one, and this bounds false negatives rather than
the false-positive rate that actually gates Goal 1 -- but the machinery is no
longer unexercised.

The negative control returning `no_signal` confirms B11's prediction from the
other direction: the same defect one call later, behind the regex, is not
found. That result is now asserted rather than rediscovered, and if it ever
flips to detected the relib gaps have been fixed.

**What the first live run actually bought.** Both faults initially came back
`no_baseline_result`, because `packaging`'s `addopts` deselects its own
property tests -- and the negative control was scored PASS, since nothing was
detected and nothing detected was what it wanted. A canary that reports green
when the pipeline produced nothing retires the doubt it exists to hold open.
Inconclusive verdicts now fail in both directions. The canary's first act was
to catch a bug in the canary.


---

## B19. The deep corpus run found two classifier bugs and a fourth CrossHair defect

**Area:** `tools/discovery/classify.py` · **Kind:** results, and two fixes

47 progressing tests across six projects through the full pipeline at
`crosshair_max_examples=300` against a 3x400 baseline.

| project | verdicts |
| --- | --- |
| packaging (15) | 15 `crosshair_crash` -- misclassified, see below |
| attrs (12) | 7 `pending_validation` -- misclassified, 5 `no_signal` |
| cattrs (15) | 15 `no_signal` |
| dateutil (2) | 2 `no_signal` |
| pyrsistent (2) | 2 `no_signal` |
| bidict (1) | 1 `no_signal` |

No trophies. Both non-`no_signal` groups turned out to be defects in the
classifier rather than findings, which is the answer the canary was built to
make legible: before it, a run of 47 tests reporting 22 non-trivial verdicts
would have looked like a productive sweep.

**Bug 1: a timeout was reported as a crash.** All 15 `packaging` tests hit the
2400s wall budget, and the sandbox escalates a timeout straight to SIGKILL, so
the run carried both `timed_out` and a negative return code. The crash branch
was checked first, so `CROSSHAIR_TIMEOUT` was unreachable dead code and every
budget exhaustion was reported as CrossHair crashing. Fixed by checking the
timeout first. This is B2 showing its cost: the budget is per invocation, so
15 tests at 300 examples share one 2400s allowance.

**Bug 2: a CrossHair internal error was routed to the trophy track.** The
seven `attrs` results were `CrossHairInternal: Numeric operation on symbolic
while not tracing`, raised inside the test and caught by pytest as an ordinary
failure. `_INTERNAL_ERROR_RE` only scans stderr, so nothing caught it, and the
classifier treated it as a finding awaiting validation. A CrossHair defect
would have been presented as a candidate third-party bug -- the exact
false-positive path that gates Goal 1. Fixed by classifying a
crosshair-internal exception type as `crosshair_crash` before the finding
branch.

**A fourth CrossHair finding, for the report in `crosshair-findings.md`:**
`CrossHairInternal: Numeric operation on symbolic while not tracing`
reproduces on seven of `attrs`' `tests/test_funcs.py` tests (`TestAssoc`,
`TestEvolve`, `TestAsDict::test_asdict_preserve_order`) under
`backend="crosshair"`. Unlike the three relib findings this one is a genuine
internal invariant failure on unmodified third-party code, and it needs no
fault injection to reproduce.


---

## B20. Per-test budgets turn 15 unusable results into 14 real ones

**Area:** `tools/discovery/cli.py` · **Kind:** result

The deep run's `packaging` arm produced 15 `crosshair_crash` verdicts, all of
them artefacts: 15 tests shared one 2400s allowance and one `max_examples`, so
the solver arm was SIGKILLed mid-run and every test in the batch inherited the
kill. Re-run with `--per-test`, one pipeline invocation per test at 200 solver
examples and a 420s budget each:

| | before (shared budget) | after (`--per-test`) |
| --- | --- | --- |
| `crosshair_crash` | 15 | 0 |
| `crosshair_timeout` | 0 | 1 |
| `no_signal` | 0 | 14 |

Fourteen tests that had produced nothing usable now report a real result, and
the one genuine timeout --
`test_ranges_cross_epoch.py::test_membership_consistent_across_epochs` -- is
labelled as a timeout rather than a crash. That is the first time
`CROSSHAIR_TIMEOUT` has ever been reachable; before B19's ordering fix the
crash branch shadowed it entirely, so it was dead code from the day it was
written.

Cost: 4499s for 15 tests, against 2450s for the same 15 sharing one budget.
Roughly 1.8x for results that are actually interpretable, and the per-test
mode parallelises trivially if that becomes the bottleneck.

Goal 1 remains at zero: 213 shallow plus 47 deep plus these 15, no trophy
candidate outside the injected canary fault.


---

## B21. Fallbacks to concrete matching are now reported automatically

**Area:** provider + `tools/discovery` · **Kind:** result

Three of the four CrossHair findings were invisible to the tool. `relib`
rejects a construct, realizes the string, matches concretely, and the iteration
reports `completed normally`; nothing separated it from a healthy search.
Finding them meant reading a debug buffer by hand.

`observe_test_case` now reports the construct behind a fallback and the code
that forced a realization, and the run report aggregates both. A live
two-test run against `packaging`:

```
    88 solver iterations, 100% productive
    65 (74%) realized a symbolic value; 1 of 2 tests searched mostly concretely
        88  100.0%  completed normally
    59 iterations fell back to concrete matching on a construct CrossHair does not handle:
            59  \s* POSSESSIVE_REPEAT
    realization forced at:
           131  (test_pre_release_integer_normalized ...:130) (__init__ version.py:418)
           112  (__init__ version.py:446) (_fullmatch relib.py:802)

  solver path search:
    STALLED    30 locs    59 iters  ...::test_pre_release_integer_normalized
              206 locs    47 iters  ...::test_release_is_tuple_of_nonneg_ints
```

The histogram still says `completed normally` for all 88 iterations, which is
exactly the blind spot: on its own it reports a perfectly healthy run. The
fallback count names `POSSESSIVE_REPEAT` outright, and the realization sites
name `version.py:418` and `relib.py:802` -- B11's two blockers, reported rather
than investigated.

The two signals corroborate one another. The stalled test is the one whose
fallbacks accumulate, and the test that reaches 206 code locations is the one
going through the from-parts constructor rather than the regex. Neither signal
alone says that: the stall metric says a search stopped extending its reach,
and the fallback count says why.

**Not closed by this.** The counts come from different sources -- the
histogram from observability rows, the iteration counts from the provider's
oracle -- so they do not share a denominator and should not be compared as
though they do. And this reports constructs `relib` explicitly logs; a
capability gap that fails some other way stays invisible.
