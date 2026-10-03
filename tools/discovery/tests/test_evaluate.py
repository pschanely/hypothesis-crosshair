"""Checks the scorer, with attention to the errors it must not treat alike.

A decider that is right 90% of the time but calls someone else's correct code
a bug is worse than one that is right 70% of the time and defers the rest, so
the scorecard has to separate those rather than averaging them.
"""

import json

from discovery.cluster import Signature
from discovery.evaluate import Case, load_cases, score
from discovery.triage import TriageCategory, TriageItem


def case(name, expected, frame="src/pkg/a.py:1"):
    return Case(
        name=name,
        expected=TriageCategory(expected),
        item=TriageItem(
            key=name,
            signature=Signature("AssertionError", frame, "assert N == N"),
            nodeids=[f"t.py::{name}"],
            examples=["v=1"],
            project="/proj",
            sample="E assert -1 >= 0",
        ),
    )


def always(category, confidence=0.9):
    return lambda item: {
        "category": category,
        "confidence": confidence,
        "reasoning": "because",
    }


def test_a_perfect_decider_scores_clean():
    truth = {"a": "project_bug", "b": "overstrong_property"}
    cases = [case(name, kind) for name, kind in truth.items()]
    card = score(cases, lambda item: always(truth[item.key])(item))
    assert card.correct == 2
    assert card.reaching_a_stranger == []
    assert card.deferred == []


def test_calling_our_own_defect_a_project_bug_reaches_a_stranger():
    card = score([case("a", "crosshair_artifact")], always("project_bug"))
    assert len(card.reaching_a_stranger) == 1
    assert card.correct == 0


def test_calling_an_overstrong_property_a_project_bug_reaches_a_stranger():
    card = score([case("a", "overstrong_property")], always("project_bug"))
    assert len(card.reaching_a_stranger) == 1


def test_a_correct_project_bug_does_not_count_as_dangerous():
    card = score([case("a", "project_bug")], always("project_bug"))
    assert card.reaching_a_stranger == []
    assert card.correct == 1


def test_routing_a_real_bug_elsewhere_loses_it():
    card = score([case("a", "project_bug")], always("overstrong_property"))
    assert len(card.lost_findings) == 1
    assert card.reaching_a_stranger == []


def test_deferring_is_counted_but_is_not_dangerous_or_lost():
    card = score([case("a", "project_bug")], always("unclear"))
    assert len(card.deferred) == 1
    assert card.lost_findings == []
    assert card.reaching_a_stranger == []


def test_confusing_the_two_internal_streams_is_its_own_category():
    card = score([case("a", "crosshair_artifact")], always("overstrong_property"))
    assert len(card.wrong_stream) == 1
    assert card.reaching_a_stranger == []
    assert card.lost_findings == []


def test_an_off_schema_answer_is_unusable_not_wrong():
    card = score([case("a", "project_bug")], lambda item: {"category": "nope"})
    assert len(card.unusable) == 1
    assert card.answered == 0
    assert card.correct == 0
    assert card.reaching_a_stranger == [], "no answer cannot reach anyone"


def test_a_decider_that_raises_is_unusable():
    def boom(item):
        raise RuntimeError("no model")

    card = score([case("a", "project_bug")], boom)
    assert len(card.unusable) == 1
    assert "RuntimeError" in card.unusable[0].error


def test_the_confusion_table_counts_every_case():
    cases = [case("a", "project_bug"), case("b", "project_bug")]
    card = score(cases, always("unclear"))
    assert card.confusion() == {"project_bug": {"unclear": 2}}


def test_the_summary_leads_with_the_dangerous_count():
    card = score([case("a", "crosshair_artifact")], always("project_bug"))
    text = "\n".join(card.describe())
    assert "reaching a stranger: 1" in text
    assert text.index("reaching a stranger") < text.index("lost findings")


def test_cases_load_from_jsonl(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "# a comment\n"
        "\n"
        + json.dumps(
            {
                "name": "x",
                "expected": "project_bug",
                "rationale": "why",
                "frame": "src/pkg/a.py:9",
                "exception_type": "ValueError",
                "message": "boom",
                "nodeids": ["t.py::test_a"],
                "examples": ["v=1"],
                "sample": "trace",
                "project": "/proj",
            }
        )
        + "\n"
    )
    loaded = load_cases(str(path))
    assert len(loaded) == 1
    assert loaded[0].expected is TriageCategory.PROJECT_BUG
    assert loaded[0].item.signature.frame == "src/pkg/a.py:9"
    assert loaded[0].item.sample == "trace"


def test_the_shipped_cases_load_and_are_labelled():
    """The case file is a regression asset; a broken one fails silently."""
    import os

    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    loaded = load_cases(os.path.join(here, "cases", "triage.jsonl"))
    assert len(loaded) >= 4
    for entry in loaded:
        assert entry.rationale, f"{entry.name} has no recorded reason"
        assert entry.item.sample, f"{entry.name} has no traceback to read"
        assert entry.item.nodeids


def test_an_unusable_answer_still_appears_in_the_confusion_table():
    """A decider that answers nothing must not look like one that answered."""
    card = score([case("a", "project_bug")], lambda item: {"category": "nope"})
    assert card.confusion() == {"project_bug": {"unusable": 1}}
