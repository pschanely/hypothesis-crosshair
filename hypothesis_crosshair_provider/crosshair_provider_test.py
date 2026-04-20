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
            assert type(provider.realize(s_bool)) == bool
            assert type(provider.realize(s_int)) == int
            assert type(provider.realize(s_float)) == float
            assert type(provider.realize(s_str)) == str
            # NOTE: draw_bytes can raise IgnoreAttempt, which will leave the bytes
            # symbolic without a concrete value:
            assert type(provider.realize(s_bytes)) in (bytes, type(None))
        except BackendCannotProceed:
            pass
        except TargetException:
            found_ct += 1
    assert found_ct > 0, "CrossHair could not find the exception"


def test_string_draw_with_no_intervals():
    """There is only one valid string with no intervals: the empty string. We produce a concrete value in this case."""
    provider = CrossHairPrimitiveProvider()
    with provider.per_test_case_context_manager():
        x = provider.draw_string(IntervalSet.from_string(""))
    assert type(x) is str
    assert x == ""


def test_post_run_value_export():
    provider = CrossHairPrimitiveProvider()
    with provider.per_test_case_context_manager():
        s_int = provider.draw_integer()
        if s_int > 10:
            pass
    assert provider.completion == "completed normally"
    assert type(provider.realize(s_int)) is int
    assert type(provider.realize([s_int])[0]) is int


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
            provider.realize(s_int)
        assert not provider.exhausted
        assert provider.completion == "completed normally"
    provider.bubble_status()
    assert provider.exhausted


def test_value_export_with_no_decisions():
    provider = CrossHairPrimitiveProvider()
    with provider.per_test_case_context_manager():
        s_int = provider.draw_integer()
    assert type(provider.realize(s_int)) is int


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


def test_replay_choices_steers_user_code_to_corpus_values():
    """Warm-start draws are symbolic; the solver is biased so user-code
    branches on the drawn value pick the edge the corpus would reach."""
    provider = CrossHairPrimitiveProvider()
    provider.replay_choices((7,))
    taken = None
    with provider.per_test_case_context_manager():
        n = provider.draw_integer()
        if n == 7:
            taken = "eq_7"
        elif n > 10:
            taken = "gt_10"
        else:
            taken = "other"
    assert taken == "eq_7"
    assert provider._replay_queue == []


def test_replay_choices_records_branches_for_later_iterations():
    """The payoff of warm-starting: a seed that only covers one branch
    should leave the sibling available for later (unseeded) iterations
    to explore via the search tree's normal exhaust-then-sibling logic."""
    provider = CrossHairPrimitiveProvider()
    provider.replay_choices((5,))

    branches_reached = set()
    for _ in range(5):
        try:
            with provider.per_test_case_context_manager():
                n = provider.draw_integer()
                branches_reached.add("gt" if n > 10 else "le")
        except BackendCannotProceed:
            pass
        if provider.exhausted:
            break

    assert "le" in branches_reached
    assert "gt" in branches_reached


def test_replay_choices_surfaces_exceptions():
    """A user exception under warm-start is converted to
    BackendCannotProceed (queueing a concrete doublecheck replay on the
    next iteration), exactly like any other failing symbolic iteration."""
    provider = CrossHairPrimitiveProvider()
    provider.replay_choices((424242,))
    with pytest.raises(BackendCannotProceed):
        with provider.per_test_case_context_manager():
            assert provider.draw_integer() != 424242
    with pytest.raises(AssertionError):
        with provider.per_test_case_context_manager():
            assert provider.draw_integer() != 424242


def test_replay_choices_multiple_seeds_then_symbolic():
    """Each queued corpus input is consumed in FIFO order; draws after the
    queue drains fall through to normal symbolic exploration."""
    provider = CrossHairPrimitiveProvider()
    provider.replay_choices((11,))
    provider.replay_choices((22,))

    taken = []
    # Run three iterations against the same test function; the first two
    # are warm-started (seeds 11 and 22), the third is plain symbolic.
    for _ in range(3):
        try:
            with provider.per_test_case_context_manager():
                n = provider.draw_integer()
                if n == 11:
                    taken.append(11)
                elif n == 22:
                    taken.append(22)
                else:
                    taken.append("other")
        except BackendCannotProceed:
            taken.append("bcp")
    # The two warm-start iterations must steer to their seeds; the third
    # iteration explores a different value.
    assert taken[0] == 11
    assert taken[1] == 22
    assert provider._replay_queue == []


def test_replay_choices_type_mismatch_leaves_symbolic_free():
    """A type-mismatched corpus value does not crash the iteration; the
    draw falls through to normal symbolic behaviour."""
    provider = CrossHairPrimitiveProvider()
    provider.replay_choices(("not-an-int",))
    with provider.per_test_case_context_manager():
        s_int = provider.draw_integer()
    assert isinstance(provider.realize(s_int), int)


@patch("crosshair.statespace.solver_is_sat", side_effect=UnknownSatisfiability)
def test_unsat_during_realization(solver_is_sat_mock):
    provider = CrossHairPrimitiveProvider()
    with provider.per_test_case_context_manager():
        s_int = provider.draw_integer()
    with provider.post_test_case_context_manager():
        with pytest.raises(BackendCannotProceed):
            provider.realize(s_int)
    assert solver_is_sat_mock.call_count == 1


@patch("crosshair.statespace.solver_is_sat", side_effect=UnknownSatisfiability)
def test_unsat_during_user_exception_realization(solver_is_sat_mock):
    provider = CrossHairPrimitiveProvider()
    with pytest.raises(BackendCannotProceed):
        with provider.per_test_case_context_manager():
            s_int = provider.draw_integer()
            raise TargetException
    assert solver_is_sat_mock.call_count == 1
