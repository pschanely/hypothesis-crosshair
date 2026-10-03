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


---

## B22. Hypothesis harvests injected literals, biasing every canary

**Area:** canary fault design · **Kind:** finding, invalidates an assumption

`cattrs/structure-int-shifted` triggered on `obj == 606811`, a value chosen so
random search would not reach it. The baseline found it in under 150 examples,
reproducibly, and the canary returned `shared_find` instead of
`trophy_candidate`.

Hypothesis (>= 6.16) harvests integer constants from the source of the module
under test and feeds them to `integers()`. Confirmed directly:

```
injected  -> 606811 harvested from the patched source: True
clean     -> 606811 harvested from the patched source: False
```

**So a fault keyed on a literal hands its own trigger to the random arm.** An
injected fault is systematically easier for the baseline than an equivalent
natural bug, which biases every canary towards `shared_find` and away from
`trophy_candidate` -- exactly the direction that would make the pipeline look
worse at its own job than it is.

It also partly explains why `packaging/release-negated` survives as a trophy:
73 and 12 are harvested individually, but the fault needs them together at
positions 0 and 1 of a release of length >= 2, and the conjunction is not
harvestable as a unit.

**Fixed** by keying the cattrs fault on a relation rather than a literal --
`obj > 1000000 and obj % 9721 == 0`. No multiple of 9721 above a million
appears in the source, so nothing is handed over, while a solver reads both
constraints directly. Verified: the fault fires when injected, is inert when
clean, and the triggering value is not in the harvested set.

**Rule for future faults:** never key a fault on a literal the baseline can
read out of the patched source. Use a conjunction of ordinary values, or a
relation whose witnesses do not appear literally.

---

## B23. The solver's search is not reproducible, so a single canary run is a coin toss

**Area:** provider · **Kind:** finding, corrects an earlier diagnosis

`packaging/release-negated` passed as a `trophy_candidate` in the morning and
failed the canary in the afternoon under the same budgets. The first reading
recorded here was that the pipeline's solver arm misses a fault a direct run
finds. That was wrong. It is not the pipeline.

Bisecting the difference between the two invocations ruled out every candidate
in turn -- `--hypothesis-seed`, `PYTHONHASHSEED`, `--confcutdir`, `PYTHONPATH`
pointing at a copied plugin, a fresh `HYPOTHESIS_STORAGE_DIRECTORY`, and the
Runner's replacement environment. Then the same configuration produced both
outcomes:

| configuration | result | iterations |
| --- | --- | --- |
| Runner-equivalent minimal env | found | 85 |
| the same, minutes earlier | **missed** | 57 |
| + `PATH` | found | 49 |
| + `HOME` | missed | 57 |

Across a dozen nominally identical runs the iteration count landed on 26, 43,
49, 57, 85 and back to 57. **The search is non-deterministic despite a fixed
`--hypothesis-seed` and `PYTHONHASHSEED=0`.**

**The dominant amplifier is in the provider**, though not the root cause.
`_make_statespace` sets

```python
execution_deadline=process_time() + per_path_timeout,   # 2.5s when deadline is None
model_check_timeout=per_path_timeout / 2,
```

Both budgets are measured in process time, so a path that completes on an idle
machine is abandoned on a loaded one, and the pathing oracle then targets
somewhere else. That also explains the shape of the failures: the missing runs
reach *more* code locations (154-180) than the finding runs (124-137). A
timed-out path abandons the branch holding the fault, and the search spends its
remaining budget broadening elsewhere.

**Consequences, in order of how much they matter:**

1. A `no_signal` cannot be read as "the solver looked and found nothing"
   without knowing how many attempts it had. This weakens the deep corpus
   run's 20 `no_signal` verdicts, which were single attempts.
2. Canary results are themselves flaky, so a green canary from one attempt is
   weak evidence -- including the stage-4 result that made the pipeline look
   trustworthy in the first place.
3. Reproducing a solver finding needs the same machine under the same load,
   which is worth knowing before filing anything upstream.

**Repetition is the methodology, not a stopgap.** z3 does not guarantee
determinism, so no amount of work upstream makes a seeded run reproducible:
the floor on this variance is not zero. Counting per-path budgets in solver
steps rather than process time would narrow the spread, and is worth doing as
variance reduction, but it would not turn a single attempt into evidence. It is
not a defect to file.

So the canary runs each fault `--repeat` times (default 3) and reports a
detection rate. A fault expected to be found passes if any attempt finds it,
since the question is whether the pipeline can reach it at all; a fault
expected *not* to be found must go unfound in every attempt, which is the
stronger claim and the one worth making strictly.

**This is permanent, which changes how budget should be spent.** A
single-attempt `no_signal` is not weak evidence pending a fix -- it is
uninterpretable, and always will be. Detection rate is the only meaningful
measure of whether the solver reaches something, so a corpus run has to choose
between breadth and repeats rather than assuming one pass settles a test. The
deep corpus run's 20 `no_signal` verdicts were single attempts and should be
read as unmeasured rather than as negative results.


---

## B24. Canary baselines at three attempts

Four faults, three attempts each, same budgets as the single-attempt run.

| fault | expected | rate | verdict |
| --- | --- | --- | --- |
| `packaging/release-negated` | detected | **2/3** | `trophy_candidate` |
| `packaging/parsed-pre-shifted` | not detected | 0/3 | `no_signal` |
| `cattrs/structure-int-shifted` | `trophy_candidate` | 3/3 | `trophy_candidate` |
| `bidict/write-skips-inverse` | `shared_find` | 3/3 | `shared_find` |

All four behave as expected, and the rates say more than the pass marks.

