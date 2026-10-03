"""Checks the candidate survey, with attention to what it may reject.

Rejecting a project is the one irreversible act at this stage: nothing
downstream revisits it, and the findings it would have produced are never
counted. So most of these tests are about what must *not* be a rejection.
"""

import os

from discovery.candidates import (
    EXTERNAL_RESOURCE_IMPORTS,
    OPAQUE_IMPORTS,
    Assessment,
    Signal,
    Survey,
    assess,
    order,
    python_files,
    rank,
    survey,
)


def project(tmp_path, files):
    for relative, source in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    return str(tmp_path)


GIVEN = """\
from hypothesis import given, strategies as st

@given(st.integers())
def test_one(n):
    assert n == n
"""


def test_a_plain_given_test_is_found(tmp_path):
    found = survey(project(tmp_path, {"tests/test_a.py": GIVEN}))
    assert [t.nodeid for t in found.tests] == [
        os.path.join("tests", "test_a.py") + "::test_one"
    ]
    assert found.tests[0].strategies == ["integers"]


def test_the_module_qualified_form_is_found(tmp_path):
    source = """\
import hypothesis
import hypothesis.strategies

@hypothesis.given(hypothesis.strategies.text())
def test_two(s):
    pass
"""
    found = survey(project(tmp_path, {"tests/test_b.py": source}))
    assert len(found.tests) == 1
    assert found.tests[0].strategies == ["text"]


def test_an_unrelated_decorator_named_given_is_not_a_property_test(tmp_path):
    source = """\
from behave import given

@given("something")
def step(context):
    pass
"""
    found = survey(project(tmp_path, {"tests/test_c.py": source}))
    assert found.tests == []


def test_positional_strategies_bind_to_the_trailing_parameters(tmp_path):
    source = """\
from hypothesis import given, strategies as st

@given(st.integers())
def test_d(tmp_path, n):
    pass
"""
    found = survey(project(tmp_path, {"tests/test_d.py": source}))
    assert found.tests[0].fixtures == ["tmp_path"]


def test_keyword_strategies_leave_the_rest_as_fixtures(tmp_path):
    source = """\
from hypothesis import given, strategies as st

@given(n=st.integers())
def test_e(n, capsys):
    pass
"""
    found = survey(project(tmp_path, {"tests/test_e.py": source}))
    assert found.tests[0].fixtures == ["capsys"]


def test_self_is_not_a_fixture(tmp_path):
    source = """\
from hypothesis import given, strategies as st

class TestThing:
    @given(st.integers())
    def test_f(self, n):
        pass
"""
    found = survey(project(tmp_path, {"tests/test_f.py": source}))
    assert found.tests[0].fixtures == []


def test_a_state_machine_is_found(tmp_path):
    source = """\
from hypothesis.stateful import RuleBasedStateMachine, rule

class Machine(RuleBasedStateMachine):
    @rule()
    def step(self):
        pass
"""
    found = survey(project(tmp_path, {"tests/test_g.py": source}))
    assert found.state_machines == ["Machine"]


def test_a_composite_is_a_strategy_not_a_test(tmp_path):
    source = """\
from hypothesis import strategies as st

@st.composite
def versions(draw):
    return draw(st.integers())
"""
    found = survey(project(tmp_path, {"tests/test_h.py": source}))
    assert found.composites == ["versions"]
    assert found.tests == []


def test_virtualenvs_and_build_output_are_not_walked(tmp_path):
    files = {
        "tests/test_a.py": GIVEN,
        ".venv/lib/site-packages/pkg/test_vendored.py": GIVEN,
        "build/lib/test_copy.py": GIVEN,
        "__pycache__/test_stale.py": GIVEN,
    }
    found = survey(project(tmp_path, files))
    assert len(found.tests) == 1
    assert python_files(str(tmp_path)) == [os.path.join("tests", "test_a.py")]


def test_a_file_this_interpreter_cannot_parse_still_reports_its_markers(tmp_path):
    source = """\
from hypothesis import given, strategies as st

def f(:::):
    pass

@given(st.integers())
def test_i(n):
    pass
"""
    found = survey(project(tmp_path, {"tests/test_i.py": source}))
    assert found.tests == []
    assert found.hidden_tests == 1
    assert assess(found).runnable, "an unreadable file must not read as an empty one"


