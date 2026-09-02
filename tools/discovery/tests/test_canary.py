"""Fault injection and canary scoring.

The scoring rules matter as much as the injection: a canary that reports green
when the pipeline produced nothing is worse than no canary, because it retires
the very doubt it exists to hold open.
"""

import pytest
from discovery.canary import (
    CanaryResult,
    Expectation,
    Fault,
    FaultNotApplied,
    injected,
    summarise,
)
from discovery.model import Verdict

FAULT = Fault(
    name="demo/fault",
    relative_path="mod.py",
    original="return x",
    replacement="return -x",
    nodeids=["t.py::test_a"],
)


def write(tmp_path, text, name="mod.py"):
    (tmp_path / name).write_text(text)
    return str(tmp_path)


def test_injection_applies_and_reverts(tmp_path):
    before = "def f(x):\n    return x\n"
    project = write(tmp_path, before)
    with injected(project, FAULT) as path:
        assert "return -x" in open(path).read()
    assert open(tmp_path / "mod.py").read() == before


def test_injection_reverts_even_when_the_body_raises(tmp_path):
    before = "def f(x):\n    return x\n"
    project = write(tmp_path, before)
    with pytest.raises(ValueError):
        with injected(project, FAULT):
            raise ValueError("pipeline blew up")
    assert open(tmp_path / "mod.py").read() == before


def test_an_anchor_that_matches_nothing_is_refused(tmp_path):
    """A silent no-op patch would run the pipeline against clean code."""
    project = write(tmp_path, "def f(x):\n    return y\n")
    with pytest.raises(FaultNotApplied, match="appears 0 times"):
        with injected(project, FAULT):
            pass


def test_an_ambiguous_anchor_is_refused(tmp_path):
    project = write(tmp_path, "def f(x):\n    return x\n\ndef g(x):\n    return x\n")
    with pytest.raises(FaultNotApplied, match="appears 2 times"):
        with injected(project, FAULT):
            pass


def test_a_missing_file_is_refused(tmp_path):
    with pytest.raises(FaultNotApplied, match="does not exist"):
        with injected(str(tmp_path), FAULT):
            pass


def result(expectation, **kw):
    return CanaryResult(fault="f", expectation=expectation, **kw)


def test_a_confirmed_detection_passes():
    entry = result(
        Expectation.DETECTED,
        verdicts={"t": Verdict.TROPHY_CANDIDATE},
        detected=True,
    )
    assert entry.conclusive and entry.passed


def test_a_clean_miss_passes_a_negative_control():
    entry = result(Expectation.NOT_DETECTED, verdicts={"t": Verdict.NO_SIGNAL})
    assert entry.conclusive and entry.passed


def test_a_pipeline_that_produced_nothing_never_passes():
    """The bug this rule exists for: it used to pass vacuously."""
    entry = result(Expectation.NOT_DETECTED, verdicts={"t": Verdict.NO_BASELINE_RESULT})
    assert not entry.conclusive
    assert not entry.passed
    assert "inconclusive" in entry.detail


def test_an_unclassified_target_never_passes():
    entry = result(
        Expectation.NOT_DETECTED,
        verdicts={"t": Verdict.NO_SIGNAL},
        missing=["t2"],
    )
    assert not entry.passed


def test_no_verdicts_at_all_never_passes():
    assert not result(Expectation.NOT_DETECTED).passed


def test_an_unconfirmed_find_settles_neither_expectation():
    for expectation in (Expectation.DETECTED, Expectation.NOT_DETECTED):
        entry = result(
            expectation,
            verdicts={"t": Verdict.PENDING_VALIDATION},
            unconfirmed=True,
        )
        assert not entry.passed, expectation


def test_an_injection_error_never_passes():
    entry = result(Expectation.NOT_DETECTED, error="anchor appears 0 times")
    assert not entry.passed
    assert entry.detail == "anchor appears 0 times"


def test_summary_counts_and_warns():
    lines = summarise(
        [
            result(
                Expectation.DETECTED,
                verdicts={"t": Verdict.TROPHY_CANDIDATE},
                detected=True,
            ),
            result(
                Expectation.NOT_DETECTED, verdicts={"t": Verdict.NO_BASELINE_RESULT}
            ),
        ]
    )
    text = "\n".join(lines)
    assert "1/2 faults behaved as expected" in text
    assert "cannot be trusted" in text


