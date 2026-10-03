"""A recorded index of repositories already known to carry Hypothesis tests.

The PyPI route ranks by download count and discovers whether a project has
property tests by cloning it, which costs a clone per candidate at a hit rate
in the single-digit percent. An index like this inverts that: every entry was
observed to have tests when the index was built, so the clone confirms rather
than discovers.

What it records is a snapshot. Repositories get renamed, archived and
deleted, and tests move, so an entry is a lead and the survey still decides.
The pinned versions in a recorded requirements file are stale by
construction, and in particular pin a Hypothesis older than the plugin
supports; only the distribution names are used.
"""

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from .candidates import OPAQUE_IMPORTS
from .probe import Candidate
from .pypi import requirement_name

SOURCE = "recorded-index"

#: Supplied by provisioning itself, so a recorded pin must never fight it.
PROVISIONED_SEPARATELY = frozenset(
    {"hypothesis", "pytest", "crosshair-tool", "hypothesis-crosshair"}
)

#: Hosts an entry's name can be resolved against when it carries no URL.
DEFAULT_HOST = "https://github.com"


@dataclass
class KnownRepo:
    """One entry: a repository, the tests seen in it, and what it needed."""

    name: str
    nodeids: List[str] = field(default_factory=list)
    requirements: List[str] = field(default_factory=list)

    @property
    def repo_url(self) -> str:
        return f"{DEFAULT_HOST}/{self.name}"

    @property
    def test_dependencies(self) -> List[str]:
        """Distribution names the entry recorded, without their stale pins."""
        seen = []
        for line in self.requirements:
            name = requirement_name(line)
            if name and name not in PROVISIONED_SEPARATELY and name not in seen:
                seen.append(name)
        return seen

    @property
    def opaque_dependencies(self) -> List[str]:
        """Recorded dependencies that make CrossHair realize immediately."""
        return [
            name
            for name in self.test_dependencies
            if name.replace("-", "_") in OPAQUE_IMPORTS
        ]

    def as_candidate(self) -> Candidate:
        return Candidate(
            name=self.name.replace("/", "__"),
            repo_url=self.repo_url,
            source=SOURCE,
            known_nodeids=list(self.nodeids),
            test_dependencies=self.test_dependencies,
        )


def load(path: str) -> List[KnownRepo]:
    """Read an index mapping ``owner/repo`` to its recorded node ids."""
    with open(path) as handle:
        raw: Dict[str, dict] = json.load(handle)
    found = []
    for name, entry in raw.items():
        text = entry.get("requirements.txt") or ""
        found.append(
            KnownRepo(
                name=name,
                nodeids=list(entry.get("node_ids") or []),
                requirements=[line.strip() for line in text.splitlines()],
            )
        )
    return found


def with_tests(every: Sequence[KnownRepo]) -> List[KnownRepo]:
    """Entries that recorded at least one test, best candidate first.

    An entry whose recorded dependencies include an array or dataframe
    library sorts after every entry without one, however many tests it has:
    the solver realizes at that boundary and the run degrades to slow random
    testing. Test count orders the rest.
    """
    carrying = [entry for entry in every if entry.nodeids]
    return sorted(
        carrying,
        key=lambda entry: (
            bool(entry.opaque_dependencies),
            -len(entry.nodeids),
            entry.name,
        ),
    )


def candidates(path: str, budget: int = 0) -> List[Candidate]:
    """Candidates from an index file, the most tests first."""
    ranked = with_tests(load(path))
    chosen = ranked[:budget] if budget else ranked
    return [entry.as_candidate() for entry in chosen]
