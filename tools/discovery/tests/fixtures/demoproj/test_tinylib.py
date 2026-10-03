import random

import tinylib
from hypothesis import given, settings
from hypothesis import strategies as st


@given(st.binary(min_size=4))
def test_checksum_matches_reference(data):
    # Only fails when the true checksum is exactly zero: needs a solver.
    assert tinylib.checksum(data) == tinylib.reference_checksum(data)


@given(st.text())
def test_first_word_returns_str(text):
    # Raises on blank input: trivially findable by either arm.
    assert isinstance(tinylib.first_word(text), str)


@settings(backend="hypothesis")  # explicit pin; the runner must override it
@given(st.integers(), st.integers(), st.integers())
def test_clamp_within_bounds(value, low, high):
    if low > high:
        return
    assert low <= tinylib.clamp(value, low, high) <= high


@given(st.text())
def test_slugify_is_idempotent(text):
    once = tinylib.slugify(text)
    assert tinylib.slugify(once) == once


@given(st.integers())
def test_flaky(value):
    assert random.random() < 0.003