def test_an_unparsable_file_with_no_markers_hides_nothing(tmp_path):
    found = survey(project(tmp_path, {"pkg/broken.py": "def f(:::): pass\n"}))
    assert found.unparsed
    assert found.hidden_tests == 0
    assert not assess(found).runnable


def test_only_an_absence_of_tests_blocks_a_project(tmp_path):
    found = survey(project(tmp_path, {"pkg/lib.py": "x = 1\n"}))
    result = assess(found)
    assert not result.runnable
    assert "no @given tests" in result.blocker


def test_the_worst_possible_candidate_is_still_runnable(tmp_path):
    source = """\
import numpy
import requests
from hypothesis import given, strategies as st

@given(st.integers())
def test_j(n, tmp_path, capsys):
    pass
"""
    result = assess(survey(project(tmp_path, {"tests/test_j.py": source})))
    assert result.runnable, "a low score must never become a rejection"
    assert result.score < 50
    assert result.blocker == ""


def test_an_array_library_lowers_the_score_without_excluding(tmp_path):
    clean = assess(survey(project(tmp_path / "a", {"tests/test_a.py": GIVEN})))
    heavy = assess(
        survey(project(tmp_path / "b", {"tests/test_a.py": "import numpy\n" + GIVEN}))
    )
    assert heavy.score < clean.score
    assert heavy.runnable


def test_each_import_set_is_scored_separately(tmp_path):
    opaque = assess(
        survey(project(tmp_path / "o", {"tests/test_a.py": "import pandas\n" + GIVEN}))
    )
    external = assess(
        survey(
            project(tmp_path / "e", {"tests/test_a.py": "import requests\n" + GIVEN})
        )
    )
    assert "pandas" in OPAQUE_IMPORTS and "requests" in EXTERNAL_RESOURCE_IMPORTS
    assert opaque.score < external.score, "an opaque value costs more than a socket"


def test_an_uncontrolled_source_is_noted_rather_than_scored(tmp_path):
    source = """\
from hypothesis import given, strategies as st

@given(st.randoms())
def test_k(rng):
    pass
"""
    noted = assess(survey(project(tmp_path / "n", {"tests/test_k.py": source})))
    plain = assess(survey(project(tmp_path / "p", {"tests/test_k.py": GIVEN})))
    assert any("randoms" in note for note in noted.notes)
    assert noted.score == plain.score, "a note must not move the candidate"


def test_more_tests_outrank_fewer(tmp_path):
    one = project(tmp_path / "one", {"tests/test_a.py": GIVEN})
    many = project(
        tmp_path / "many",
        {
            f"tests/test_{i}.py": GIVEN.replace("test_one", f"test_{i}")
            for i in range(20)
        },
    )
    ordered = rank([one, many])
    assert ordered[0].survey.project == many
    assert ordered[0].score > ordered[1].score


def test_blocked_projects_sort_last_whatever_their_size(tmp_path):
    empty = project(tmp_path / "empty", {f"pkg/m{i}.py": "x = 1\n" for i in range(50)})
    tiny = project(tmp_path / "tiny", {"tests/test_a.py": GIVEN})
    ordered = rank([empty, tiny])
    assert [a.runnable for a in ordered] == [True, False]


def test_a_domain_strategy_raises_the_score(tmp_path):
    bare = project(tmp_path / "bare", {"tests/test_a.py": GIVEN})
    rich = project(
        tmp_path / "rich",
        {
            "tests/test_a.py": GIVEN
            + "\n".join(
                f"@st.composite\ndef built_{i}(draw):\n    return 1\n" for i in range(5)
            )
        },
    )
    assert assess(survey(rich)).score > assess(survey(bare)).score


def test_describe_names_the_blocker_and_hides_the_signals(tmp_path):
    text = "\n".join(assess(survey(project(tmp_path, {"a.py": "x = 1\n"}))).describe())
    assert "not runnable" in text
    assert "breadth" not in text


def test_a_fixture_lowers_the_score_on_its_own(tmp_path):
    fixtured = GIVEN.replace("def test_one(n):", "def test_one(tmp_path, n):")
    plain = assess(survey(project(tmp_path / "p", {"tests/test_a.py": GIVEN})))
    taking = assess(survey(project(tmp_path / "f", {"tests/test_a.py": fixtured})))
    assert taking.score < plain.score
    assert taking.runnable


