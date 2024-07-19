import math
import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import (RuleBasedStateMachine, rule,
                                 run_state_machine_as_test)


def test_int():
    @settings(backend="crosshair")
    @given(st.integers())
    def hypothesis_test(d: int):
        assert d != 424242

    with pytest.raises(AssertionError, match=re.escape("assert 424242 != 424242")):
        hypothesis_test()


def test_float():
    @settings(backend="crosshair")
    @given(st.floats())
    def hypothesis_test(f: float):
        assert f != 42.42

    with pytest.raises(AssertionError, match=re.escape("assert 42.42 != 42.42")):
        hypothesis_test()


def test_float_in_bounds():
    @settings(backend="crosshair")
    @given(st.floats(min_value=0.04, max_value=0.06))
    def hypothesis_test(f: float):
        assert f != 0.05

    with pytest.raises(AssertionError, match=re.escape("assert 0.05 != 0.05")):
        hypothesis_test()


def test_float_out_of_bounds():
    @settings(backend="crosshair")
    @given(st.floats(min_value=0.03, max_value=0.04))
    def hypothesis_test(f: float):
        assert f != 0.05

    hypothesis_test()


def test_float_can_produce_nan():
    @settings(backend="crosshair")
    @given(st.floats(allow_nan=True))
    def hypothesis_test(f: float):
        assert not math.isnan(f)

    with pytest.raises(AssertionError):  # , match=re.escape("assert 0.05 != 0.05")):
        hypothesis_test()


def test_string():
    @settings(backend="crosshair")
    @given(st.text(min_size=3, max_size=3))
    def hypothesis_test(s: str):
        assert isinstance(s, str) and len(s) == 3

    hypothesis_test()


def test_list():
    @settings(backend="crosshair")
    @given(st.lists(st.integers()))
    def hypothesis_test(d: list[int]):
        assert d != [42, 123]

    with pytest.raises(
        AssertionError, match=re.escape("assert [42, 123] != [42, 123]")
    ):
        hypothesis_test()


def test_set():
    @settings(backend="crosshair")
    @given(st.sets(st.integers()))
    def hypothesis_test(d: set[int]):
        assert d != {42, 123}

    with pytest.raises(
        AssertionError, match=re.escape("assert {42, 123} != {42, 123}")
    ):
        hypothesis_test()


def test_dict():
    @settings(backend="crosshair")
    @given(st.dictionaries(st.integers(), st.integers()))
    def hypothesis_test(d: dict[int, int]):
        assert d != {42: 123}

    with pytest.raises(
        AssertionError, match=re.escape("assert {42: 123} != {42: 123}")
    ):
        hypothesis_test()


def test_date():
    @settings(backend="crosshair")
    @given(st.dates())
    def f(d):
        pass

    f()


def test_bool_probabilities():
    # Regression test for https://github.com/pschanely/hypothesis-crosshair/issues/18

    @run_state_machine_as_test
    @settings(backend="crosshair", deadline=None)
    class IntListRules(RuleBasedStateMachine):
        @rule()
        def a(self):
            pass

        @rule()
        def b(self):
            pass
