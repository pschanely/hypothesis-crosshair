"""Command line entry point for stages 1-3."""

import argparse
import json
import os
import shlex
import sys
import time
import uuid
from typing import Dict, List, Optional

from . import telemetry
from .model import Verdict
from .pipeline import Pipeline, PipelineConfig, PipelineReport
from .runner import EnvSpec, Runner
from .sandbox import DockerSandbox, Limits, LocalSandbox, Sandbox, docker_available
from .store import Store

_HEADLINE_ORDER = [
    Verdict.TROPHY_CANDIDATE,
    Verdict.CROSSHAIR_FALSE_POSITIVE,
    Verdict.SOUNDNESS_SUSPECT,
    Verdict.CROSSHAIR_FALSE_NEGATIVE,
    Verdict.CROSSHAIR_CRASH,
    Verdict.CROSSHAIR_TIMEOUT,
    Verdict.OBSERVER_EFFECT,
    Verdict.PENDING_VALIDATION,
    Verdict.SHARED_FIND,
    Verdict.QUARANTINED_NONDETERMINISTIC,
    Verdict.QUARANTINED_UNSTABLE,
    Verdict.NO_BASELINE_RESULT,
    Verdict.NO_SIGNAL,
]


def _build_sandbox(args: argparse.Namespace) -> Sandbox:
    if args.sandbox == "docker":
        if not docker_available():
            sys.exit(
                "docker is not usable here. Use --sandbox local only for code you "
                "already trust; it provides no isolation."
            )
        return DockerSandbox(image=args.image)
    return LocalSandbox(i_understand_this_is_unsafe=True)


def _format(report: PipelineReport) -> str:
    lines: List[str] = []
    lines.append(f"project:   {report.project_dir}")
    lines.append(f"collected: {len(report.collected)} hypothesis tests")
    lines.append(f"eligible:  {len(report.eligible)} passed the baseline gate")
    lines.append(f"duration:  {report.duration:.1f}s")
    lines.append("")
    grouped = {}
    for item in report.classifications:
        grouped.setdefault(item.verdict, []).append(item)
    for verdict in _HEADLINE_ORDER:
        items = grouped.get(verdict)
        if not items:
            continue
        lines.append(f"{verdict.value}  ({len(items)})")
        for item in items:
            lines.append(f"    {item.nodeid}")
            lines.append(f"        {item.rationale}")
            if item.falsifying_example:
                first = item.falsifying_example.replace("\n", " ")
                lines.append(f"        example: {first[:110]}")
        lines.append("")
    lines.extend(_telemetry_section(report))
    if any(c.verdict is Verdict.PENDING_VALIDATION for c in report.classifications):
        lines.append(
            "NOTE: pending_validation means the clean-room replay was inconclusive, "
            "NOT that the finding was refuted."
        )
    if report.trophies:
        lines.append(
            "NOTE: trophy candidates are drafts for human review. This tool never "
            "reports anything to a third-party project."
        )
    return "\n".join(lines)