**2/3 measures B23 directly.** The fault that flipped between runs flips
within a single canary invocation: attempts one and three find it, attempt two
does not. That is the non-determinism, observed rather than inferred, and it
sets a floor on how much a single attempt can be trusted -- roughly a third of
single-attempt `no_signal` verdicts on a reachable fault would be wrong.

**The negative control is 0/3, and that asymmetry is informative.** B11's relib
blockage is a capability gap rather than a search outcome, so it does not vary:
the pattern is rejected identically every time. A fault the solver *cannot*
reach is stable; a fault it *can* reach is flaky. So a repeated `no_signal` is
weak evidence of unreachability, while a repeated detection is strong evidence
of reachability -- the confidence is asymmetric, and only the second direction
firms up with repetition.

**The cattrs fix is confirmed.** Re-keyed from a literal to a relation, it
moves from `shared_find` at one attempt to `trophy_candidate` at 3/3, which is
what B22 predicted: the baseline was reading the trigger out of the patched
source, not searching for it.


---

## B25. The corpus re-run with retries, and a bug in the retry itself

`--per-test --retry-no-signal 2` over the deep manifest, 47 tests. The
container restarted twice mid-run, so this was assembled from three launches;
`cattrs` completed its first pass but was killed during retries.

| project | verdicts | retries that changed one |
| --- | --- | --- |
| packaging (15) | 9 `crosshair_timeout`, 6 `no_signal` | 3 |
| attrs (12) | 7 `crosshair_crash`, 5 `no_signal` | 0 |
| cattrs (15) | 15 `no_signal` (first pass only) | not reached |
| dateutil (2) | 2 `no_signal` | 0 |
| pyrsistent (2) | 2 `no_signal` | 0 |
| bidict (1) | 1 `no_signal` | 0 |

**The retry had a bug, and its own output showed it.** All three changed
verdicts went `no_signal` to `crosshair_timeout`. A `no_signal` means the
solver ran and reported nothing; a timeout means it never finished. Accepting
one as the outcome of a retry trades an answer for a non-answer, and makes the
corpus look worse than it is. Fixed: a retry now only replaces a `no_signal`
with a verdict that says *more*, so timeouts, missing baselines and quarantines
no longer overwrite it. The three timeouts are mostly a budget choice -- 240s
per test here against 420s in the earlier per-test run -- not a new finding.

**No retry anywhere turned a `no_signal` into a finding**, across 16 tests that
got three attempts each. That does not undo B23: the canary measured a
*reachable* fault being missed one time in three, so retries demonstrably
matter where something is there to find. It says that on this corpus there was
nothing being missed, which is consistent with every other result -- these are
mature libraries whose suites pass.

**`attrs` confirms B19's classifier fix on real data.** The same seven tests
the first deep run filed as `pending_validation` -- on the trophy track,
awaiting a clean-room replay -- now come back as `crosshair_crash` with
`CrossHair raised crosshair.util.CrossHairInternal inside the test`. That is
the fix working end to end, on the run that first produced the confusion.

**`cattrs`' retry phase was deliberately skipped.** Its first-pass verdicts
are known -- 15 of 15 `no_signal` -- and only the retries were outstanding. A
third container restart killed the chunked resume two tests in, and the
expected yield did not justify a fourth attempt: across the 16 tests that did
receive three attempts each, no retry produced a finding, and `cattrs` is 15
more tests of the same shape. The question it would answer is already answered
by the rest of the corpus.

**Practical note for future runs.** Three container restarts killed long runs,
twice at roughly the two-hour mark. A corpus pass should be driven per project,
in chunks, with results written as it goes: the per-project files are what made
this table recoverable at all, and the chunked resume limited the third loss to
two tests rather than fifteen.

**And check for the process, not the marker.** Progress was twice reported as
"still running" on the strength of a log file that lacked its completion
marker. A dead run looks exactly like a slow one by that test; only `pgrep` or
the container's uptime distinguishes them.

## B26. CrossHair already has the differential and the census; what is left, and what it must cost

Two of the three things proposed for finding CrossHair bugs directly already
exist upstream, in better form.

**The symbolic-vs-concrete differential is `crosshair/fuzz_core_test.py`.** It
enumerates the whole `crosshair.inputgen.catalog` surface, drives each operation
once per call shape, and compares return value, exception type and in-place
mutation via `behavior_compare.run_differential`. It filters nondeterministic
operations (two concrete runs must agree) and identity-eq outputs, and carries
roughly a hundred `KNOWN_FAILURES` grouped by root cause.

**The reachability census is `crosshair/tools/measure_support.py`.** It runs an
operation forward to a concrete output, then asks CrossHair to invert it
(`post: _ != output`) while sweeping input size to find the cliff. Its "black"
cell -- CrossHair falsely confirming that no input yields a known-reachable
output -- is exactly the soundness shape of CrossHair issue #448.

Four things that machinery structurally cannot see, each visible in its own code:

- **Results are realized before they are compared.** `summarize_execution` calls
  `deep_realize` on the return value and `flexible_equal` compares concrete
  values, so a defect in the *symbolic result's own model* is erased before the
  comparison. Issue #516 is this shape: the realized tuple is a fine tuple; the
  bug is that the unrealized result compares equal to a list.
- **Every argument is pinned.** `run_symbolic_pinned` calls `pin_to` on each
  proxy before running, so the solver never branches on an unconstrained value.
  Pinned-path soundness and search soundness are different properties, and only
  the second is what runs under Hypothesis.
- **One expression, one call.** No composition and no sequences. Issue #453
  (mutate a symbolic `array.array`, then compare it) is two steps; it appears in
  `KNOWN_FAILURES` only because `array.extend` happens to diverge alone.
- **The catalog is Python's own surface.** No user-defined classes, so no
  `__eq__`/`__hash__` pairs, dataclasses, inheritance, descriptors, generators,
  or a symbolic stored in a user object's field.

