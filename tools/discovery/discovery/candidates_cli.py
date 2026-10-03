"""Entry point for surveying candidate checkouts before provisioning them."""

import argparse
import json
import os
import sys
from typing import List, Optional

from .candidates import assess, order, survey


def _as_json(assessed) -> dict:
    return {
        "project": assessed.survey.project,
        "runnable": assessed.runnable,
        "blocker": assessed.blocker,
        "score": assessed.score,
        "property_tests": len(assessed.survey.tests),
        "state_machines": len(assessed.survey.state_machines),
        "domain_strategies": len(assessed.survey.composites),
        "unreadable_files": len(assessed.survey.unparsed),
        "hidden_tests": assessed.survey.hidden_tests,
        "notes": assessed.notes,
        "signals": {
            signal.name: round(signal.points, 1) for signal in assessed.signals
        },
    }


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discovery-candidates",
        description=(
            "Rank checkouts by how worthwhile a CrossHair run looks, reading "
            "their source without importing or executing any of it."
        ),
    )
    parser.add_argument(
        "paths", nargs="+", help="project checkouts, or a parent of them"
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="also list projects with no property tests to run",
    )
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--top", type=int, default=0, help="keep only the best N runnable candidates"
    )
    args = parser.parse_args(argv)

    roots = []
    for path in args.paths:
        if not os.path.isdir(path):
            print(f"not a directory: {path}", file=sys.stderr)
            return 2
        entries = sorted(
            os.path.join(path, name)
            for name in os.listdir(path)
            if os.path.isdir(os.path.join(path, name))
        )
        if any(
            os.path.exists(os.path.join(path, marker))
            for marker in ("pyproject.toml", "setup.py", "setup.cfg")
        ):
            roots.append(path)
        else:
            roots.extend(entries)

    assessed = order([assess(survey(root)) for root in roots])
    runnable = [a for a in assessed if a.runnable]
    blocked = [a for a in assessed if not a.runnable]
    kept = runnable[: args.top] if args.top else runnable
    shown = kept + (blocked if args.all else [])

    if args.json:
        print(json.dumps([_as_json(a) for a in shown], indent=1))
    else:
        for item in shown:
            print("\n".join(item.describe()))
        kept_note = f", showing {len(kept)}" if len(kept) != len(runnable) else ""
        print(
            f"\n{len(runnable)} candidate(s) worth provisioning{kept_note}, "
            f"{len(blocked)} with nothing to run"
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
