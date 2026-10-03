import time

from discovery.model import Classification, Outcome, Verdict
from discovery.store import Store, cache_key


def item(nodeid="t.py::test_x", verdict=Verdict.TROPHY_CANDIDATE):
    return Classification(
        nodeid=nodeid,
        verdict=verdict,
        baseline=Outcome.PASSED,
        crosshair=Outcome.FAILED,
        validation=Outcome.FAILED,
        rationale="reproduced without the plugin",
        falsifying_example="test_x(a=1)",
    )


def test_verdicts_round_trip(tmp_path):
    with Store(str(tmp_path / "s.db")) as store:
        store.record_run("r1", "/proj", "abc123", time.time(), {"python": "3.11"})
        store.record_verdicts("r1", [item(), item("t.py::test_y", Verdict.NO_SIGNAL)])
        assert len(store.verdicts("r1")) == 2
        trophies = store.verdicts("r1", Verdict.TROPHY_CANDIDATE.value)
        assert len(trophies) == 1
        assert trophies[0]["falsifying_example"] == "test_x(a=1)"


def test_recording_the_same_verdict_twice_replaces_it(tmp_path):
    with Store(str(tmp_path / "s.db")) as store:
        store.record_run("r1", "/proj", "abc", time.time(), {})
        store.record_verdict("r1", item())
        store.record_verdict("r1", item(verdict=Verdict.NO_SIGNAL))
        rows = store.verdicts("r1")
        assert len(rows) == 1
        assert rows[0]["verdict"] == Verdict.NO_SIGNAL.value


def test_cache_round_trips_and_misses_cleanly(tmp_path):
    with Store(str(tmp_path / "s.db")) as store:
        key = cache_key(
            commit_sha="abc",
            nodeid="t.py::test_x",
            crosshair_version="0.0.106",
            plugin_version="0.0.30",
            python_version="3.11",
        )
        assert store.cached(key) is None
        store.put_cache(key, item())
        assert store.cached(key)["verdict"] == Verdict.TROPHY_CANDIDATE.value


def test_a_version_bump_invalidates_every_cached_verdict():
    """The re-run after a release is what makes this a regression suite."""
    base = dict(
        commit_sha="abc",
        nodeid="t.py::test_x",
        plugin_version="0.0.30",
        python_version="3.11",
    )
    assert cache_key(crosshair_version="0.0.106", **base) != cache_key(
        crosshair_version="0.0.107", **base
    )