Three extensions follow, all built on `CallSpec` and `run_differential` rather
than beside them: a **composition differential** that chains several catalogued
operations and keeps intermediates symbolic, comparing only at the end; a
**sequence differential** over one symbolic receiver, which catches #453 as a
class rather than per-method; and a **user-defined type surface**, which is the
one place a third-party corpus has an edge the catalog cannot reach.

**The governing constraint is cost, not coverage.** CrossHair's suite already
trades breadth for run time deliberately -- `INPUTS_PER_OP = 3`, `PIN_ITERS =
12`, "deliberately NOT a wide fuzz" -- and a composition or sequence
differential multiplies the surface rather than adding to it. None of these may
land as a broad CI gate. The shape to copy is `measure_support`: an out-of-band
sweep run on demand, emitting a ranked artifact, with only a small pinned
regression set in CI for divergences the sweep has already found. Any proposal
here needs a measured cost-per-finding before it is worth proposing.

**One cheap prioritization signal is available now.** `KNOWN_FAILURES` is a flat
list; many entries are "should realize first". The provider's telemetry already
reports realization sites and unsupported constructs from real third-party runs,
so cross-referencing the two says which of those gaps actually bite under
Hypothesis on real code. This costs a corpus pass we already know how to run,
and it ranks the existing list instead of lengthening it.

## B27. The realization census: one of CrossHair's hundred known gaps is reached by real code

B26 proposed ranking CrossHair's `KNOWN_FAILURES` by what third-party
Hypothesis suites actually hit. This is that measurement: the solver arm and
the telemetry tier only -- no clean room, and the baseline cut to the minimum
the gate needs, because realization sites come from the CrossHair arm alone.
154 tests across `packaging`, `attrs`, `bidict` and `cattrs`, at 8-12 examples
each, chosen for breadth rather than depth. 83 of the 154 realized at least
once. Sites are attributed to their innermost CrossHair frame.

| realizing frame | iterations | tests | projects | catalogued? |
| --- | ---: | ---: | ---: | --- |
| `__format__` (opcode_intercept.py) | 1981 | 61 | 1 | **yes** -- `str.__format__` |
| `_fullmatch` (relib.py) | 437 | 28 | 1 | no |
| `draw_integer` (crosshair_provider.py) | 52 | 26 | 1 | no |
| `__contains__` (simplestructs.py) | 25 | 7 | 1 | no |
| `__getitem__` (simplestructs.py) | 22 | 5 | 2 | no |
| `__ch_deep_realize__` (simplestructs.py) | 14 | 2 | 1 | no |
| `draw_float` (crosshair_provider.py) | 2 | 1 | 1 | no |

One unsupported construct appeared at all: `\s* POSSESSIVE_REPEAT`, 559
iterations across 37 tests.

**The headline is the ratio: 1 of 100.** `KNOWN_FAILURES` enumerates a hundred
soundness gaps, and a corpus of real Hypothesis suites reached exactly one of
them -- `str.__format__`, which then dominates everything else by a factor of
four. The list is not wrong, but it is not ordered by anything a user would
feel, and it is not where the next finding is.

**Most of what real code hits is not catalogued at all.** Six of the seven
frames match no entry, because `inputgen.catalog` enumerates Python's own
operations and these are CrossHair's internals: the regex engine (`relib`) and
the symbolic containers (`simplestructs`). That is B26's fourth gap showing up
in measurement rather than in argument.

**Two entries are attribution artifacts, not defects.** `draw_integer` and
`draw_float` are this plugin's own draw path, where Hypothesis asks for a value
and the provider realizes one. They rank high on test count because every test
goes through them. They are listed because the census should report what it
measured, not a filtered version of it.

**The corpus is narrow, and the table says so.** `packaging` drives all but one
row; `__getitem__` is the only frame seen in two projects, and `bidict` and
`cattrs` produced no realization sites at all. The census measures what these
four libraries reach, not what Python code reaches. Widening it is cheap --
this pass needed no clean room and no validation interpreter, which is roughly
a quarter of a full three-way run -- and that is the argument for running the
CrossHair-defect channel at breadth rather than depth.

## B28. Clustering, and three ways a signature leaks the example

Stage 5's agent triages clusters rather than failures, so `cluster.py` is a
prerequisite for it and is deterministic code, not model work. A failure's
identity is its exception type, the innermost project frame, and a normalized
message. Innermost rather than outermost because the outermost frame of a test
failure is always the test function, which the node id already names.

**Everything that varies per example has to come out of the signature, and
three separate things leak it.** Each was found by running the thing rather
than by reading it.

- **The frame pattern matched across newlines.** `[^:]*` includes `\n`, so a
  "path" could start inside pytest's `E `-prefixed example block and run on
  until it found a `.py:` further down. The first live run returned a frame of
  `E           data=b'0\xe8\xe1>',`. Unit tests on hand-written tracebacks all
  passed, because a hand-written traceback has no example block above the
  frames.
- **Hypothesis's `Falsifying example:` block is folded into the same message**
  as the assertion by pytest, and it is the one part guaranteed to differ
  between two sightings of one defect.
- **pytest's assertion introspection spells out the operands.** The `+ where`
  and `+ and` continuation lines carry the values the example produced, so
  `assert 7 == 8` and `assert 3 == 4` kept distinct signatures even after
  numeric literals were normalized away.

With all three removed, the demo project's three planted defects cluster as
three, and the `IndexError` is attributed to `tinylib.py:22` -- the library --
rather than to the test that called it.

**Two of the first twelve tests passed for the wrong reason**, and only
mutation testing said so. The library-frame test put the project frame last,
so it passed whether or not foreign frames were filtered; the ordering test
used exception names that sorted into the wanted order anyway, so it passed
whether or not clusters were sorted by size. A test that cannot fail is worth
nothing, and neither of these could.

