"""Reading PyPI metadata to decide which checkouts are worth fetching.

Nothing here can tell whether a project has Hypothesis tests. Hypothesis is a
development dependency, and PyPI metadata carries only runtime ones, so the
question is answered exactly by ``candidates.survey`` against a checkout --
which costs a shallow clone. The job here is to order tens of thousands of
package names so that those clones go to plausible projects first.

One fact rejects a package: no repository to clone. Everything else is a
rank, and the budget decides where the line falls, so a package below the
line is reachable by raising the budget rather than struck off.
"""

import json
import math
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Set

from .candidates import OPAQUE_IMPORTS
from .probe import Candidate

#: Download ranks published from the public PyPI download tables.
TOP_PACKAGES_URL = (
    "https://raw.githubusercontent.com/hugovk/top-pypi-packages/main/"
    "top-pypi-packages.json"
)

PYPI_JSON = "https://pypi.org/pypi/{name}/json"

#: Hosts a checkout can be cloned from.
VCS_HOSTS = ("github.com", "gitlab.com", "bitbucket.org", "codeberg.org")

#: ``project_urls`` keys that name the source itself, preferred over the rest.
SOURCE_KEYS = ("source", "repository", "code", "git", "homepage", "home")

#: Path segments that begin a page about a repository rather than the
#: repository, so everything from one of these onward is dropped.
NON_REPO_SEGMENTS = frozenset(
    {
        "issues",
        "pull",
        "pulls",
        "blob",
        "tree",
        "wiki",
        "releases",
        "discussions",
        "actions",
        "commits",
        "tags",
        "security",
        "-",
    }
)

#: Downloads above which popularity stops adding to a score.
DOWNLOADS_SATURATION = 1_000_000_000

PREFILTER_WEIGHTS = {
    "pure_python": 50,
    "no_opaque_dependency": 30,
    "reach": 20,
}

USER_AGENT = "hypothesis-crosshair-discovery (+https://github.com/pschanely)"


@dataclass
class PackageFacts:
    """What PyPI says about a package, before anything is cloned."""

    name: str
    downloads: int = 0
    repo_url: str = ""
    #: Wheel platform tags, empty when the package publishes no wheel.
    wheel_tags: Set[str] = field(default_factory=set)
    runtime_dependencies: List[str] = field(default_factory=list)
    requires_python: str = ""

    @property
    def pure_python(self) -> Optional[bool]:
        """Whether every published wheel is platform independent.

        ``None`` when the package publishes no wheel at all, which says
        nothing either way.
        """
        if not self.wheel_tags:
            return None
        return self.wheel_tags == {"any"}

    def as_candidate(self) -> "Candidate":
        return Candidate(
            name=self.name, repo_url=self.repo_url, source="pypi-downloads"
        )

    @property
    def opaque_dependencies(self) -> List[str]:
        return sorted(
            {
                dep
                for dep in self.runtime_dependencies
                if dep.split(".")[0] in OPAQUE_IMPORTS
            }
        )


def requirement_name(requirement: str) -> str:
    """The distribution a requirement line names, without version or extras."""
    for separator in (";", "[", "(", "=", "<", ">", "!", "~", " ", "#"):
        requirement = requirement.split(separator)[0]
    return requirement.strip().lower().replace("_", "-")


def _trim_to_repository(url: str) -> str:
    scheme, _, rest = url.partition("://")
    segments = rest.rstrip("/").split("/")
    kept = [segments[0]]
    for segment in segments[1:]:
        if segment.lower() in NON_REPO_SEGMENTS:
            break
        kept.append(segment)
    trimmed = "/".join(kept)
    if trimmed.endswith(".git"):
        trimmed = trimmed[: -len(".git")]
    return f"{scheme}://{trimmed}" if scheme else trimmed