def test_a_blocked_project_sorts_after_a_runnable_one_it_outscores():
    """``order`` must not depend on a blocker also costing points."""
    blocked = Assessment(survey=Survey(project="blocked"), blocker="nothing to run")
    blocked.signals = [Signal("breadth", 1.0, "pretend")]
    runnable = Assessment(survey=Survey(project="runnable"))
    runnable.signals = [Signal("breadth", 0.1, "real")]
    assert blocked.score > runnable.score
    assert [a.survey.project for a in order([blocked, runnable])] == [
        "runnable",
        "blocked",
    ]


def test_a_parent_directory_expands_to_the_projects_under_it(tmp_path, capsys):
    from discovery.candidates_cli import main

    project(tmp_path / "corpus" / "good", {"tests/test_a.py": GIVEN})
    project(tmp_path / "corpus" / "bare", {"pkg/lib.py": "x = 1\n"})
    assert main([str(tmp_path / "corpus"), "--all"]) == 0
    out = capsys.readouterr().out
    assert "good" in out and "bare" in out
    assert "1 candidate(s) worth provisioning, 1 with nothing to run" in out


def test_a_checkout_is_surveyed_rather_than_its_subdirectories(tmp_path, capsys):
    from discovery.candidates_cli import main

    root = project(tmp_path / "one", {"tests/test_a.py": GIVEN})
    (tmp_path / "one" / "pyproject.toml").write_text("[project]\nname = 'one'\n")
    assert main([root]) == 0
    assert "one: score" in capsys.readouterr().out


def test_the_summary_counts_every_candidate_not_just_the_shown_ones(tmp_path, capsys):
    from discovery.candidates_cli import main

    for name in ("a", "b", "c"):
        project(tmp_path / "corpus" / name, {"tests/test_a.py": GIVEN})
    main([str(tmp_path / "corpus"), "--top", "1"])
    assert "3 candidate(s) worth provisioning, showing 1" in capsys.readouterr().out


def test_json_output_carries_the_blocker_and_the_signals(tmp_path, capsys):
    import json

    from discovery.candidates_cli import main

    project(tmp_path / "corpus" / "good", {"tests/test_a.py": GIVEN})
    project(tmp_path / "corpus" / "bare", {"pkg/lib.py": "x = 1\n"})
    main([str(tmp_path / "corpus"), "--all", "--json"])
    rows = {
        r["project"].split(os.sep)[-1]: r for r in json.loads(capsys.readouterr().out)
    }
    assert rows["good"]["runnable"] and rows["good"]["signals"]["breadth"] > 0
    assert not rows["bare"]["runnable"] and rows["bare"]["blocker"]


def test_a_compiled_extension_is_the_strongest_negative(tmp_path):
    """CrossHair realizes at the boundary however pure the Python around it."""
    plain = assess(survey(project(tmp_path / "p", {"tests/test_a.py": GIVEN})))
    rust = project(tmp_path / "r", {"tests/test_a.py": GIVEN})
    (tmp_path / "r" / "Cargo.toml").write_text("[package]\nname = 'x'\n")
    compiled = assess(survey(rust))
    assert compiled.survey.native_markers == ["Cargo.toml"]
    assert compiled.score < plain.score
    assert compiled.runnable, "a compiled project is still worth a late slot"


def test_a_cython_source_anywhere_counts_as_compiled(tmp_path):
    root = project(tmp_path, {"tests/test_a.py": GIVEN, "src/pkg/fast.pyx": "x = 1\n"})
    assert assess(survey(root)).survey.native_markers == [
        os.path.join("src", "pkg", "fast.pyx")
    ]


def test_a_setup_py_without_an_extension_is_not_compiled(tmp_path):
    root = project(
        tmp_path,
        {
            "tests/test_a.py": GIVEN,
            "setup.py": "from setuptools import setup\nsetup()\n",
        },
    )
    assert assess(survey(root)).survey.native_markers == []


def test_a_setup_py_building_an_extension_is_compiled(tmp_path):
    root = project(
        tmp_path,
        {
            "tests/test_a.py": GIVEN,
            "setup.py": "from setuptools import setup, Extension\n"
            "setup(ext_modules=[Extension('x', ['x.c'])])\n",
        },
    )
    assert assess(survey(root)).survey.native_markers == ["setup.py"]


def test_the_weights_add_up_to_the_score_they_are_reported_against():
    from discovery.candidates import SCORE_WEIGHTS

    assert sum(SCORE_WEIGHTS.values()) == 100