**The plugin's own suite flaked once during this work** -- one failure in a run
that passed twice immediately after, with nothing in the change touching the
plugin. Consistent with B23: that suite runs CrossHair, and CrossHair's search
is not reproducible.

## B29. Triage: the queue, the schema gate, and what scrubbing takes away

Stage 5 puts a model at failure triage. Everything around that judgment stays
deterministic, so this is the queue, the item a decider reads, and the schema
its answer has to satisfy -- the decider itself is a seam.

**The work queue was generalized rather than copied.** It now carries a `kind`
and a JSON payload, so per-test runs and per-cluster triage share one
implementation of claiming, leases, the attempt bound and progress. A second
copy of that logic is how two queues drift apart.

**A decider runs as a subprocess**, one invocation per cluster, reading the
cluster as JSON on stdin and printing a JSON object. That keeps a model out of
this tool's dependencies, and it means a decider that hangs costs a timeout
rather than the run. An answer that does not parse, a non-zero exit and a
timeout each abandon one cluster and leave the batch alone.

**The schema gate is the point, not paperwork.** `parse_verdict` refuses a
bare string, a missing field, an invented category, a confidence outside 0..1
or given as a bool or a word, empty reasoning, and evidence that is not a list
of strings. An answer allowed through unvalidated becomes durable state that
nothing downstream can distinguish from a checked one.

**Scrubbing the signature removes what a reader needs.** The first live run
triaged the demo project's flaky test as `unclear`, because a decider sees the
normalized message -- `assert N < N` -- and the normalization that makes two
sightings of one defect agree had already removed the `random.random()` call
that explains it. A cluster now also carries one unscrubbed traceback, which
affects no identity and restores the context. With it, the same three clusters
come back `project_bug`, `overstrong_property` and `unclear`, and the
`project_bug` is the planted library defect.

**Two mutation anchors went stale** when the queue was generalized, and the
suite reported them as uncaught rather than skipped-and-forgotten. Both were
refreshed and both behaviors are still caught. 28 mutations across the three
suites now fail their tests when introduced.

Still deterministic work before an agent is useful here: nothing yet carries a
triaged cluster onward, so `project_bug` does not reach the trophy track and
`crosshair_artifact` does not reach the CrossHair stream.

## B30. Routing: a trophy needs two things to agree

Triage says what a cluster is; `outcomes.py` says what follows, and refuses
the promotions that do not follow. A cluster becomes a trophy draft only when
the classifier says `trophy_candidate` -- the example was replayed with the
plugin absent and still failed -- *and* triage read the code and called it a
project bug. Either alone has a failure mode the other covers: triage can
misread code, and a replay cannot tell a real defect from an over-strong
property.

`pending_validation` is specifically not enough. It means the replay was
inconclusive, which is not evidence in either direction, so triage calling
something a project bug cannot stand in for the replay that did not run.
Everything triage calls a project bug without that confirmation is **withheld**
and reported with the verdicts that blocked it, rather than dropped.

**The live run withheld for a reason worth recording.** On the demo project,
triage called the `IndexError` at `tinylib.py:22` a project bug and it was
right -- but its verdict is `shared_find`, because random search finds it too.
It is a real bug and not a CrossHair trophy, and the routing says so:
`tinylib.py:22 [shared_find]`. The first wording of that line claimed the
clean-room replay had not confirmed it, which was wrong; the line now prints
the classifier verdicts and explains itself.

The one trophy draft that did come through carries the column the design asks
for, quantified from the run rather than asserted: *the baseline found nothing
in 100 examples across 2 seeds (200 draws)*.

Nothing here reports anything anywhere, and the report says so.

## B31. Scoring a decider, and why accuracy is the wrong headline

Stage 5's agent cannot be adopted on the strength of looking plausible, so
`evaluate.py` scores a decider against clusters whose answer is already known.
The cases in `tools/discovery/cases/triage.jsonl` come from real runs -- the
pipeline's own cluster output -- and are labelled by hand with the reason
recorded alongside each one.

**The errors do not cost the same, so the scorecard does not average them.**
Calling someone else's correct code a bug is the only mistake that can put a
draft in front of a third party, and one of those costs more than several
missed findings. The scorecard counts, separately: *reaching a stranger*
(predicted a project bug where there is none), *lost findings* (a real project
bug sent to a stream nothing revisits), *wrong stream* (our own defect confused
with an over-strong property), *deferred* (answered `unclear` where an answer
existed -- safe, costs time), and *unusable* (nothing that parsed, which is
scored as neither right nor wrong). The summary leads with the dangerous count,
and the command exits non-zero on any of it.

**The baseline is a stub that scores 3 of 4 with zero dangerous errors.** It
defers the CrossHair artifact as `unclear`, which is the right thing to do
when it cannot tell. Any real decider has to beat that, and beating it on
accuracy while reaching a stranger is not beating it.

**One case is a real CrossHair defect, reproduced for the purpose.**
`CrossHairInternal: Numeric operation on symbolic while not tracing` at
`src/attr/_make.py:2668`, reached by two attrs tests and clustered as one.
That run also fired the A/B divergence check, so observability changed the
outcome for those tests as well.

**Mutation testing found one real gap and two stale anchors.** Nothing asserted
that an unusable answer appears in the confusion table, so a decider that
answered nothing could have been indistinguishable from one that answered.
Nine mutations now fail the scorer's tests.

The case set is small -- four clusters, two projects. It is a floor, not a
benchmark, and it grows as runs produce clusters worth labelling.

## B32. The triage decider, and why 4/4 is not a result yet

