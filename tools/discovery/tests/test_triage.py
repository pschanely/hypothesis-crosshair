"""Checks the deterministic half of triage: the queue and the schema gate.

The decider is whatever reads a cluster and answers. Everything here is about
what happens around it, and in particular about what an unusable answer is not
allowed to do.
"""

import pytest
from discovery.cluster import Cluster, Signature
from discovery.store import Store
from discovery.triage import (
    TriageCategory,
    TriageSchemaError,
    enqueue_clusters,
    next_item,
    parse_verdict,
    run_triage,
    signature_key,
)

PROJECT = "/proj"


def group(frame="src/pkg/a.py:10", exc="AssertionError", tests=("t.py::test_a",)):
    return Cluster(
        signature=Signature(exc, frame, "assert N == N"),
        nodeids=list(tests),
        examples=["x=1"],
    )


def answer(category="project_bug", confidence=0.9, reasoning="looks real", **extra):
    return {
        "category": category,
        "confidence": confidence,
        "reasoning": reasoning,
        **extra,
    }


@pytest.fixture
def store(tmp_path):
    with Store(str(tmp_path / "s.db")) as opened:
        yield opened


def clock():
    clock.t += 1.0
    return clock.t


clock.t = 1000.0


# --- the schema gate -------------------------------------------------------


def test_a_well_formed_answer_parses():
    verdict = parse_verdict(answer(evidence=["src/pkg/a.py:10"]))
    assert verdict.category is TriageCategory.PROJECT_BUG
    assert verdict.confidence == 0.9
    assert verdict.evidence == ["src/pkg/a.py:10"]
    assert not verdict.needs_human


@pytest.mark.parametrize(
    "payload, because",
    [
        ("project_bug", "a bare string is not an answer"),
        ({"confidence": 0.5, "reasoning": "x"}, "no category"),
        ({"category": "project_bug", "reasoning": "x"}, "no confidence"),
        ({"category": "project_bug", "confidence": 0.5}, "no reasoning"),
        (answer(category="definitely_a_bug"), "invented category"),
        (answer(confidence=1.5), "confidence above the range"),
        (answer(confidence=-0.1), "confidence below the range"),
        (answer(confidence="high"), "confidence not a number"),
        (answer(confidence=True), "a bool is not a confidence"),
        (answer(reasoning="   "), "empty reasoning"),
        (answer(evidence="src/pkg/a.py"), "evidence not a list"),
        (answer(evidence=[3]), "evidence not strings"),
    ],
)
def test_a_malformed_answer_is_refused(payload, because):
    with pytest.raises(TriageSchemaError):
        parse_verdict(payload)


def test_unclear_is_routed_to_a_person():
    assert parse_verdict(answer(category="unclear")).needs_human


# --- the queue -------------------------------------------------------------


def test_one_cluster_keeps_its_identity_across_runs():
    first = group()
    second = group()
    assert signature_key(first.signature) == signature_key(second.signature)
    assert signature_key(first.signature) != signature_key(
        group(frame="src/pkg/b.py:1").signature
    )


def test_each_cluster_is_triaged_once(store):
    clusters = [group(frame=f"src/pkg/a.py:{i}") for i in range(3)]
    assert enqueue_clusters(store, "r1", clusters, PROJECT) == 3
    assert enqueue_clusters(store, "r1", clusters, PROJECT) == 0, "already queued"

    seen = []

    def decide(item):
        seen.append(item.key)
        return answer()

    outcome = run_triage(store, "r1", decide, clock)
    assert len(seen) == 3
    assert len(outcome.decided) == 3
    assert store.progress("r1", "triage") == {"done": 3}


def test_the_item_carries_what_a_decider_needs(store):
    enqueue_clusters(
        store, "r1", [group(tests=("t.py::test_a", "t.py::test_b"))], PROJECT
    )
    item = next_item(store, "r1", clock())
    assert item is not None
    assert item.signature.frame == "src/pkg/a.py:10"
    assert item.nodeids == ["t.py::test_a", "t.py::test_b"]
    assert item.examples == ["x=1"]
    assert item.project == PROJECT
    assert "reached by 2 test(s)" in item.describe()


def test_an_off_schema_answer_is_not_recorded(store):
    enqueue_clusters(store, "r1", [group()], PROJECT)
    outcome = run_triage(store, "r1", lambda item: answer(category="nonsense"), clock)
    assert outcome.decided == {}
    assert len(outcome.rejected) == 1
    assert store.triaged("r1") == [], "an unparsed answer must not become state"
    assert store.progress("r1", "triage") == {"abandoned": 1}


def test_a_decider_that_raises_costs_only_its_own_cluster(store):
    clusters = [group(frame=f"src/pkg/a.py:{i}") for i in range(3)]
    enqueue_clusters(store, "r1", clusters, PROJECT)

    def decide(item):
        if item.signature.frame.endswith(":1"):
            raise RuntimeError("model unavailable")
        return answer()

    outcome = run_triage(store, "r1", decide, clock)
    assert len(outcome.decided) == 2
    assert len(outcome.rejected) == 1
    assert store.progress("r1", "triage") == {"done": 2, "abandoned": 1}


def test_the_budget_stops_the_run(store):
    clusters = [group(frame=f"src/pkg/a.py:{i}") for i in range(5)]
    enqueue_clusters(store, "r1", clusters, PROJECT)
    outcome = run_triage(store, "r1", lambda item: answer(), clock, budget=2)
    assert outcome.total == 2
    assert store.progress("r1", "triage")["done"] == 2


def test_triage_resumes_where_it_stopped(store):
    clusters = [group(frame=f"src/pkg/a.py:{i}") for i in range(5)]
    enqueue_clusters(store, "r1", clusters, PROJECT)
    run_triage(store, "r1", lambda item: answer(), clock, budget=2)
    seen = []

    def decide(item):
        seen.append(item.key)
        return answer()

    run_triage(store, "r1", decide, clock)
    assert len(seen) == 3, "the two already decided must not be offered again"


def test_decided_clusters_are_queryable_by_category(store):
    enqueue_clusters(store, "r1", [group(), group(frame="src/pkg/b.py:2")], PROJECT)
    categories = iter(["project_bug", "crosshair_artifact"])
    run_triage(store, "r1", lambda item: answer(category=next(categories)), clock)
    found = store.triaged("r1", "crosshair_artifact")
    assert len(found) == 1
    assert found[0]["category"] == "crosshair_artifact"
    assert found[0]["reasoning"] == "looks real"


def test_the_item_carries_a_readable_traceback(store):
    """A decider that only sees `assert N == N` cannot tell what happened."""
    group_with_sample = group()
    group_with_sample.sample = "tests/t.py:4: in test_a\nE   assert -73 >= 0\n"
    enqueue_clusters(store, "r1", [group_with_sample], PROJECT)
    item = next_item(store, "r1", clock())
    assert "assert -73 >= 0" in item.sample
    assert "assert -73 >= 0" in item.as_json()