def test_a_fault_pinned_to_a_verdict_rejects_a_different_detection():
    """A trophy and a shared find both count as detection."""
    from discovery.canary import CanaryResult, Expectation
    from discovery.model import Verdict

    result = CanaryResult(
        fault="f",
        expectation=Expectation.DETECTED,
        expect_verdicts=frozenset({Verdict.TROPHY_CANDIDATE}),
        verdicts={"t::a": Verdict.SHARED_FIND},
        detected=True,
    )
    assert not result.passed


def test_a_fault_pinned_to_a_verdict_accepts_that_verdict():
    from discovery.canary import CanaryResult, Expectation
    from discovery.model import Verdict

    result = CanaryResult(
        fault="f",
        expectation=Expectation.DETECTED,
        expect_verdicts=frozenset({Verdict.SHARED_FIND}),
        verdicts={"t::a": Verdict.SHARED_FIND},
        detected=True,
    )
    assert result.passed


def test_import_check_rejects_code_loaded_from_elsewhere(tmp_path):
    """Patching a tree the target never imports tests nothing."""
    import sys

    from discovery.canary import (
        Expectation,
        Fault,
        FaultNotApplied,
        verify_target_imports,
    )

    fault = Fault(
        name="x/y",
        relative_path="mod.py",
        original="a",
        replacement="b",
        nodeids=["t::a"],
        import_module="json",
        expectation=Expectation.DETECTED,
    )
    with pytest.raises(FaultNotApplied, match="not from"):
        verify_target_imports([sys.executable], str(tmp_path), fault)


def test_import_check_passes_when_the_module_is_inside_the_project(tmp_path):
    import os
    import sys

    from discovery.canary import Expectation, Fault, verify_target_imports

    (tmp_path / "canarymod.py").write_text("VALUE = 1\n")
    fault = Fault(
        name="x/y",
        relative_path="canarymod.py",
        original="1",
        replacement="2",
        nodeids=["t::a"],
        import_module="canarymod",
        expectation=Expectation.DETECTED,
    )
    previous = os.environ.get("PYTHONPATH")
    os.environ["PYTHONPATH"] = str(tmp_path)
    try:
        verify_target_imports([sys.executable], str(tmp_path), fault)
    finally:
        if previous is None:
            os.environ.pop("PYTHONPATH")
        else:
            os.environ["PYTHONPATH"] = previous


def test_import_check_is_skipped_when_no_module_is_declared():
    from discovery.canary import Expectation, Fault, verify_target_imports

    fault = Fault(
        name="x/y",
        relative_path="mod.py",
        original="a",
        replacement="b",
        nodeids=["t::a"],
        expectation=Expectation.DETECTED,
    )
    verify_target_imports(["/nonexistent/python"], "/nowhere", fault)


def test_a_killed_run_is_undone_on_the_next_injection(tmp_path):
    """finally does not run when the process is killed."""
    from discovery.canary import BACKUP_SUFFIX, Expectation, Fault, injected

    source = tmp_path / "mod.py"
    source.write_text("VALUE = 1\n")
    # what a killed run leaves behind: a patched file and its backup
    source.write_text("VALUE = 2\n")
    (tmp_path / ("mod.py" + BACKUP_SUFFIX)).write_text("VALUE = 1\n")

    fault = Fault(
        name="x/y",
        relative_path="mod.py",
        original="VALUE = 1",
        replacement="VALUE = 3",
        nodeids=["t::a"],
        expectation=Expectation.DETECTED,
    )
    with injected(str(tmp_path), fault):
        assert source.read_text() == "VALUE = 3\n"
    assert source.read_text() == "VALUE = 1\n"
    assert not (tmp_path / ("mod.py" + BACKUP_SUFFIX)).exists()


def test_injection_leaves_no_backup_behind(tmp_path):
    from discovery.canary import BACKUP_SUFFIX, Expectation, Fault, injected

    source = tmp_path / "mod.py"
    source.write_text("VALUE = 1\n")
    fault = Fault(
        name="x/y",
        relative_path="mod.py",
        original="VALUE = 1",
        replacement="VALUE = 2",
        nodeids=["t::a"],
        expectation=Expectation.DETECTED,
    )
    with injected(str(tmp_path), fault):
        assert (tmp_path / ("mod.py" + BACKUP_SUFFIX)).exists(), "no undo record"
    assert not (tmp_path / ("mod.py" + BACKUP_SUFFIX)).exists()
    assert source.read_text() == "VALUE = 1\n"
