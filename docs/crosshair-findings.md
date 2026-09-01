# CrossHair findings, ready to file

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

## What fixing these is expected to buy

Stripping both unhandled constructs from `VERSION_PATTERN` by hand raises the
per-iteration cost from 0.044s to 12.6s -- the signature of symbolic work
actually happening -- while code locations rise only from 17 to 27. So the
likely outcome is "slow and still limited" rather than "solved", and the
budget question becomes the real one afterwards. The stripped pattern also
trips finding 3, so that measurement is contaminated to an unknown degree.
