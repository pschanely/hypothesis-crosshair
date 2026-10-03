"""Checks harness repair, and the repairs it must be unable to make.

The failure texts here were produced by the corpus rather than written for
the test: a suite whose own configuration names a plugin that is not
installed refuses to start, and says which flags it did not recognize.
"""

import dataclasses
import sys

from discovery.harness import (
    MAX_REPAIRS,
    MODULE_DISTRIBUTIONS,
    PLUGIN_FLAGS,
    Repair,
    diagnose,
    plan,
)

BENCHMARK = """\
ERROR: usage: python -m pytest [options] [file_or_dir] [file_or_dir] [...]
python -m pytest: error: unrecognized arguments: --benchmark-sort=fullname \
--benchmark-warmup=true --benchmark-warmup-iterations=5 \
--benchmark-group-by=fullname
"""

XDIST = """\
ERROR: usage: python -m pytest [options] [file_or_dir] [file_or_dir] [...]
python -m pytest: error: unrecognized arguments: -n0
  inifile: /home/user/corpus/hyperlink/pytest.ini
  rootdir: /home/user/corpus/hyperlink
"""


def test_a_benchmark_suite_asks_for_the_benchmark_plugin():
    repair = diagnose(BENCHMARK)
    assert repair.packages == ["pytest-benchmark"]
    assert repair.pytest_args == []


def test_an_xdist_suite_installs_the_plugin_and_then_stops_it_working():
    repair = diagnose(XDIST)
    assert repair.packages == ["pytest-xdist"]
    assert repair.pytest_args == ["-n0"], "xdist hides tests from the injected plugin"


def test_an_unfamiliar_failure_yields_no_repair():
    assert diagnose("E   AssertionError: assert 1 == 2") is None
    assert diagnose("") is None


def test_a_missing_import_is_matched_to_its_distribution():
    repair = diagnose("ModuleNotFoundError: No module named 'pytest_benchmark'")
    assert repair.packages == ["pytest-benchmark"]


def test_an_unmapped_missing_import_is_not_guessed_at():
    assert diagnose("ModuleNotFoundError: No module named 'some_private_thing'") is None


def test_several_plugins_in_one_failure_are_installed_together():
    text = (
        "python -m pytest: error: unrecognized arguments: --cov=pkg "
        "--benchmark-sort=name -n2"
    )
    repair = diagnose(text)
    assert repair.packages == ["pytest-cov", "pytest-benchmark", "pytest-xdist"]
    assert repair.pytest_args == ["-n0"]


def test_a_flag_matches_at_most_one_distribution():
    """The lookup takes the first matching prefix, so none may extend another."""
    for prefix, dist in PLUGIN_FLAGS.items():
        for other, other_dist in PLUGIN_FLAGS.items():
            if prefix is not other and other.startswith(prefix):
                assert dist == other_dist, f"{other} is shadowed by {prefix}"


def test_a_repair_cannot_express_a_change_to_the_suite():
    """The dangerous repair is unrepresentable, not merely discouraged.

    A harness repair that edited a test could turn a failing assertion into a
    passing one, or a passing one into a finding. Nothing here can name a
    file, so no such repair can be built.
    """
    fields = {f.name for f in dataclasses.fields(Repair)}
    assert fields == {"name", "rationale", "packages", "env", "pytest_args"}


def test_a_repair_describes_what_it_changes():
    repair = Repair(
        name="r",
        rationale="why",
        packages=["pytest-cov"],
        env={"K": "v"},
        pytest_args=["-p", "no:cacheprovider"],
    )
    text = repair.describe()
    assert "install pytest-cov" in text and "K=v" in text and "no:cacheprovider" in text


def test_a_repair_already_tried_is_not_offered_again():
    first = plan(BENCHMARK)
    assert first is not None
    assert plan(BENCHMARK, applied=[first.name]) is None, "a repeat would loop"


def test_repairs_stop_after_the_budget():
    assert plan(BENCHMARK, applied=["a", "b", "c"]) is None
    assert MAX_REPAIRS == 3


def test_every_mapped_module_names_a_plausible_distribution():
    for module, dist in MODULE_DISTRIBUTIONS.items():
        assert dist and " " not in dist
        assert module != dist or module == "hypothesis"


def test_a_flags_value_is_not_reported_as_a_flag():
    """A project may write ``addopts`` with spaces, so argparse echoes values."""
    repair = diagnose(
        "python -m pytest: error: unrecognized arguments: "
        "--benchmark-sort fullname --benchmark-warmup true"
    )
    assert repair.packages == ["pytest-benchmark"]
    assert "fullname" not in repair.rationale
    assert "--benchmark-sort" in repair.rationale


def _broken_project(tmp_path, addopts):
    (tmp_path / "pytest.ini").write_text(f"[pytest]\naddopts = {addopts}\n")
    (tmp_path / "test_a.py").write_text(
        "from hypothesis import given, strategies as st\n\n"
        "@given(st.integers())\n"
        "def test_a(n):\n"
        "    assert n == n\n"
    )
    return [
        "--project",
        str(tmp_path),
        "--crosshair-python",
        sys.executable,
        "--sandbox",
        "local",
        "--run-root",
        str(tmp_path / "runs"),
    ]


def test_a_repairable_collection_failure_reports_the_repair(tmp_path, capsys):
    from discovery.cli import main

    assert main(_broken_project(tmp_path, "--benchmark-sort=fullname")) == 3
    err = capsys.readouterr().err
    assert "install pytest-benchmark" in err
    assert "collection failed" in err


def test_an_unrepairable_collection_failure_asks_for_a_person(tmp_path, capsys):
    from discovery.cli import main

    assert main(_broken_project(tmp_path, "--no-such-plugin-flag")) == 2
    assert "needs a person" in capsys.readouterr().err