Stage 5's named deliverable is the agent at failure triage, and
`deciders/claude_decider.py` is it: a subprocess that reads a cluster on
stdin, asks the `claude` CLI, and prints a verdict. It plugs into
`--triage-command` and `--decider` unchanged, because the seam was built
first.

**It gets read-only access to the project** -- `Read`, `Grep`, `Glob`, nothing
that writes or executes. Triage needs the source, and the project is
third-party code this pipeline treats as untrusted; nothing in this judgment
needs to run it. **The prompt states the error asymmetry** the scorecard
measures: calling a project's correct code a bug is the most costly mistake
available, and `unclear` is always safe.

**It scores 4/4 with zero dangerous errors, three runs in a row.** On the
checksum case it read the source and named the actual root cause -- `return
total or 1`, which turns a real checksum of 0 into 1 -- with file and line.
On the attrs case it identified a CrossHair artifact from the traceback alone,
with no source available.

**That number is not yet evidence, and it should not be quoted as though it
were.** Four cases, two projects, and the same person wrote the labels, the
prompt and the scorer. The one thing it does establish is the absence of the
error that matters: across twelve judgments nothing was routed toward a third
party that should not have been. A set this small cannot distinguish a good
decider from one tuned to it, and the honest next step is more labelled
clusters from projects whose failures nobody has looked at yet.

**A case file carrying project names rather than paths gave source access by
accident.** The first scoring run happened to work because the decider's cwd
sat above the fixture. `load_cases` now resolves a relative project path
against the repository, and a case naming a checkout that is not here stays
unresolved, so a decider visibly gets only the cluster.

## B33. Scaling the corpus to ten projects, and a third classifier bug

The corpus went from 4 projects to 10 -- `hyperlink`, `srt`, `natsort`, `h2`,
`priority` and `pyrsistent` join `packaging`, `attrs`, `cattrs` and `bidict` --
and the Goal-2 sweep now covers 335 tests. Doing it by hand was deliberate:
every step is one the stage-6 agent is meant to automate, so the breakages are
requirements rather than anecdotes.

**Candidate discovery is the bottleneck, not provisioning.** Of 15 libraries
examined, 6 had Hypothesis property tests. Picking by "popular pure-Python
computational library" gave 2 of 8; picking from libraries already known to
use Hypothesis gave 4 of 7. Provisioning, by contrast, was clean: all 6 new
projects built and collected with no fixes at all, which puts harness repair
at 3 of 10 overall rather than the 3 of 4 the first batch suggested.

**A third classifier bug, found the way the first two were -- by running it.**
24 natsort tests came back `quarantined_unstable` with the rationale "baseline
outcomes differed across seeds: error", from a run with **one** baseline seed.
One seed cannot differ from itself. The tests error in baseline setup (they are
locale-dependent), and the gate treated any non-pass, non-fail outcome as
disagreement. The gate now judges stability on decisive outcomes only: a
baseline with no pass or fail is `no_baseline_result` and says which outcomes
it saw, while genuine pass-versus-fail disagreement is still unstable. All 191
existing tests passed before the fix, so nothing covered this.

**hyperlink's two `crosshair_false_negative` verdicts hold at three seeds and
200 baseline examples -- and the telemetry says the verdict's name is wrong.**
Every solver iteration realized a symbolic value, 90% were filtered by the
test's own `assume()`, and 8% were productive, with 233 realizations forced at
`(draw data.py:1316) (hostname_labels hypothesis.py:199)`. CrossHair is not
missing a failure random search found; it never searched. The same two tests
also fire the observer-effect check. A verdict that reads "baseline fails but
CrossHair does not" overstates what happened whenever the search was starved.

**The classifier's stated rule and its behavior disagree, and that is a design
question rather than a bug to fix unilaterally.** `classify.py` opens with
"Tier-B telemetry may be attached as supporting evidence, but never decides a
verdict", yet `QUARANTINED_NONDETERMINISTIC` is decided from telemetry. If that
precedent stands, a degraded search should downgrade a false negative the same
way; if the rule stands, that branch belongs outside `classify`. Worth a
decision before either is extended.

### The census at ten projects

335 tests, 192 of which realized at least once.

| realizing frame | iterations | tests | projects | catalogued? |
| --- | ---: | ---: | ---: | --- |
| `__format__` (opcode_intercept.py) | 2044 | 67 | 2 | **yes** -- `str.__format__` |
| `_fullmatch` (relib.py) | 437 | 28 | 1 | no |
| `__getitem__` (simplestructs.py) | 420 | 44 | **4** | no |
| `_find` (abcstring.py) | 144 | 16 | 1 | no |
| `__repr__` (opcode_intercept.py) | 116 | 17 | 2 | no |
| `__mod__` (opcode_intercept.py) | 96 | 4 | 2 | **yes** -- `float.__mod__` |
| `draw_integer` (crosshair_provider.py) | 65 | 34 | 4 | artifact of this plugin |
| eight more | <35 each | | | no |

**Breadth now disagrees with frequency, and breadth is the better signal.**
`__getitem__` on symbolic containers is reached by four unrelated libraries;
`__format__` outweighs it four to one in raw iterations but appears in two, and
most of that is still packaging. A frame many unrelated projects hit is more
likely to matter generally than one a single project hammers.

**The one-in-a-hundred ratio survived the scaling.** Catalogued gaps reached
went from 1 to 2 of 100; uncatalogued frames went from 6 to 12. Tripling the
corpus roughly doubled both, and did not change the conclusion that real
Hypothesis suites spend their time in CrossHair's own internals -- the regex
engine, the symbolic containers, string find -- rather than in the catalogued
Python surface that `fuzz_core_test` enumerates.

## B34. Observability may not decide anything

