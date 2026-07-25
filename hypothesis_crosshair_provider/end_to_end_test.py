import math
import os
import re

import pytest
from crosshair.core import is_tracing
from crosshair.util import PathTimeout
from hypothesis import given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, rule, run_state_machine_as_test


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


def test_proxy_intolerance():
    @settings(backend="crosshair")
    @given(st.text())
    def f(t):
        # ideally we keep this up-to-date with some C function that will not accept symbolics:
        os.fspath(t)

    f()


def test_datetimes_can_generate_in_few_iterations():
    @given(st.datetimes())
    @settings(backend="crosshair", deadline=None, max_examples=10)
    def f(n):
        raise Exception("generated one")

    with pytest.raises(Exception, match="generated one"):
        f()


def test_nonrepresentable_float_bound_is_not_reported_as_invalid_argument():
    # Regression for https://github.com/pschanely/CrossHair/issues/491:
    # a real-approximated symbolic float can realize to a value with no exact
    # IEEE-double representation. Feeding it to st.floats(width=64) used to leak
    # a spurious InvalidArgument out of the provider. Here (n + 1) / n is such a
    # value for every n in range (1 + 1/n rounds to 1.0, but is not equal to it).
    @settings(backend="crosshair", max_examples=5, deadline=None)
    @given(st.integers(min_value=10**20, max_value=10**21))
    def hypothesis_test(n):
        x = (n + 1) / n
        st.floats(min_value=x, width=64)

    hypothesis_test()


def test_incomplete_exhaustion_does_not_claim_verified():
    @given(st.integers())
    @settings(backend="crosshair", deadline=None, max_examples=10)
    def f(n):
        if n > 10:
            if is_tracing():
                raise PathTimeout
            else:
                assert False, "hypothesis will find this error"

    try:
        f()
    except AssertionError as e:
        exc_text = str(e) + " ".join(e.__notes__)
        print("exc_text", exc_text)
        assert "please send them a bug report" not in exc_text
