"""Checks the promotions routing allows, and the ones it refuses.

The dangerous direction here is promotion on one piece of evidence. Triage
reads code and can be wrong about whether a failure is real; the clean-room
replay cannot read code but is the only thing that shows the example failing
without the plugin. A trophy needs both.
"""

from discovery.cluster import Cluster, Signature
from discovery.model import Classification, Outcome, Verdict
from discovery.outcomes import route
from discovery.triage import TriageCategory, TriageVerdict, signature_key

PROJECT = "/proj"


def group(frame="src/pkg/a.py:10", tests=("t.py::test_a",)):
    return Cluster(
        signature=Signature("AssertionError", frame, "assert N == N"),
        nodeids=list(tests),
        examples=["v=-73"],
        sample="t.py:4: in test_a\nE  assert -73 >= 0\n",
    )


def verdict(category, confidence=0.9, reasoning="read the code"):
    return TriageVerdict(TriageCategory(category), confidence, reasoning)


def classified(nodeid, kind):
    return Classification(
        nodeid=nodeid,
        verdict=kind,
        baseline=Outcome.PASSED,
        crosshair=Outcome.FAILED,
        exception_type="AssertionError",
    )


def run(clusters, answers, classifications, **kw):
    decided = {signature_key(g.signature): a for g, a in zip(clusters, answers)}
    return route(
        clusters,
        decided,
        classifications,
        project=PROJECT,
        commit="c0ffee",
        crosshair_version="0.0.111",
        baseline_examples=200,
        baseline_seeds=3,
        today="2026-10-03",
        **kw,
    )


def test_a_confirmed_project_bug_becomes_a_trophy_draft():
    one = group()
    routing = run(
        [one],
        [verdict("project_bug")],
        [classified("t.py::test_a", Verdict.TROPHY_CANDIDATE)],
    )
    assert len(routing.trophies) == 1
    trophy = routing.trophies[0]
    assert trophy.evidence.commit == "c0ffee"
    assert trophy.evidence.falsifying_example == "v=-73"
    assert trophy.found_on == "2026-10-03"
    assert "200 examples across 3 seeds (600 draws)" in (
        trophy.why_random_search_misses_it
    )


def test_an_unconfirmed_project_bug_is_withheld():
    """pending_validation means the replay was inconclusive, which is not
    evidence; triage cannot stand in for the replay that did not run."""
    one = group()
    routing = run(
        [one],
        [verdict("project_bug")],
        [classified("t.py::test_a", Verdict.PENDING_VALIDATION)],
    )
    assert routing.trophies == []
    assert len(routing.withheld) == 1


def test_a_refuted_example_is_never_a_trophy():
    one = group()
    routing = run(
        [one],
        [verdict("project_bug", confidence=1.0)],
        [classified("t.py::test_a", Verdict.CROSSHAIR_FALSE_POSITIVE)],
    )
    assert routing.trophies == []
    assert len(routing.withheld) == 1


def test_triage_can_withdraw_a_trophy_the_classifier_proposed():
    """The classifier cannot read code; this is what triage is for."""
    one = group()
    routing = run(
        [one],
        [verdict("crosshair_artifact")],
        [classified("t.py::test_a", Verdict.TROPHY_CANDIDATE)],
    )
    assert routing.trophies == []
    assert len(routing.crosshair_defects) == 1


def test_a_crosshair_artifact_needs_no_clean_room():
    """It is a bug in our own tooling, so a third-party replay says nothing."""
    one = group()
    routing = run(
        [one],
        [verdict("crosshair_artifact")],
        [classified("t.py::test_a", Verdict.PENDING_VALIDATION)],
    )
    assert len(routing.crosshair_defects) == 1


def test_an_overstrong_property_is_dismissed():
    one = group()
    routing = run(
        [one],
        [verdict("overstrong_property")],
        [classified("t.py::test_a", Verdict.TROPHY_CANDIDATE)],
    )
    assert routing.trophies == []
    assert len(routing.dismissed) == 1


def test_unclear_goes_to_a_person():
    one = group()
    routing = run(
        [one],
        [verdict("unclear", confidence=0.3)],
        [classified("t.py::test_a", Verdict.TROPHY_CANDIDATE)],
    )
    assert routing.trophies == []
    assert len(routing.needs_human) == 1


def test_an_untriaged_cluster_is_not_dismissed():
    """Silence from triage is not a decision."""
    routing = route(
        [group()],
        {},
        [classified("t.py::test_a", Verdict.TROPHY_CANDIDATE)],
        project=PROJECT,
    )
    assert routing.total == 0


def test_evidence_records_what_the_classifier_said():
    one = group(tests=("t.py::test_a", "t.py::test_b"))
    routing = run(
        [one],
        [verdict("project_bug")],
        [
            classified("t.py::test_a", Verdict.TROPHY_CANDIDATE),
            classified("t.py::test_b", Verdict.PENDING_VALIDATION),
        ],
    )
    assert routing.trophies[0].evidence.classifier_verdicts == [
        "pending_validation",
        "trophy_candidate",
    ]


def test_one_confirmed_test_in_a_cluster_is_enough():
    """A cluster is one defect; a replay confirming it once confirms it."""
    one = group(tests=("t.py::test_a", "t.py::test_b"))
    routing = run(
        [one],
        [verdict("project_bug")],
        [
            classified("t.py::test_a", Verdict.PENDING_VALIDATION),
            classified("t.py::test_b", Verdict.TROPHY_CANDIDATE),
        ],
    )
    assert len(routing.trophies) == 1


def test_every_cluster_lands_somewhere():
    clusters = [group(frame=f"src/pkg/a.py:{i}") for i in range(4)]
    answers = [
        verdict("project_bug"),
        verdict("crosshair_artifact"),
        verdict("overstrong_property"),
        verdict("unclear"),
    ]
    routing = run(
        clusters, answers, [classified("t.py::test_a", Verdict.TROPHY_CANDIDATE)]
    )
    assert routing.total == 4