B33 left open whether `classify.py`'s stated rule or its behavior was wrong.
The rule is right, and the reason is stronger than consistency: a
CrossHair-backed observability run realizes symbolic draws and perturbs the
search, so it is **known to diverge** from the run being judged. An
observability run gives clues for running other, more trustworthy tests. It
settles nothing.

**Two verdicts were being decided from it, not one.** Alongside
`QUARANTINED_NONDETERMINISTIC`, the `STABLE_FAIL` branch read
`_claims_exhausted(stats)` to choose between `SOUNDNESS_SUSPECT` and
`CROSSHAIR_FALSE_NEGATIVE` -- and "exhausted all paths" is a completion count,
which only exists in the telemetry tier. Both are gone from `classify`, and a
test now asserts the rule itself rather than one instance of it: for every
baseline/CrossHair combination, the verdict with loud telemetry attached must
equal the verdict without it.

**Both verdicts are removed rather than left unreachable.** Nothing could
produce them once telemetry was disallowed, and an enum member nothing
produces is the same dead state as the cache that nothing called. Neither had
ever been produced on the corpus. Bringing `soundness_suspect` back is worth
doing, because an unsoundness claim is the most valuable thing this pipeline
could report -- but it needs a tier-A source. CrossHair's exhaustion claim
would have to reach the verdict tier some other way than through
observability, which it currently does not.

**What those observations became.** `telemetry.clues_from` reports them as
clues, each naming the run that would settle it: discarded-for-nondeterminism
iterations, an exhaustion claim, and a search that ran mostly concretely
under a verdict that depends on the solver having searched. The report prints
them under a heading that says they are never verdicts.

On hyperlink, live:

    clues from the observability tier  (1, never verdicts)
        ...::test_hostnames_ascii
            100% of solver iterations realized a symbolic value, so the
            search ran mostly concretely
            -> re-run with a larger budget before treating
               'crosshair_false_negative' as evidence the solver explored this

**Two bugs surfaced in wiring that up, both from running it.** Telemetry is
keyed by Hypothesis property name while everything else is keyed by pytest
node id, so the first version matched nothing; `pipeline.stats_for` already
bridged that and is now shared rather than private. Then the clue still did
not fire, because a node id can carry an `observer_effect` classification
*alongside* its real verdict, and building a node-id-to-verdict map let the
annotation overwrite the verdict it should have been read against.

---

## B35. Candidate triage, and the asymmetry that runs the other way

Stage 6 begins at the measured bottleneck. B33 found that of 15 libraries
examined, 6 had Hypothesis property tests, while harness repair was needed for
only 3 of 10 provisioned projects and 0 of the 6 newest. Finding candidates is
the expensive part; fixing them is not.

**The error asymmetry is inverted relative to failure triage.** In `triage.py`
the costly mistake is calling someone else's correct code a bug, because that
can reach a stranger, so `unclear` is always safe and the decider is pushed
toward deferring. Here the costly mistake is the opposite one. Admitting a dud
costs one provisioning and one run, and the report says so. Rejecting a project
that would have produced findings costs those findings permanently: nothing
downstream revisits a project that was never queued, and no counter anywhere
records what was lost.

So `candidates.py` rejects on exactly one fact -- no `@given` test, no state
machine, and no marker in a file it could not read -- and expresses every other
concern as a score that orders the queue. A low score is a late slot, never a
closed door. `test_the_worst_possible_candidate_is_still_runnable` asserts that
directly: numpy, requests, three fixtures, score under 40, still runnable.

**Parsing with the host interpreter silently hides tests.** The survey reads
source with `ast.parse`, which uses the grammar of the Python running the
survey, not the one the project targets. On the corpus this dropped 34 files --
31 in pint, 3 in cattrs -- all of them PEP 695 syntax that 3.11 cannot parse.
Those particular files held no `@given`, which was luck. A project whose only
property tests sat in such a file would have been rejected as having none,
which is precisely the silent false rejection above. Unparsable files now get a
textual marker scan; it cannot give strategies or fixtures, but it answers the
only question that triggers a rejection, and `hidden_tests` keeps the project
in the queue.

**The scanner agrees with grep exactly.** Across the 11 corpus projects that
have property tests, the AST count matches `grep -c @given` on every one:
attrs 59, bidict 8, cattrs 134, dateutil 4, h2 21, hyperlink 14, jsonschema 1,
natsort 31, packaging 389, priority 7, srt 44. The 8 rejections are all genuine
-- those projects import `hypothesis` nowhere at all.

**One scoring signal was backwards and is now inverted.** `plain_strategies`
scored a test down for naming a strategy not in a hardcoded list of Hypothesis
builtins. Every one of packaging's 13 "unrecognized" strategies turned out to
be a project-defined `@st.composite` -- `pep440_versions`, `release_segment`,
`pre_tags` -- so the signal was docking 8 points for having a mature property
suite. It is now `domain_strategies` and points the other way, which is also
the better theory: a suite random search has already hammered for years is
where symbolic execution has the most left to find.

**The score does not predict yield, and cannot yet be shown to.** Against the
real runs, all 10 non-`no_signal` events landed in the top half of the ranking
and the bottom half produced zero across 115 tests. That is not evidence: the
weights were chosen after seeing these projects, so it is a consistency check,
not a held-out prediction. And packaging is a flat counterexample -- the
highest score in the corpus, 68 tests run, nothing found. natsort's 24 results
are excluded as they came from the classifier bug fixed in B33.

The weights are a stated preference about where CrossHair pays off, which the
`SCORE_WEIGHTS` docstring says outright. They earn their numbers only by being
revised against what the corpus yields.

**A bug the tests caught.** `import hypothesis.strategies` binds the name
`hypothesis`, but the alias map pointed that name at the full dotted path,
clobbering a plain `import hypothesis` so that `@hypothesis.given` resolved to
`hypothesis.strategies.given` and matched nothing. Any project using that
import form would have had every property test missed.

