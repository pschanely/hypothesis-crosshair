"""Entry point for the canary: run known faults through the whole pipeline."""

import argparse
import os
import shlex
import sys
import uuid
from typing import List, Optional

from . import faults as fault_defs
from .canary import CanaryResult, run_fault, summarise
from .pipeline import Pipeline, PipelineConfig
from .runner import EnvSpec, Runner
from .sandbox import DockerSandbox, Limits, LocalSandbox, Sandbox, docker_available


def _build_sandbox(args: argparse.Namespace) -> Sandbox:
    if args.sandbox == "docker":
        if not docker_available():
            sys.exit(
                "docker is not usable here. Use --sandbox local only for code you "
                "already trust; it provides no isolation."
            )
        return DockerSandbox(image=args.image)
    return LocalSandbox(i_understand_this_is_unsafe=True)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discovery-canary",
        description="Inject known faults and check the pipeline's verdicts.",
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--crosshair-python", required=True)
    parser.add_argument(
        "--validation-python",
        required=True,
        help=(
            "interpreter without hypothesis-crosshair. Required: without a "
            "clean room a detection cannot be confirmed, so every fault would "
            "come back unconfirmed and the canary would prove nothing."
        ),
    )
    parser.add_argument("--run-root", default=None)
    parser.add_argument("--sandbox", choices=("docker", "local"), default="docker")
    parser.add_argument("--image", default="python:3.12-slim")
    parser.add_argument("--baseline-seeds", default="1,2,3")
    parser.add_argument("--baseline-max-examples", type=int, default=200)
    parser.add_argument("--crosshair-max-examples", type=int, default=100)
    parser.add_argument("--crosshair-timeout", type=int, default=900)
    parser.add_argument("--pytest-arg", action="append", default=[])
    parser.add_argument("--no-telemetry-tier", action="store_true")
    parser.add_argument(
        "--fault", action="append", default=[], help="run only these faults, by name"
    )
    args = parser.parse_args(argv)

    project = os.path.abspath(args.project)
    run_root = os.path.abspath(
        args.run_root or os.path.join(project, ".discovery", uuid.uuid4().hex[:12])
    )
    os.makedirs(run_root, exist_ok=True)

    chosen = [f for f in fault_defs.ALL if not args.fault or f.name in args.fault]
    if not chosen:
        print(f"no faults matched {args.fault}", file=sys.stderr)
        return 2

    config = PipelineConfig(
        baseline_seeds=tuple(
            int(s) for s in args.baseline_seeds.split(",") if s.strip()
        ),
        baseline_max_examples=args.baseline_max_examples,
        crosshair_max_examples=args.crosshair_max_examples,
        crosshair_limits=Limits(wall_seconds=args.crosshair_timeout),
        pytest_args=tuple(args.pytest_arg),
        run_telemetry_tier=not args.no_telemetry_tier,
    )

    results: List[CanaryResult] = []
    for fault in chosen:
        runner = Runner(
            _build_sandbox(args),
            project_dir=project,
            run_root=os.path.join(run_root, fault.name.replace("/", "_")),
        )
        pipeline = Pipeline(
            runner,
            crosshair_env=EnvSpec(
                "crosshair", shlex.split(args.crosshair_python), has_crosshair=True
            ),
            validation_env=EnvSpec(
                "validation", shlex.split(args.validation_python), has_crosshair=False
            ),
            config=config,
        )
        results.append(run_fault(pipeline, project, fault))
        print(f"  ran {fault.name}: {results[-1].detail}", flush=True)

    print()
    print("\n".join(summarise(results)))
    return 0 if all(entry.passed for entry in results) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
