import types

from hypothesis.internal.intervalsets import IntervalSet

from hypothesis_crosshair_provider.crosshair_provider import \
    CrossHairPrimitiveProvider


class TargetException(Exception):
    pass


def _example_user_code(s_bool, s_int, s_float, s_str, s_bytes):
    if s_bool:
        if s_int == 120:
            if s_float < 2.0:
                if s_str == "foo":
                    if s_bytes == b"b":
                        raise TargetException


def test_end_to_end():
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
                s_bytes = provider.draw_bytes(1)
                assert type(s_bool) == bool
                assert type(s_int) == int
                assert type(s_float) == float
                assert type(s_str) == str
                assert type(s_bytes) == bytes
                _example_user_code(s_bool, s_int, s_float, s_str, s_bytes)
            assert type(provider.export_value(s_bool)) == bool
            assert type(provider.export_value(s_int)) == int
            assert type(provider.export_value(s_float)) == float
            assert type(provider.export_value(s_str)) == str
            # NOTE: draw_bytes can raise IgnoreAttempt, which will leave the bytes
            # symbolic without a concrete value:
            assert type(provider.export_value(s_bytes)) in (bytes, types.NoneType)
        except TargetException:
            found_ct += 1
    assert found_ct > 0, "CrossHair could not find the exception"