**An unobservable guarantee, removed the same way B34 removed one.** `rank`
sorted on `(not runnable, -score)`, but a blocked project always scores 0, so
the first key could never change an outcome -- a mutation deleting it was
uncaught because it was unobservable, not because the test was weak. Ordering
is now `order()`, taking assessments rather than paths, so the contract can be
tested on constructed input instead of depending on how `assess` happens to
score a rejection.

Open: `st.randoms()` is reported as a note rather than scored, because one
instance in the corpus (bidict) is not enough to weight. Strategy-level fit
generally needs measuring against outcomes before it earns a number.

---

## B36. Harness repair, and a flaw in every mutation suite

The second half of stage 6. B33 measured harness repair as rarer than the
design assumed -- 3 of 10 provisioned projects, 0 of the 6 newest -- so this
is built from the three failures actually recorded rather than from imagined
ones.

**A repair may change the environment. It may never change the suite.** A
"repair" that edited a test could turn a failing assertion into a passing one,
or a passing one into a finding, and the run would then manufacture results
rather than discover them. `Repair` can express packages, environment
variables and pytest arguments. It has no field that can name a file, so the
dangerous repair is unrepresentable rather than merely forbidden -- the same
move as the sandbox holding no credentials. A test asserts the field set
directly, and a mutation that adds a `patch_file` field is caught.

**The real failures were more uniform than expected.** Both plugin cases --
cattrs needing pytest-benchmark, bidict needing pytest-xdist -- surface the
same way, because the project's own `addopts` names flags the plugin supplies
and pytest refuses to start:

    python -m pytest: error: unrecognized arguments: --benchmark-sort=fullname
    --benchmark-warmup=true --benchmark-warmup-iterations=5

So the diagnosis is a flag-prefix table, and the flag is in the error text
itself. A project writing `addopts` with spaces rather than `=` makes argparse
echo the flag's *value* as a separate token, which is why only tokens starting
with `-` are reported as flags.

**xdist is installed and then stopped from working.** It moves tests into
subprocesses the injected plugin never reaches, so a suite configured for it
gets `-n0`. This was applied by hand during provisioning and had never reached
the pipeline, which means any project whose `addopts` carried `-n auto` would
have run the injected plugin under xdist -- the thing that broke bidict.

**Diagnosis is in the pipeline; applying is not.** Installing a package needs
the network, and the safety model allows network only during the install
phase, never during collection. So a repairable collection failure prints the
repair and exits 3, an unrecognized one exits 2 and asks for a person, and the
orchestrator outside applies and re-runs. Verified live against hyperlink on
both paths.

**The shallow-clone version bug is real but not a repair.** A clone with no
tags makes setuptools_scm report `0.1.dev1` instead of `25.4.0`. It did not
break the install on retry, so it is not encoded as a repair; it matters
because a trophy report naming `attrs 0.1.dev1` would be wrong. Provenance is
keyed on the commit rather than the version, so nothing cached is poisoned.

**A flaw in all nine mutation suites, found by tripping over it.** The suites
copy the target aside, mutate, run, and restore. When a mutated line happens
to have the same length as the original, the restored file can match the size
and mtime recorded in `__pycache__`, and Python reuses a `.pyc` compiled from
the *mutated* source. A real test failure appeared against clean source, which
is how it surfaced. Every suite now clears bytecode before each mutation and
runs with `PYTHONDONTWRITEBYTECODE`.

This is worth stating plainly: until this was fixed, any suite's result could
have been scored against the wrong source, in either direction. All nine were
re-run from scratch afterwards and all report every mutation caught, which is
the first time that claim has been trustworthy.

---

## B37. Candidate discovery: clone, do not guess

B33 named candidate discovery the bottleneck, and the fix turns out not to
need an agent at all.

**PyPI metadata cannot answer the question.** Hypothesis is a development
dependency, and only runtime dependencies are published, so no amount of
metadata says whether a project has property tests. The design doc anticipated
this and proposed GitHub code search; a shallow clone plus `candidates.survey`
answers it exactly in about a second, which is cheaper than reasoning about it
and is not a guess. So the prefilter only ranks, and the probe decides.

The source is `hugovk/top-pypi-packages`, which is the public PyPI download
ranking -- built now from ClickHouse's PyPI tables rather than BigQuery, same
lineage. 15,000 names, no credentials. Per-package metadata comes from the
PyPI JSON API, cached on disk.

**The one metadata signal that measures something real is the wheel tag.** A
package publishing only `-any.whl` has no C extension for CrossHair to realize
at; `numpy` ships `manylinux` and `macosx` wheels and `cattrs` ships `any`.
That is the design's "exclude numpy/pandas/torch-centric code" rule measured
directly rather than guessed from imports. A package with no wheel at all is
unknown rather than compiled, and scores between the two.

**The prefilter score saturates and is not claimed to rank.** Across the top
300 packages, most are pure Python with no array dependency and large download
counts, so most score 100. That is honest about what it is: a way to drop the
unfit, not a way to order the fit. The survey does the ordering.

**Measured, over the 30 most downloaded clonable packages:** 6 carry
Hypothesis property tests, found in 54 seconds of cloning. The hit rate, 20%,
is lower than B33's hand-picked 6 of 15, but it costs 1.8 seconds per
candidate rather than a judgment call.

**Over 150 repositories, through the CLI:** 13 carry property tests and 3
were refused on size. Seven are candidates the corpus did not have --
`sympy` (22 property tests), `idna` (17), `pydantic` (9), `hpack` (9),
`jmespath` (7), `virtualenv` (7), `sentry-sdk` (3) -- against six it already
had. The hit rate falls from 20% in the top 30 to 8.7% over 150, which is
what one would expect: the most downloaded packages are the best tested.

