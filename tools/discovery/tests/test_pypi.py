"""Checks the PyPI prefilter, which runs before anything has been cloned.

The payload shapes here are trimmed copies of real responses. Nothing in
this file reaches the network.
"""

import json

from discovery.pypi import (
    PackageFacts,
    facts_from,
    metadata,
    repository_url,
    score,
    shortlist,
)


def payload(
    name="thing",
    urls=None,
    project_urls=None,
    requires_dist=None,
    home_page=None,
):
    return {
        "info": {
            "name": name,
            "project_urls": project_urls or {},
            "home_page": home_page,
            "requires_dist": requires_dist,
            "requires_python": ">=3.9",
        },
        "urls": urls or [],
    }


def wheel(filename):
    return {"packagetype": "bdist_wheel", "filename": filename}


SDIST = {"packagetype": "sdist", "filename": "thing-1.0.tar.gz"}
PURE = wheel("thing-1.0-py3-none-any.whl")
NATIVE = wheel("thing-1.0-cp312-cp312-manylinux_2_17_x86_64.whl")


def test_a_platform_independent_wheel_reads_as_pure_python():
    assert facts_from(payload(urls=[PURE, SDIST])).pure_python is True


def test_a_platform_wheel_reads_as_compiled():
    assert facts_from(payload(urls=[PURE, NATIVE])).pure_python is False


def test_no_wheel_at_all_says_nothing_either_way():
    """An sdist-only package is unknown, not compiled: srt is one."""
    assert facts_from(payload(urls=[SDIST])).pure_python is None


def test_an_unknown_build_scores_between_the_two():
    pure = score(facts_from(payload(urls=[PURE])))
    unknown = score(facts_from(payload(urls=[SDIST])))
    native = score(facts_from(payload(urls=[NATIVE])))
    assert native < unknown < pure


def test_an_array_dependency_lowers_the_score():
    heavy = facts_from(payload(urls=[PURE], requires_dist=["numpy>=1.20", "click"]))
    assert heavy.opaque_dependencies == ["numpy"]
    assert score(heavy) < score(facts_from(payload(urls=[PURE])))


def test_a_dependency_marker_is_not_part_of_the_name():
    facts = facts_from(
        payload(requires_dist=['typing-extensions>=4.0; python_version < "3.11"'])
    )
    assert facts.runtime_dependencies == ["typing-extensions"]


def test_an_extra_requirement_is_reduced_to_its_name():
    facts = facts_from(payload(requires_dist=["cattrs[msgpack] (>=1.0)"]))
    assert facts.runtime_dependencies == ["cattrs"]


def test_the_source_url_is_preferred_over_another_link_on_the_same_host():
    """A funding link is on github.com and is not a repository."""
    info = payload(
        project_urls={
            "Funding": "https://github.com/sponsors/someone",
            "Source": "https://github.com/python-attrs/cattrs",
        }
    )["info"]
    assert repository_url(info) == "https://github.com/python-attrs/cattrs"


def test_a_tracker_on_the_repository_still_resolves_to_the_repository():
    """cattrs lists its issue tracker first, which cannot be cloned."""
    info = payload(
        project_urls={"Issues": "https://github.com/python-attrs/cattrs/issues"}
    )["info"]
    assert repository_url(info) == "https://github.com/python-attrs/cattrs"


def test_a_page_about_a_repository_is_trimmed_back_to_it():
    info = payload(
        project_urls={"Changelog": "https://github.com/owner/repo/blob/main/CHANGES"}
    )["info"]
    assert repository_url(info) == "https://github.com/owner/repo"


def test_a_git_suffix_is_dropped():
    info = payload(project_urls={"Source": "https://github.com/owner/repo.git"})["info"]
    assert repository_url(info) == "https://github.com/owner/repo"


def test_the_home_page_is_used_when_there_are_no_project_urls():
    info = payload(home_page="https://github.com/cdown/srt")["info"]
    assert repository_url(info) == "https://github.com/cdown/srt"


def test_a_url_on_no_known_host_is_not_a_repository():
    info = payload(project_urls={"Homepage": "https://example.com/docs"})["info"]
    assert repository_url(info) == ""


def test_only_a_package_with_nowhere_to_clone_is_dropped():
    nowhere = PackageFacts(name="nowhere", downloads=10**9)
    poor = PackageFacts(name="poor", repo_url="https://github.com/a/b")
    kept = shortlist([nowhere, poor])
    assert [f.name for f in kept] == ["poor"]


def test_the_budget_is_a_line_not_a_judgement():
    every = [
        PackageFacts(name=f"p{i}", repo_url=f"https://github.com/a/p{i}", downloads=i)
        for i in range(5)
    ]
    assert len(shortlist(every, budget=2)) == 2
    assert len(shortlist(every)) == 5, "no budget keeps everything clonable"


def test_downloads_break_a_tie_but_do_not_outrank_fitness():
    popular_native = PackageFacts(
        name="native",
        repo_url="https://github.com/a/native",
        downloads=10**9,
        wheel_tags={"manylinux_2_17_x86_64"},
    )
    quiet_pure = PackageFacts(
        name="pure",
        repo_url="https://github.com/a/pure",
        downloads=1,
        wheel_tags={"any"},
    )
    assert [f.name for f in shortlist([popular_native, quiet_pure])] == [
        "pure",
        "native",
    ]


def test_metadata_is_read_from_the_cache_without_a_request(tmp_path):
    """The name must be one PyPI cannot serve, or a request would succeed."""
    name = "hypothesis-crosshair-no-such-package-9f3a"
    (tmp_path / f"{name}.json").write_text(json.dumps(payload(name=name)))
    assert metadata(name, str(tmp_path))["info"]["name"] == name


def test_one_repository_yields_one_checkout():
    """11 of the top 300 packages are published from one monorepo."""
    shared = [
        PackageFacts(
            name=f"p{i}",
            repo_url="https://github.com/googleapis/google-cloud-python",
            downloads=100 - i,
            wheel_tags={"any"},
        )
        for i in range(4)
    ]
    other = PackageFacts(
        name="other", repo_url="https://github.com/a/other", wheel_tags={"any"}
    )
    kept = shortlist(shared + [other])
    assert len(kept) == 2
    assert {f.repo_url for f in kept} == {
        "https://github.com/googleapis/google-cloud-python",
        "https://github.com/a/other",
    }


def test_the_best_scoring_package_represents_its_repository():
    quiet = PackageFacts(
        name="quiet", repo_url="https://github.com/a/b", downloads=1, wheel_tags={"any"}
    )
    busy = PackageFacts(
        name="busy",
        repo_url="https://github.com/a/b",
        downloads=10**9,
        wheel_tags={"any"},
    )
    assert [f.name for f in shortlist([quiet, busy])] == ["busy"]


def test_the_budget_counts_repositories_not_packages():
    shared = [
        PackageFacts(
            name=f"p{i}", repo_url="https://github.com/a/mono", wheel_tags={"any"}
        )
        for i in range(5)
    ]
    other = PackageFacts(
        name="o", repo_url="https://github.com/a/o", wheel_tags={"any"}
    )
    assert len(shortlist(shared + [other], budget=2)) == 2
