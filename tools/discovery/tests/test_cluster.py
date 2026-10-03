"""Checks that clustering merges one defect and keeps two apart."""

from discovery.cluster import (
    Signature,
    cluster,
    failing_frame,
    normalize_message,
    scrub,
)
from discovery.model import CaseOutcome, Classification, Outcome, Verdict

PROJECT = "/proj"

LONGREPR = """\
self = <tests.test_version.TestVersion object at 0x7f3a48462250>

    @given(versions())
    def test_release_nonneg(self, v):
>       assert v.release[0] >= 0
E       assert -73 >= 0

tests/test_version.py:41: in test_release_nonneg
src/packaging/version.py:418: in __init__
src/packaging/version.py:512: AssertionError
"""


def failure(nodeid, exc="AssertionError", example="v=Version('-73.12')"):
    return Classification(
        nodeid=nodeid,
        verdict=Verdict.TROPHY_CANDIDATE,
        baseline=Outcome.PASSED,
        crosshair=Outcome.FAILED,
        exception_type=exc,
        falsifying_example=example,
    )


def detail(nodeid, longrepr=LONGREPR, message="assert -73 >= 0"):
    return CaseOutcome(
        nodeid=nodeid,
        outcome=Outcome.FAILED,
        exception_type="AssertionError",
        message=message,
        longrepr=longrepr,
    )


def test_the_innermost_project_frame_is_the_one_used():
    """The outermost frame is the test, which the node id already names."""
    assert failing_frame(LONGREPR, PROJECT) == "src/packaging/version.py:512"


def test_library_frames_are_not_mistaken_for_project_code():
    """The deepest frame of a failure is usually inside a library, and a venv
    inside the project directory is still a library."""
    text = (
        "tests/test_parse.py:10: in test_round_trip\n"
        "src/mypkg/parse.py:88: in loads\n"
        "/proj/.venv/lib/python3.12/site-packages/attr/_make.py:620: in __init__\n"
        "/usr/lib/python3.12/json/decoder.py:355: ValueError\n"
    )
    assert failing_frame(text, PROJECT) == "src/mypkg/parse.py:88"


def test_a_traceback_with_no_project_frame_yields_nothing():
    text = "/usr/lib/python3.12/json/decoder.py:355: ValueError\n"
    assert failing_frame(text, PROJECT) == ""


def test_addresses_and_temp_paths_are_scrubbed():
    text = "<Foo object at 0x7f3a48462250> wrote /tmp/pytest-of-root/pytest-4/x"
    cleaned = scrub(text)
    assert "0x7f3a48462250" not in cleaned
    assert "pytest-4" not in cleaned


def test_a_message_keeps_its_shape_but_not_its_numbers():
    """The number is the falsifying example showing through."""
    assert normalize_message("assert -73 >= 0") == normalize_message("assert -5 >= 0")
    assert normalize_message("assert -73 >= 0") == "assert N >= N"


def test_two_messages_that_differ_in_words_stay_apart():
    assert normalize_message("expected a tuple") != normalize_message("expected a list")


def test_one_defect_reached_by_three_tests_is_one_cluster():
    items = [failure(f"tests/t.py::test_{i}", example=f"v={i}") for i in range(3)]
    details = {
        item.nodeid: detail(item.nodeid, message=f"assert -{i} >= 0")
        for i, item in enumerate(items)
    }
    found = cluster(items, details, PROJECT)
    assert len(found) == 1
    assert found[0].size == 3
    assert len(found[0].examples) == 3


def test_failures_in_different_places_stay_apart():
    first = failure("tests/t.py::test_a")
    second = failure("tests/t.py::test_b")
    other = LONGREPR.replace(
        "src/packaging/version.py:512", "src/packaging/specifiers.py:90"
    )
    found = cluster(
        [first, second],
        {
            first.nodeid: detail(first.nodeid),
            second.nodeid: detail(second.nodeid, other),
        },
        PROJECT,
    )
    assert len(found) == 2


def test_different_exception_types_stay_apart():
    first = failure("tests/t.py::test_a", exc="AssertionError")
    second = failure("tests/t.py::test_b", exc="ValueError")
    details = {n: detail(n) for n in (first.nodeid, second.nodeid)}
    assert len(cluster([first, second], details, PROJECT)) == 2


def test_a_run_where_nothing_failed_has_no_clusters():
    passing = Classification(
        nodeid="tests/t.py::test_a",
        verdict=Verdict.NO_SIGNAL,
        baseline=Outcome.PASSED,
        crosshair=Outcome.PASSED,
    )
    assert cluster([passing], {}, PROJECT) == []


def test_clusters_are_ordered_by_size():
    """Size leads, so the exception names here sort against the wanted order."""
    items = [
        failure(f"tests/t.py::test_{i}", exc="ZeroDivisionError") for i in range(3)
    ]
    lonely = failure("tests/t.py::test_other", exc="ArithmeticError")
    details = {i.nodeid: detail(i.nodeid) for i in items + [lonely]}
    found = cluster(items + [lonely], details, PROJECT)
    assert [c.size for c in found] == [3, 1]
    assert found[0].signature.exception_type == "ZeroDivisionError"


def test_a_failure_with_no_traceback_still_clusters():
    """A crash reported without a longrepr must not vanish from triage."""
    item = failure("tests/t.py::test_a")
    found = cluster([item], {}, PROJECT)
    assert len(found) == 1
    assert found[0].signature == Signature("AssertionError", "", "")


def test_the_report_shows_clusters():
    """Clustering that nothing calls is worth nothing, so assert the wiring."""
    from discovery.cli import _format
    from discovery.model import Arm, RunResult, Tier
    from discovery.pipeline import PipelineReport

    report = PipelineReport(project_dir=PROJECT)
    items = [failure(f"tests/t.py::test_{i}") for i in range(3)]
    report.classifications = items
    report.collected = [i.nodeid for i in items]
    run = RunResult(Arm.CROSSHAIR, Tier.A_VERDICT, 0, 1.0)
    for item in items:
        run.outcomes[item.nodeid] = detail(item.nodeid)
    report.crosshair_run = run

    text = _format(report)
    assert "failure clusters  (1)" in text
    assert "[3] AssertionError at src/packaging/version.py:512" in text


def test_a_falsifying_example_block_is_not_read_as_a_frame():
    """pytest prefixes the example with `E `, and it can span lines."""
    text = (
        "E       Falsifying example: test_round_trip(\n"
        "E           data=b'0\\xe8\\xe1>',\n"
        "E       )\n"
        "\n"
        "tests/test_tinylib.py:14: AssertionError\n"
    )
    assert failing_frame(text, PROJECT) == "tests/test_tinylib.py:14"


def test_the_falsifying_example_is_not_part_of_the_message():
    """It is the one part guaranteed to differ between sightings of one bug."""
    first = normalize_message(
        "IndexError: list index out of range\nFailing test case: test_x(\n text='',\n)"
    )
    second = normalize_message(
        "IndexError: list index out of range\nFailing test case: test_x(\n text='qq',\n)"
    )
    assert first == second == "IndexError: list index out of range"


def test_assertion_introspection_is_not_part_of_the_message():
    """pytest spells out the operands, which are per-example values."""
    template = (
        "assert {0} == {1}\n"
        " +  where {0} = checksum(b'{2}')\n"
        " +  and   {1} = reference(b'{2}')\n"
    )
    first = normalize_message(template.format(7, 8, "ab"))
    second = normalize_message(template.format(3, 4, "zz"))
    assert first == second == "assert N == N"