def _telemetry_section(report: PipelineReport) -> List[str]:
    """Aggregate solver health across the run.

    The ignore-reason histogram is the CrossHair-facing output: it says where
    the solver spent a corpus's worth of budget without exploring user code.
    """
    stats = (report.telemetry_run.telemetry if report.telemetry_run else {}) or {}
    totals: Dict[str, int] = {}
    cases = 0
    productive = 0
    for entry in stats.values():
        cases += entry.crosshair_cases
        productive += entry.productive
        for text, count in entry.counts.items():
            totals[text] = totals.get(text, 0) + count
    if not cases:
        return []
    lines = ["solver health (tier B telemetry)"]
    lines.append(f"    {cases} solver iterations, {productive / cases:.0%} productive")
    for text, count in sorted(totals.items(), key=lambda kv: -kv[1]):
        lines.append(f"    {count:6d}  {count / cases:5.1%}  {text}")
    drift: Dict[str, int] = {}
    for entry in stats.values():
        for text, count in telemetry.api_drift_completions(entry).items():
            drift[text] = drift.get(text, 0) + count
    if drift:
        lines.append("    possible API drift (excludes assume() filtering):")
        for text, count in sorted(drift.items(), key=lambda kv: -kv[1]):
            lines.append(f"        {count:6d}  {text}")
    covered = sum(
        len(v) for entry in stats.values() for v in entry.covered_lines.values()
    )
    if covered:
        lines.append(f"    {covered} distinct lines covered across the run")
    lines.append("")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="discovery",
        description="Run a project's Hypothesis tests under CrossHair and classify the result.",
    )
    parser.add_argument(
        "--project", required=True, help="directory of the project under test"
    )
    parser.add_argument("--run-root", default=None, help="where to write run artifacts")
    parser.add_argument("--sandbox", choices=("docker", "local"), default="docker")
    parser.add_argument(
        "--image", default="python:3.12-slim", help="image for --sandbox docker"
    )
    parser.add_argument(
        "--crosshair-python",
        default="python",
        help="interpreter command where hypothesis-crosshair IS installed",
    )
    parser.add_argument(
        "--validation-python",
        default=None,
        help=(
            "interpreter command where hypothesis-crosshair is NOT installed. "
            "Without it, findings stay unvalidated rather than being claimed."
        ),
    )
    parser.add_argument("--baseline-seeds", default="1,2,3")
    parser.add_argument("--baseline-max-examples", type=int, default=200)
    parser.add_argument("--crosshair-max-examples", type=int, default=100)
    parser.add_argument("--crosshair-timeout", type=int, default=900)
    parser.add_argument("--no-telemetry-tier", action="store_true")
    parser.add_argument(
        "--pytest-arg",
        action="append",
        default=[],
        help="extra argument passed to every pytest run, e.g. -m property. Repeatable.",
    )
    parser.add_argument(
        "--store", default=None, help="sqlite path for durable verdicts"
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("nodeids", nargs="*", help="restrict to these node ids")
    args = parser.parse_args(argv)

    project = os.path.abspath(args.project)
    run_id = uuid.uuid4().hex[:12]
    run_root = os.path.abspath(
        args.run_root or os.path.join(project, ".discovery", run_id)
    )
    os.makedirs(run_root, exist_ok=True)

    runner = Runner(_build_sandbox(args), project_dir=project, run_root=run_root)
    crosshair_env = EnvSpec(
        label="crosshair",
        python_argv=shlex.split(args.crosshair_python),
        has_crosshair=True,
    )
    validation_env = (
        EnvSpec(
            label="validation",
            python_argv=shlex.split(args.validation_python),
            has_crosshair=False,
        )
        if args.validation_python
        else None
    )
    config = PipelineConfig(
        baseline_seeds=tuple(
            int(s) for s in args.baseline_seeds.split(",") if s.strip()
        ),
        baseline_max_examples=args.baseline_max_examples,
        crosshair_max_examples=args.crosshair_max_examples,
        crosshair_limits=Limits(wall_seconds=args.crosshair_timeout),
        run_telemetry_tier=not args.no_telemetry_tier,
        pytest_args=tuple(args.pytest_arg),
    )

    pipeline = Pipeline(
        runner,
        crosshair_env=crosshair_env,
        validation_env=validation_env,
        config=config,
    )
    report = pipeline.run(args.nodeids or None)

    if args.store:
        with Store(args.store) as store:
            store.record_run(run_id, project, "", time.time(), {"run_root": run_root})
            store.record_verdicts(run_id, report.classifications)

    if args.json:
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "project": project,
                    "collected": report.collected,
                    "eligible": report.eligible,
                    "observer_effect": report.observer_effect,
                    "classifications": [
                        {
                            "nodeid": c.nodeid,
                            "verdict": c.verdict.value,
                            "rationale": c.rationale,
                            "falsifying_example": c.falsifying_example,
                            "exception_type": c.exception_type,
                        }
                        for c in report.classifications
                    ],
                },
                indent=2,
            )
        )
    else:
        print(_format(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
