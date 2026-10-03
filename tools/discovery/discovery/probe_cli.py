"""Entry point for finding projects worth provisioning.

Candidates come either from the PyPI download ranking, where whether a
project has property tests is discovered by cloning it, or from a recorded
index of repositories already known to carry them.
"""

import argparse
import json
import sys
from typing import List, Optional

from .known_repos import candidates as recorded_candidates
from .probe import probe_all
from .pypi import facts_from, metadata, shortlist, top_packages


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discovery-probe",
        description=(
            "Rank PyPI packages, clone the plausible ones shallowly, and "
            "report which actually carry Hypothesis property tests."
        ),
    )
    parser.add_argument("--top", type=int, default=200, help="packages to consider")
    parser.add_argument(
        "--index",
        default="",
        help=(
            "a recorded index of repositories known to carry Hypothesis "
            "tests, used instead of the PyPI download ranking"
        ),
    )
    parser.add_argument("--budget", type=int, default=25, help="checkouts to fetch")
    parser.add_argument("--cache", default="", help="directory for PyPI responses")
    parser.add_argument("--work", required=True, help="directory for checkouts")
    parser.add_argument(
        "--keep", action="store_true", help="leave checkouts in place to provision"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if args.index:
        chosen = recorded_candidates(args.index, args.budget)
        print(
            f"{len(chosen)} candidate(s) from the recorded index",
            file=sys.stderr,
        )
    else:
        rows = top_packages(args.top)
        facts = []
        for row in rows:
            payload = metadata(row["project"], args.cache)
            if payload is not None:
                facts.append(facts_from(payload, row["download_count"]))
        shortlisted = shortlist(facts, args.budget)
        print(
            f"{len(facts)} packages read, {len(shortlisted)} cloned of "
            f"{len([f for f in facts if f.repo_url])} with a repository",
            file=sys.stderr,
        )
        chosen = [f.as_candidate() for f in shortlisted]

    results = []
    for result in probe_all(chosen, args.work, keep=args.keep):
        results.append(result)
        if not args.json:
            print(result.describe())
    worth = [r for r in results if r.worth_provisioning]
    worth.sort(key=lambda r: -r.assessment.score)

    if args.json:
        print(
            json.dumps(
                [
                    {
                        "name": r.candidate.name,
                        "repo": r.candidate.repo_url,
                        "score": r.assessment.score,
                        "property_tests": len(r.assessment.survey.tests),
                        "state_machines": len(r.assessment.survey.state_machines),
                        "seconds": round(r.seconds, 1),
                    }
                    for r in worth
                ],
                indent=1,
            )
        )
    else:
        print(f"\n{len(worth)}/{len(results)} carry Hypothesis tests, best first:")
        for result in worth:
            print(f"  {result.describe()}")
        failed = [r for r in results if r.error]
        if failed:
            print(f"\n{len(failed)} could not be read:")
            for result in failed:
                print(f"  {result.describe()}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