That is the bottleneck gone. B33 found 6 candidates from 15 libraries by
hand; this found 7 new ones from 150 repositories without a judgment call,
and the limit now is how many can be provisioned rather than how many can be
found.

**Two bugs found by running it wide, both about cost rather than correctness.**

A depth-1 clone of `google-cloud-python` is 1.4GB. `--filter=blob:limit=1m`
does not help, because the size is not large blobs: it is a monorepo with an
enormous tree of small generated files. A checkout now has a megabyte budget,
and one over it is deleted unread and reported as needing a bigger budget
rather than as having no tests.

Worse, 11 of the top 300 packages are published from that one repository, and
7 more from `opentelemetry-python`. Probing per package rather than per
repository would have cloned it 11 times -- about 15GB to read the same tree
over and over. The shortlist now yields one checkout per repository, keeping
the best-scoring package as its representative. Across 289 clonable packages
that is 266 repositories, so 23 clones were pure waste.

**A checkout is third-party source no gate has passed.** It is cloned at depth
1, read, and deleted. Nothing in it is imported, installed or run, which is
what makes it safe to do this to hundreds of unknown projects.

Open: the prefilter cannot see repository size before cloning, so the budget
is enforced after the download rather than before it. GitHub's API would say,
but that means a call per candidate against repositories outside this
project.

---

## B38. Running the seven new candidates

Provisioned and swept the candidates B37 found. The sweep ran without the
telemetry tier, since B34 established it cannot decide a verdict and it
roughly doubles the cost, at a 120s CrossHair budget rather than the 900s
default.

| project | result |
| --- | --- |
| jmespath | 7 no_signal |
| hpack | 9 no_signal |
| virtualenv | 7 no_signal, after a repair |
| idna | 10 crosshair_timeout, 7 no_signal |
| pydantic | 1 pending_validation, 8 no_signal |
| sympy | 2 of 22, too slow to finish here |
| sentry-sdk | not run; needs the skip-unimportable repair |

**Provisioning found three repair classes, all now encoded (B36).** virtualenv
sets `addopts = -m 'not property'`, so its own configuration deselects every
property test and the baseline reported `not_run` for all seven; `-m ""`
overrides it and they then run. pydantic's strict marker configuration aborts
on `thread_unsafe`, registered by pytest-run-parallel. sentry-sdk's optional
integration modules cannot be imported without extras.

**A gap the repairs do not cover.** pydantic collected cleanly and then
errored on every test, because its conftest imports `jsonschema` inside a
fixture body. Diagnosis reads collection output, so it never saw it. A run
where every test comes back `no_baseline_result` should feed the baseline
arm's output to `harness.diagnose`, which it currently does not.

**idna's timeouts were mostly the budget.** Re-running two of them at the
900s default split: `test_encode` became `no_signal`, `test_decode` still
timed out. So one is an artifact of the short sweep and one is a real wall at
the full budget. A sweep budget buys breadth and costs exactly this kind of
certainty, which is worth stating whenever a sweep's `crosshair_timeout`
count is quoted.

**pydantic produced a defect in our own provider -- finding 5.** The
`pending_validation` test fails with `IndexError` inside Hypothesis's
`datetimes()` strategy. `draw_integer(0, 30, shrink_towards=24)` returned
9999, which is `datetime.MAXYEAR`: a value recorded for a *year* draw handed
to an *index* draw during concrete double-check replay. `_replayed_draw`
accepts a popped value on `isinstance(value, expected_type)` alone, so the
bounds are never applied -- the check in `draw_integer` sits after that early
return, and the same holds for float, string and bytes.

Two things about it are worth keeping.

It only reproduces when the test body realizes the value **at a C boundary**.
`repr()` and `.isoformat()` do not trigger it; `pydantic_core.validate_python`
does. Realizing changes which branches a strategy takes, so the replay's draw
sequence no longer lines up with the recording.

And instrumenting it makes it disappear. Wrapping `draw_integer` to realize
and bounds-check every result turned a 3-of-3 failure into a pass, which is
the B34 observation problem in a new place: the fix had to record symbolically
and realize only inside the `except`.

**The differential caught it, which is the point.** An out-of-bounds draw
surfaces as a plain `IndexError` in someone else's library, with nothing to
suggest the backend produced an impossible value -- a trophy-manufacturing
machine. The clean-room replay did not reproduce it without the plugin, so the
verdict was `pending_validation` and not `trophy_candidate`.

---

## B39. Fixing the replay bounds check

The defect B38 found is fixed. `_replayed_draw` now takes the constraints the
draw asked for and discards the test case when the popped value does not
answer them, which is the handling already in place for a replay whose types
no longer line up.

All four constrained draws pass theirs: integer bounds, float range plus nan
and smallest-nonzero-magnitude, string alphabet and length, bytes length.
`draw_boolean` has no constraint to check.

**The fix cannot break a working replay.** `doublecheck_inputs` holds the
realized results of draws the symbolic run already constrained, so a value
that answered its draw then still answers it now. Only a misaligned replay --
where the value belongs to a different draw -- can fail the check, and that
case was already meant to be discarded.

Measured: the reproduction goes from failing on 3 of 3 seeds to passing on 3
of 3, and the pipeline's verdict for pydantic's `test_datetime_datetime` goes
from `pending_validation` to `no_signal` on 3 of 3 runs. Eleven regression
tests were added; seven of them fail with the check removed.

Worth noting what this does *not* do. It converts a corrupted test case into
a discarded one, which is right, but a replay that desynchronizes is still
desynchronizing. If discards become common on a project, the cause is the
draw sequence shifting under realization rather than the check being too
strict, and that is the thing to measure next.
