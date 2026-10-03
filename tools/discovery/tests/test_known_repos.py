"""Checks the recorded index, whose entries are a snapshot rather than truth."""

import json

from discovery.known_repos import SOURCE, KnownRepo, candidates, load, with_tests

REQUIREMENTS = (
    "attrs==24.2.0\n"
    "hypothesis==6.112.5\n"
    "pytest==8.2.2\n"
    "# a comment\n"
    "\n"
    "sortedcontainers==2.4.0\n"
)


def index(tmp_path, entries):
    path = tmp_path / "index.json"
    path.write_text(json.dumps(entries))
    return str(path)


def test_an_entry_becomes_a_candidate_with_its_repository_url():
    entry = KnownRepo(name="owner/thing", nodeids=["t.py::test_a"])
    candidate = entry.as_candidate()
    assert candidate.repo_url == "https://github.com/owner/thing"
    assert candidate.known_nodeids == ["t.py::test_a"]
    assert candidate.source == SOURCE


def test_a_candidate_name_is_usable_as_a_directory():
    """The probe clones into a directory named after the candidate."""
    assert "/" not in KnownRepo(name="owner/thing").as_candidate().name


def test_recorded_pins_are_reduced_to_distribution_names():
    entry = KnownRepo(name="a/b", requirements=REQUIREMENTS.strip().splitlines())
    assert entry.test_dependencies == ["attrs", "sortedcontainers"]


def test_what_provisioning_supplies_is_never_taken_from_the_index():
    """A recorded pin would hold Hypothesis at a version the plugin predates."""
    entry = KnownRepo(name="a/b", requirements=["hypothesis==6.112.5", "pytest==8.2.2"])
    assert entry.test_dependencies == []


def test_an_entry_with_no_recorded_tests_is_not_a_candidate(tmp_path):
    path = index(
        tmp_path,
        {
            "a/empty": {"node_ids": [], "requirements.txt": ""},
            "a/full": {"node_ids": ["t.py::test_a"], "requirements.txt": ""},
        },
    )
    assert [c.name for c in candidates(path)] == ["a__full"]


def test_more_recorded_tests_sorts_earlier():
    entries = [
        KnownRepo(name="a/few", nodeids=["x"]),
        KnownRepo(name="a/many", nodeids=["x", "y", "z"]),
    ]
    assert [e.name for e in with_tests(entries)] == ["a/many", "a/few"]


def test_an_array_dependency_sorts_after_everything_without_one():
    """The solver realizes at that boundary however many tests there are."""
    heavy = KnownRepo(
        name="a/heavy", nodeids=["x"] * 500, requirements=["numpy==1.26.0"]
    )
    light = KnownRepo(name="a/light", nodeids=["x"])
    assert [e.name for e in with_tests([heavy, light])] == ["a/light", "a/heavy"]
    assert heavy.opaque_dependencies == ["numpy"]


def test_a_dashed_distribution_matches_its_module_name():
    entry = KnownRepo(name="a/b", requirements=["scikit-learn==1.5.0", "pandas==2.0"])
    assert entry.opaque_dependencies == ["pandas"], "sklearn is not scikit-learn"


def test_a_budget_cuts_the_queue_without_reordering_it(tmp_path):
    path = index(
        tmp_path,
        {
            f"a/p{i}": {"node_ids": ["x"] * (10 - i), "requirements.txt": ""}
            for i in range(5)
        },
    )
    assert [c.name for c in candidates(path, budget=2)] == ["a__p0", "a__p1"]


def test_comments_and_blank_lines_are_not_requirements(tmp_path):
    path = index(
        tmp_path, {"a/b": {"node_ids": ["x"], "requirements.txt": REQUIREMENTS}}
    )
    assert candidates(path)[0].test_dependencies == ["attrs", "sortedcontainers"]


def test_a_trailing_comment_is_not_part_of_the_name():
    entry = KnownRepo(name="a/b", requirements=["attrs==24.2.0  # pinned by hand"])
    assert entry.test_dependencies == ["attrs"]


def test_a_missing_requirements_field_is_not_an_error(tmp_path):
    path = index(tmp_path, {"a/b": {"node_ids": ["x"]}})
    assert candidates(path)[0].test_dependencies == []
