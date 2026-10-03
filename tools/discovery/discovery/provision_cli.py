"""Entry point for building environments the pipeline can then run in."""

import argparse
import json
import os
import sys
from typing import List, Optional

from .provision import provision
from .sandbox import DockerSandbox, LocalSandbox, Sandbox


def _sandbox(args) -> Sandbox:
    if args.sandbox == "docker":
        return DockerSandbox(image=args.image)
    return LocalSandbox(i_understand_this_is_unsafe=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discovery-provision",
        description=(
            "Build a virtual environment per project, repairing the harness "
            "where the failure is one that has been seen before."
        ),
    )
    parser.add_argument("projects", nargs="+", help="project checkouts")
    parser.add_argument(
        "--plugin", default=os.getcwd(), help="checkout of hypothesis-crosshair"
    )
    parser.add_argument("--sandbox", choices=("docker", "local"), default="docker")
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--python-version", default="3.12")
    parser.add_argument("--venv-name", default=".venv-ch")
    parser.add_argument(
        "--package", action="append", default=[], help="extra package to install"
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    sandbox = _sandbox(args)
    results = []
    for path in args.projects:
        project = os.path.abspath(path)
        if not os.path.isdir(project):
            print(f"not a directory: {path}", file=sys.stderr)
            return 2
        done = provision(
            sandbox,
            project,
            os.path.abspath(args.plugin),
            venv_dir=os.path.join(project, args.venv_name),
            python_version=args.python_version,
            extra_packages=args.package,
        )
        results.append(done)
        if not args.json:
            print(done.describe(), flush=True)
            for repair in done.repairs:
                print(f"    repaired: {repair}", flush=True)

    ready = [r for r in results if r.ready]
    if args.json:
        print(
            json.dumps(
                [
                    {
                        "project": r.project,
                        "ready": r.ready,
                        "python": r.python,
                        "pytest_args": r.pytest_args,
                        "env": r.env,
                        "repairs": r.repairs,
                        "collected": r.collected,
                        "error": r.error,
                    }
                    for r in results
                ],
                indent=1,
            )
        )
    else:
        print(f"\n{len(ready)}/{len(results)} provisioned")
    return 0 if len(ready) == len(results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