def repository_url(info: dict) -> str:
    """A URL in a package's metadata that a checkout can be cloned from.

    A key naming the source is preferred, because ``project_urls`` often
    lists a bug tracker on the same host first.
    """
    project_urls = info.get("project_urls") or {}
    preferred = [
        url
        for key, url in project_urls.items()
        if any(want in key.lower() for want in SOURCE_KEYS)
    ]
    rest = [url for key, url in project_urls.items() if url not in preferred]
    for key in ("home_page", "download_url"):
        if info.get(key):
            rest.append(info[key])
    for url in preferred + rest:
        if isinstance(url, str) and any(host in url for host in VCS_HOSTS):
            return _trim_to_repository(url)
    return ""


def _wheel_tags(releases: Sequence[dict]) -> Set[str]:
    tags = set()
    for entry in releases:
        if entry.get("packagetype") != "bdist_wheel":
            continue
        name = entry.get("filename", "")
        if name.endswith(".whl"):
            tags.add(name[: -len(".whl")].rsplit("-", 1)[-1])
    return tags


def facts_from(payload: dict, downloads: int = 0) -> PackageFacts:
    """Read one PyPI JSON response into the facts a shortlist needs."""
    info = payload.get("info") or {}
    return PackageFacts(
        name=info.get("name", ""),
        downloads=downloads,
        repo_url=repository_url(info),
        wheel_tags=_wheel_tags(payload.get("urls") or []),
        runtime_dependencies=[
            requirement_name(req) for req in (info.get("requires_dist") or [])
        ],
        requires_python=info.get("requires_python") or "",
    )


def score(facts: PackageFacts) -> int:
    """How promising a package looks before its source has been read."""
    points = 0.0
    if facts.pure_python:
        points += PREFILTER_WEIGHTS["pure_python"]
    elif facts.pure_python is None:
        points += PREFILTER_WEIGHTS["pure_python"] / 2
    if not facts.opaque_dependencies:
        points += PREFILTER_WEIGHTS["no_opaque_dependency"]
    if facts.downloads > 0:
        reach = math.log10(1 + min(facts.downloads, DOWNLOADS_SATURATION)) / math.log10(
            1 + DOWNLOADS_SATURATION
        )
        points += PREFILTER_WEIGHTS["reach"] * reach
    return round(points)


def shortlist(every: Sequence[PackageFacts], budget: int = 0) -> List[PackageFacts]:
    """The repositories worth cloning, best first, cut to what can be afforded.

    Several packages published from one repository yield one checkout, since
    surveying it twice reads the same tree twice. Only a package with nowhere
    to clone from is dropped. The budget is a line through a ranking, not a
    judgment about what is below it.
    """
    clonable = [facts for facts in every if facts.repo_url]
    clonable.sort(key=lambda facts: (-score(facts), -facts.downloads, facts.name))
    seen: Dict[str, PackageFacts] = {}
    for facts in clonable:
        seen.setdefault(facts.repo_url, facts)
    unique = list(seen.values())
    return unique[:budget] if budget else unique


def _get(url: str, timeout: float) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def top_packages(limit: int = 0, timeout: float = 60.0) -> List[Dict[str, object]]:
    """Package names in descending order of download count."""
    rows = json.loads(_get(TOP_PACKAGES_URL, timeout))["rows"]
    return rows[:limit] if limit else rows


def metadata(name: str, cache_dir: str = "", timeout: float = 30.0) -> Optional[dict]:
    """One package's PyPI JSON, cached on disk so a rerun costs no requests."""
    cached = os.path.join(cache_dir, f"{name}.json") if cache_dir else ""
    if cached and os.path.exists(cached):
        with open(cached) as handle:
            return json.load(handle)
    try:
        payload = json.loads(_get(PYPI_JSON.format(name=name), timeout))
    except (urllib.error.URLError, urllib.error.HTTPError, ValueError, OSError):
        return None
    if cached:
        os.makedirs(cache_dir, exist_ok=True)
        with open(cached, "w") as handle:
            json.dump(payload, handle)
    return payload
