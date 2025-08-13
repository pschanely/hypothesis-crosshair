import types
from unittest.mock import patch

import pytest
from crosshair.util import (
    IgnoreAttempt,
    NotDeterministic,
    UnexploredPath,
    UnknownSatisfiability,
)
from hypothesis import settings
from hypothesis import strategies as st
from hypothesis.errors import BackendCannotProceed
from hypothesis.internal.conjecture.provider_conformance import run_conformance_test
from hypothesis.internal.intervalsets import IntervalSet

from hypothesis_crosshair_provider.crosshair_provider import CrossHairPrimitiveProvider


class TargetException(Exception):
    pass


def _example_user_code(s_bool, s_int, s_float, s_str, s_bytes):
    if s_bool:
        if s_int == 120:
            if s_float < 2.0:
                if s_str == "foo":
                    if s_bytes == b"b":
                        raise TargetException


def test_basic_loop():
    provider = CrossHairPrimitiveProvider()
    found_ct = 0
    for _ in range(30):
        try:
            with provider.per_test_case_context_manager():
                s_bool = provider.draw_boolean()
                s_int = provider.draw_integer()
                s_float = provider.draw_float()
                s_str = provider.draw_string(
                    IntervalSet.from_string("abcdefghijklmnopqrstuvwxyz")
                )
                s_bytes = provider.draw_bytes(1, 1)
                assert type(s_bool) == bool
                assert type(s_int) == int
                assert type(s_float) == float
                assert type(s_str) == str
                assert type(s_bytes) == bytes
                _example_user_code(s_bool, s_int, s_float, s_str, s_bytes)
            # assert provider.completion == "completed normally"
            assert type(provider.export_value(s_bool)) == bool
            assert type(provider.export_value(s_int)) == int
            assert type(provider.export_value(s_float)) == float
            assert type(provider.export_value(s_str)) == str
            # NOTE: draw_bytes can raise IgnoreAttempt, which will leave the bytes
            # symbolic without a concrete value:
            assert type(provider.export_value(s_bytes)) in (bytes, types.NoneType)
        except BackendCannotProceed:
            pass
        except TargetException:
            found_ct += 1
    assert found_ct > 0, "CrossHair could not find the exception"


def test_post_run_value_export():
    provider = CrossHairPrimitiveProvider()
    with provider.per_test_case_context_manager():
        s_int = provider.draw_integer()
        if s_int > 10:
            pass
    assert provider.completion == "completed normally"
    assert type(provider.export_value(s_int)) is int
    assert type(provider.export_value([s_int])[0]) is int


def test_post_run_decisions_do_not_grow_the_search_tree():
    provider = CrossHairPrimitiveProvider()
    # There should only be one real branch; so it will take 2 iterations to exhaust
    for _ in range(2):
        with provider.per_test_case_context_manager():
            s_int = provider.draw_integer()
            if s_int > 10:
                pass
        with provider.post_test_case_context_manager():
            if s_int + 1 > 100:
                pass
        assert not provider.exhausted
        assert provider.completion == "completed normally"
    provider.bubble_status()
    assert provider.exhausted


def test_export_mid_run_does_not_grow_the_search_tree():
    provider = CrossHairPrimitiveProvider()
    # There should only be one real branch; so it will take 2 iterations to exhaust
    for _ in range(2):
        with provider.per_test_case_context_manager():
            s_int = provider.draw_integer()
            if s_int > 10:
                pass
            provider.export_value(s_int)
        assert not provider.exhausted
        assert provider.completion == "completed normally"
    provider.bubble_status()
    assert provider.exhausted


def test_value_export_with_no_decisions():
    provider = CrossHairPrimitiveProvider()
    with provider.per_test_case_context_manager():
        s_int = provider.draw_integer()
    assert type(provider.export_value(s_int)) is int


def test_provider_conformance_crosshair():
    # Hypothesis can in theory pass values of any type to `realize`,
    # but the default strategy in the conformance test here acts too much like a
    # fuzzer for crosshair internals here and finds very strange errors.
    _realize_objects = (
        st.integers() | st.floats() | st.booleans() | st.binary() | st.text()
    )
    run_conformance_test(
        CrossHairPrimitiveProvider,
        context_manager_exceptions=(IgnoreAttempt, UnexploredPath, NotDeterministic),
        settings=settings(max_examples=5, stateful_step_count=10),
        _realize_objects=_realize_objects,
    )


@patch("crosshair.statespace.solver_is_sat", side_effect=UnknownSatisfiability)
def test_unsat_during_realization(solver_is_sat_mock):
    provider = CrossHairPrimitiveProvider()
    with provider.per_test_case_context_manager():
        s_int = provider.draw_integer()
    with provider.post_test_case_context_manager():
        with pytest.raises(BackendCannotProceed):
            provider.export_value(s_int)
    assert solver_is_sat_mock.call_count == 1


@patch("crosshair.statespace.solver_is_sat", side_effect=UnknownSatisfiability)
def test_unsat_during_user_exception_realization(solver_is_sat_mock):
    provider = CrossHairPrimitiveProvider()
    with pytest.raises(BackendCannotProceed):
        with provider.per_test_case_context_manager():
            s_int = provider.draw_integer()
            raise TargetException
    assert solver_is_sat_mock.call_count == 1
