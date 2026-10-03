"""Checks the probe, against local repositories rather than the network."""

import os
import subprocess

from discovery.probe import Candidate, probe, probe_all

GIVEN = """\
from hypothesis import given, strategies as st

@given(st.integers())
def test_one(n):
    assert n == n
"""


def repository(tmp_path, name, files):
    root = tmp_path / name
    for relative, source in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "initial"],
    ):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
    return Candidate(name=name, repo_url=str(root))


def test_a_repository_with_property_tests_is_worth_provisioning(tmp_path):
    facts = repository(tmp_path, "good", {"tests/test_a.py": GIVEN})
    result = probe(facts, str(tmp_path / "work"))
    assert result.worth_provisioning
    assert len(result.assessment.survey.tests) == 1
    assert "1 property tests" in result.describe()


def test_a_repository_without_them_is_not(tmp_path):
    facts = repository(tmp_path, "bare", {"pkg/lib.py": "x = 1\n"})
    result = probe(facts, str(tmp_path / "work"))
    assert not result.worth_provisioning
    assert result.error == ""
    assert "no property tests" in result.describe()


def test_the_checkout_is_removed_once_it_has_been_read(tmp_path):
    facts = repository(tmp_path, "gone", {"tests/test_a.py": GIVEN})
    work = tmp_path / "work"
    probe(facts, str(work))
    assert not os.path.exists(work / "gone")


def test_keeping_a_checkout_leaves_it_for_provisioning(tmp_path):
    facts = repository(tmp_path, "kept", {"tests/test_a.py": GIVEN})
    work = tmp_path / "work"
    result = probe(facts, str(work), keep=True)
    assert os.path.isdir(work / "kept")
    assert result.worth_provisioning


def test_a_repository_that_cannot_be_cloned_is_reported_not_raised(tmp_path):
    facts = Candidate(name="missing", repo_url=str(tmp_path / "nowhere"))
    result = probe(facts, str(tmp_path / "work"))
    assert result.error
    assert result.assessment is None
    assert not result.worth_provisioning
    assert "could not be read" not in result.describe()
    assert "could not read" in result.describe()


def test_one_unreadable_candidate_does_not_stop_the_rest(tmp_path):
    good = repository(tmp_path, "good", {"tests/test_a.py": GIVEN})
    broken = Candidate(name="broken", repo_url=str(tmp_path / "nowhere"))
    results = list(probe_all([broken, good], str(tmp_path / "work")))
    assert [r.candidate.name for r in results] == ["broken", "good"]
    assert results[1].worth_provisioning


def test_a_stale_checkout_is_replaced_rather_than_cloned_into(tmp_path):
    facts = repository(tmp_path, "again", {"tests/test_a.py": GIVEN})
    work = tmp_path / "work"
    work.mkdir()
    (work / "again").mkdir()
    (work / "again" / "junk.py").write_text("x = 1\n")
    result = probe(facts, str(work), keep=True)
    assert result.worth_provisioning
    assert not os.path.exists(work / "again" / "junk.py")


def test_a_checkout_over_the_budget_is_deleted_unread(tmp_path):
    """A monorepo publishing many packages can exhaust a sandbox's disk."""
    facts = repository(
        tmp_path, "huge", {"tests/test_a.py": GIVEN, "data.bin": "x" * 2_200_000}
    )
    work = tmp_path / "work"
    result = probe(facts, str(work), keep=True, max_megabytes=1)
    assert result.assessment is None
    assert "over the 1MB budget" in result.error
    assert result.megabytes >= 2
    assert not os.path.exists(work / "huge"), "an oversized checkout must not linger"


def test_a_checkout_within_the_budget_is_read_as_usual(tmp_path):
    facts = repository(tmp_path, "small", {"tests/test_a.py": GIVEN})
    result = probe(facts, str(tmp_path / "work"), max_megabytes=500)
    assert result.worth_provisioning
    assert result.error == ""
