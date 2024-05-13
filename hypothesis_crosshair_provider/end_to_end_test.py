import re

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st


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
