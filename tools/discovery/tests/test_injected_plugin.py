"""Settings forcing in the injected plugin.

Forcing the backend is the plugin's whole job: if an override is accepted
silently but does not take effect, the test runs on the default backend and is
still reported as a CrossHair result.
"""

import os
import sys

import pytest
from hypothesis import settings as hyp_settings

PLUGIN_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "discovery"
)
sys.path.insert(0, PLUGIN_DIR)
import _injected_plugin as plugin  # noqa: E402

sys.path.remove(PLUGIN_DIR)


class FakeItem:
    def __init__(self, cls=None):
        self.cls = cls


def plain_test():
    pass


def test_a_plain_function_carries_its_own_settings():
    carrier, attr, parent = plugin._settings_carrier(
        FakeItem(), plain_test, hyp_settings
    )
    assert carrier is plain_test
    assert attr == "_hypothesis_internal_use_settings"
    assert parent is None


def test_an_existing_decorator_is_returned_as_the_parent():
    def decorated():
        pass

    existing = hyp_settings(max_examples=7)
    decorated._hypothesis_internal_use_settings = existing
    _, _, parent = plugin._settings_carrier(FakeItem(), decorated, hyp_settings)
    assert parent is existing


def test_a_stateful_testcase_carries_settings_on_its_class():
    """Assigning to runTest would be silently ineffective."""

    class Machine:
        settings = hyp_settings(max_examples=3)

        def runTest(self):
            pass

    carrier, attr, parent = plugin._settings_carrier(
        FakeItem(Machine), Machine.runTest, hyp_settings
    )
    assert carrier is Machine
    assert attr == "settings"
    assert parent is Machine.settings


def test_an_unrelated_class_attribute_named_settings_is_ignored():
    class NotAStateMachine:
        settings = {"not": "hypothesis settings"}

        def test_thing(self):
            pass

    carrier, attr, _ = plugin._settings_carrier(
        FakeItem(NotAStateMachine), NotAStateMachine.test_thing, hyp_settings
    )
    assert carrier is NotAStateMachine.test_thing
    assert attr == "_hypothesis_internal_use_settings"
