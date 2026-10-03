# CrossHair findings, ready to file

Findings 1-4 are in CrossHair; finding 5 is in this repository's own
provider and was found by running the discovery pipeline over pydantic.

Three findings in `crosshair/libimpl/relib.py`, all confirmed present on
`main` at `ad4a8d0` (0.0.110) and reproduced against the installed 0.0.109.

They surfaced while investigating why `packaging`'s `Version` parse makes no
progress under `backend="crosshair"` (see B11 in `backlog.md`). Findings 1 and
2 are capability gaps that cause a silent fallback to concrete matching;
finding 3 is a defect.

Not filed from the agent session: attaching `pschanely/CrossHair` for write was
refused by the permission classifier. The drafts below are meant to be pasted
as-is.

---

## 1. `POSSESSIVE_REPEAT` is unhandled, silently disabling symbolic matching

**Repro** (both patterns accept the same language):

```python
import re
from hypothesis import given, settings, strategies as st, Phase

SET = settings(backend="crosshair", max_examples=100, deadline=None,
               database=None, phases=[Phase.generate])

def check(pattern):
    compiled = re.compile(pattern)
    @SET
    @given(st.text())
    def t(s):
        assert compiled.fullmatch(s) is None
    try:
        t(); print(f"{pattern!r:20s} NOT cracked")
    except AssertionError:
        print(f"{pattern!r:20s} cracked")

check(r"[0-9]*abcdef")     # cracked
check(r"[0-9]*+abcdef")    # NOT cracked
```

The greedy form is solved; the possessive twin is not. Internally:

```python
>>> _match_pattern(re.compile(r"a*+"), symbolic_str, 0, None)
ReUnhandled: POSSESSIVE_REPEAT
```

Same for `(?:ab)*+`, `x?+`, `[a-z0-9]++`. `POSSESSIVE_REPEAT` appears nowhere
in `relib.py`, so it falls through to `raise ReUnhandled(op)` at the end of
`_internal_match_patterns`, and `_fullmatch` then realizes the string and
delegates to concrete `re`.

The realization is silent: the iteration still reports `completed normally`,
so from the outside the run looks healthy while doing random search.

Possessive repeat is `(?>x*)` -- atomic, no backtracking -- so it may be
easier to encode than the greedy form already supported.

**Impact:** `packaging` uses 12 possessive quantifiers in `VERSION_PATTERN`
on CPython >= 3.11.5, so every symbolic `Version(...)` parse degrades to
concrete.

---

## 2. `SUBPATTERN` with inline flags is unhandled

**Repro:**

```python
check(r"(?:[0-9]{6})")     # cracked
check(r"(?a:[0-9]{6})")    # NOT cracked
```

`relib.py:651`:

```python
elif op is SUBPATTERN:
    groupnum, _a, _b, subpatterns = arg
    if (_a, _b) != (0, 0):
        raise ReUnhandled("unsupported subpattern args")
```

`_a` and `_b` are the per-group add/del flags, so any `(?a:...)`, `(?i:...)`
or similar scoped-flag group disables symbolic matching for the whole pattern.
Threading the flags through `_internal_match_patterns` rather than asserting
them zero would fix it.

**Impact:** this is the binding constraint for `packaging`. Its
`VERSION_PATTERN` has two `(?a:` groups, and so does the `_VERSION_PATTERN_OLD`
kept for pre-3.11.5 interpreters -- so removing the possessive quantifiers
alone changes nothing (measured: 17 code locations to 20). Fixing this one
first is what unblocks the pattern.

---

## 3. `unicode_ignorecase_mask` builds a pattern from an unescaped character

**Repro:**

```python
>>> from crosshair.libimpl.relib import unicode_ignorecase_mask
>>> unicode_ignorecase_mask(ord('+'))
re.error: nothing to repeat at position 0
>>> unicode_ignorecase_mask(ord('('))
re.error: missing ), unterminated subpattern at position 0
>>> unicode_ignorecase_mask(ord('a'))    # fine
```

`relib.py:127`:

```python
matches = re.compile(chr(cp), re.IGNORECASE).findall(chars)
```

`chr(cp)` is interpolated into a pattern without escaping, so any
metacharacter codepoint raises. `re.escape(chr(cp))` is the fix.

Reached through `single_char_mask` -> `_internal_match_patterns` whenever an
`IGNORECASE` pattern matches a literal metacharacter against a symbolic
string:

```python
>>> _match_pattern(re.compile(r"\+", re.IGNORECASE), symbolic_str, 0, None)
re.error: nothing to repeat at position 0
```

`re.error` is not caught by `_fullmatch`, which handles only `ReUnhandled`, so
this propagates rather than falling back.

**Caveat, stated because it matters for triage:** this is currently masked in
practice. On `packaging`'s pattern, findings 1 and 2 bail out first, and I was
**not** able to reproduce it through the Hypothesis path -- the string tends to
realize before matching reaches the metacharacter. It is reproducible at the
`_match_pattern` and `unicode_ignorecase_mask` levels only, so treat the
user-facing impact as unproven. It would become reachable once 1 and 2 are
fixed.

