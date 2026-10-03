from discovery.model import Outcome
from discovery.runner import EnvSpec
from discovery.validate import (
    ValidationResult,
    build_cases,
    nodeid_to_target,
    split_example,
)


def test_split_example_recovers_the_argument_source():
    assert split_example("test_x(\n    a=1,\n    b='q',\n)") == "a=1,\n    b='q',"
    assert split_example("test_x(data=b'\\x00')") == "data=b'\\x00'"


def test_split_example_rejects_unusable_text():
    assert split_example("") is None
    assert split_example("no parens here") is None


def test_nodeid_maps_to_module_and_attribute_path():
    assert nodeid_to_target("test_a.py::test_x") == ("test_a", ["test_x"])
    assert nodeid_to_target("pkg/test_a.py::Cls::test_x") == (
        "pkg.test_a",
        ["Cls", "test_x"],
    )
    assert nodeid_to_target("test_a.py::test_x[1-2]") == ("test_a", ["test_x"])


def test_nodeid_without_a_python_file_is_unusable():
    assert nodeid_to_target("some::thing") is None


def test_cases_skip_entries_with_no_usable_example():
    cases = build_cases({"test_a.py::test_x": "test_x(a=1)", "test_a.py::test_y": ""})
    assert cases == [("test_a.py::test_x", "test_a", ["test_x"], "a=1")]


def test_only_a_definite_answer_counts_as_conclusive():
    assert ValidationResult("n", Outcome.FAILED, "reproduced").conclusive
    assert ValidationResult("n", Outcome.PASSED, "clean").conclusive
    assert not ValidationResult("n", Outcome.NOT_RUN, "inconclusive").conclusive


def test_validation_refuses_an_environment_that_has_the_plugin(tmp_path):
    from discovery.sandbox import LocalSandbox
    from discovery.validate import Validator

    validator = Validator(
        LocalSandbox(i_understand_this_is_unsafe=True), str(tmp_path), str(tmp_path)
    )
    try:
        validator.validate({}, EnvSpec("bad", ["python"], has_crosshair=True))
    except ValueError as exc:
        assert "without the plugin" in str(exc)
    else:
        raise AssertionError("expected a refusal")
