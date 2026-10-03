"""Entry point for scoring a triage decider against labelled cases."""

import argparse
import shlex
import sys
from typing import List, Optional

from .evaluate import load_cases, score
from .triage import CommandDecider


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discovery-eval",
        description="Score a triage decider against cases with known answers.",
    )
    parser.add_argument("--cases", required=True, help="JSONL file of labelled cases")
    parser.add_argument(
        "--decider",
        required=True,
        help="command that triages one cluster, as --triage-command takes it",
    )
    parser.add_argument("--timeout", type=float, default=300.0)
    args = parser.parse_args(argv)

    cases = load_cases(args.cases)
    if not cases:
        print(f"no cases in {args.cases}", file=sys.stderr)
        return 2
    card = score(cases, CommandDecider(shlex.split(args.decider), args.timeout))
    print("\n".join(card.describe()))
    return 0 if not card.reaching_a_stranger and not card.unusable else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