---

## 4. `CrossHairInternal: Numeric operation on symbolic while not tracing` on `attrs`

Unlike findings 1-3 this needs no fault injection and no synthetic pattern: it
fires on `attrs`' own test suite, unmodified, under `backend="crosshair"`.

**Repro** (from an `attrs` checkout, with `hypothesis-crosshair` installed):

```
pytest tests/test_funcs.py::TestAssoc::test_no_changes \
       tests/test_funcs.py::TestEvolve::test_change
```

with settings forced to `backend="crosshair"`, `max_examples=300`,
`deadline=None`, `database=None`. Both fail with:

```
crosshair.util.CrossHairInternal: Numeric operation on symbolic while not tracing
```

**It is budget-dependent, which is worth knowing before triage.** The same
tests pass cleanly at `max_examples=30`; two attempts to reduce this to a
smaller hand-written case (a plain `@attr.s` class, and `simple_classes()`
driven directly at 30 examples) did **not** reproduce it. So the trigger needs
enough iterations to reach whatever state breaks the tracing invariant, and a
minimal reproduction is still outstanding.

**Observed on seven tests** in one run of `attrs` at 300 examples:
`TestAssoc::{test_no_changes,test_change,test_unknown}`,
`TestEvolve::{test_no_changes,test_change,test_unknown}`, and
`TestAsDict::test_asdict_preserve_order`. The two named above were re-run in
isolation and reproduced, so it is not an artefact of running twelve tests in
one process.

The error surfaces as an ordinary pytest failure rather than on stderr, which
is worth noting for anyone building tooling on top: a harness watching stderr
for internal errors will score this as a finding about the code under test.

---

## 5. `_replayed_draw` checks a value's type but not the bounds it was asked for

This one is in **this repository**, not CrossHair:
`hypothesis_crosshair_provider/crosshair_provider.py`.

**What happens.** On concrete double-check replay, every `draw_*` returns
early through `_replayed_draw`, which pops the next recorded value and
accepts it if `isinstance(value, expected_type)`. The `min_value` and
`max_value` the caller asked for are never applied, because the bounds check
in `draw_integer` sits after that early return. The same holds for
`draw_float`, `draw_string` and `draw_bytes` and their own constraints.

A replay can desynchronize, because realizing a value changes which branches
a strategy takes and therefore how many draws it makes. When it does, a value
recorded for one draw is handed to a different draw. The existing guard
catches that only when the types differ.

**Repro.** Needs a C extension in the test body: realizing at that boundary
is what shifts the draw sequence. Pure-Python realization did not trigger it.

```python
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic_core import SchemaValidator
from pydantic_core import core_schema as cs

SCHEMA = SchemaValidator(cs.datetime_schema())

@settings(backend="crosshair", max_examples=50, deadline=None, database=None)
@given(st.datetimes())
def test_realizing_body(value):
    assert SCHEMA.validate_python(value) == value
```

```
IndexError: tuple index out of range
while generating 'value' from datetimes()
hypothesis/strategies/_internal/datetime.py:533
```

**The actual cause**, recorded by wrapping `ConjectureData.draw_integer` and
realizing only inside the `except`:

```
LAST DRAW: draw_integer(0, 30, {'shrink_towards': 24}) returned 9999
```

`st.datetimes()` asked for an index into a 31-element tuple and got 9999,
which is `datetime.MAXYEAR` -- a value recorded for a *year* draw. It is an
`int`, so the type guard passed.

**Why it matters more than the crash.** The exception surfaces inside the
strategy or the project, with no sign that the backend produced an impossible
value. A pipeline looking for bugs in third-party code sees a plain
`IndexError` in someone else's library. It is a trophy-manufacturing machine:
left unchecked it generates bug reports about correct code.

Our own run classified it `pending_validation` rather than
`trophy_candidate`, because the clean-room replay without the plugin did not
reproduce it. That is the three-way differential doing exactly the job it was
built for.

**Suggested fix.** Validate the popped value against the request, not just its
type, and raise `BackendCannotProceed("discard_test_case")` on a mismatch --
the handling already in place for a desynchronized replay. That converts
silent corruption into a discarded test case.

Also worth considering: a mismatch means the replay queue is misaligned, so
every later draw in that replay is suspect too.

---

## What fixing these is expected to buy

Stripping both unhandled constructs from `VERSION_PATTERN` by hand raises the
per-iteration cost from 0.044s to 12.6s -- the signature of symbolic work
actually happening -- while code locations rise only from 17 to 27. So the
likely outcome is "slow and still limited" rather than "solved", and the
budget question becomes the real one afterwards. The stripped pattern also
trips finding 3, so that measurement is contaminated to an unknown degree.
