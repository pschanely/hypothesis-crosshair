"""Command line entry point for stages 1-3."""

import argparse
import json
import os
import shlex
import sys
import time
import uuid
from dataclasses import replace
from typing import Dict, List, Optional

from . import telemetry
from .model import RunResult, SearchProgress, Verdict
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
            attempts = f" [{item.attempts} attempts]" if item.attempts > 1 else ""
            lines.append(f"        {item.rationale}{attempts}")
            if item.falsifying_example:
                first = item.falsifying_example.replace("\n", " ")
                lines.append(f"        example: {first[:110]}")
        lines.append("")
    lines.extend(_telemetry_section(report))
    if report.crosshair_run is not None:
        lines.extend(_search_section(report.crosshair_run.search))
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
    realizing = sum(e.realizing_cases for e in stats.values())
    degraded = [n for n, e in stats.items() if telemetry.search_is_degraded(e)]
    lines = ["solver health (tier B telemetry)"]
    lines.append(f"    {cases} solver iterations, {productive / cases:.0%} productive")
    lines.append(
        f"    {realizing} ({realizing / cases:.0%}) realized a symbolic value; "
        f"{len(degraded)} of {len(stats)} tests searched mostly concretely"
    )
    if degraded:
        lines.append(
            "    WARNING: where search is degraded, 'no failure found' is not "
            "evidence the solver explored the test."
        )
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
    unsupported = telemetry.unsupported_constructs(stats)
    if unsupported:
        fallbacks = sum(unsupported.values())
        lines.append(
            f"    {fallbacks} iterations fell back to concrete matching on a "
            "construct CrossHair does not handle:"
        )
        for reason, count in sorted(unsupported.items(), key=lambda kv: -kv[1]):
            lines.append(f"        {count:6d}  {reason[:90]}")
        lines.append(
            "    a fallback still reports 'completed normally', so these "
            "iterations are concrete random search wearing a solver's name."
        )
    sites = telemetry.realization_sites(stats)
    if sites:
        lines.append("    realization forced at:")
        for site, count in sorted(sites.items(), key=lambda kv: -kv[1])[:5]:
            lines.append(f"        {count:6d}  {site[:90]}")
    covered = sum(
        len(v) for entry in stats.values() for v in entry.covered_lines.values()
    )
    if covered:
        lines.append(
            f"    {covered} lines covered in concrete phases only "
            "(no coverage is recorded under the crosshair backend)"
        )
    lines.append("")
    return lines


def _search_section(progress: Dict[str, SearchProgress]) -> List[str]:
    """Report CrossHair's own path-search reach, which needs no observability."""
    if not progress:
        return []
    ran = {k: v for k, v in progress.items() if v.solver_iterations}
    if not ran:
        return []
    total_locs = sum(v.code_locations for v in ran.values())
    stalled = telemetry.stalled_searches(ran)
    lines = ["  solver path search (from CrossHair's pathing oracle):"]
    lines.append(
        f"    {total_locs} code locations forked across {len(ran)} tests; "
        f"{len(stalled)} stalled "
        f"(no new location in {telemetry.STALL_THRESHOLD}+ iterations)"
    )
    worst = sorted(ran.items(), key=lambda kv: kv[1].code_locations)[:5]
    for nodeid, entry in worst:
        mark = "STALLED" if nodeid in stalled else "       "
        lines.append(
            f"    {mark} {entry.code_locations:5d} locs "
            f"{entry.solver_iterations:5d} iters  {nodeid}"
        )
    if stalled:
        lines.append(
            "    a stalled search is spending budget without extending reach; "
            "reach is not discrimination, so this is a budget signal only."
        )
    lines.append("")
    return lines


def _merge_run(
    into: Optional[RunResult], one: Optional[RunResult]
) -> Optional[RunResult]:
    """Accumulate one arm's results across per-test invocations.

    Without this the merged report carries no run at all, and every section
    keyed off one -- the completion histogram, the fallback report, the path
    search -- silently reports nothing.
    """
    if one is None:
        return into
    if into is None:
        return replace(
            one,
            outcomes=dict(one.outcomes),
            telemetry=dict(one.telemetry),
            search=dict(one.search),
        )
    into.outcomes.update(one.outcomes)
    into.telemetry.update(one.telemetry)
    into.search.update(one.search)
    into.duration += one.duration
    into.timed_out = into.timed_out or one.timed_out
    into.crashed = into.crashed or one.crashed
    return into


def _run_per_test(build, run_root: str, nodeids: List[str]) -> PipelineReport:
    """Run each test in its own invocation and merge the reports.

    The budgets in ``PipelineConfig`` apply to one pytest invocation, so a
    selection of N tests shares a single wall-clock allowance and a single
    ``max_examples``. Past a handful of tests that guarantees the solver arm is
    killed mid-run, which the classifier can only report as a timeout for every
    test in the batch.
    """
    merged = PipelineReport(project_dir="")
    for index, nodeid in enumerate(nodeids):
        slot = os.path.join(run_root, f"t{index:03d}")
        one = build(slot).run([nodeid])
        merged.project_dir = one.project_dir
        merged.collected.extend(one.collected)
        merged.eligible.extend(one.eligible)
        merged.classifications.extend(one.classifications)
        merged.observer_effect.extend(one.observer_effect)
        merged.validations.update(one.validations)
        merged.clean_room = one.clean_room or merged.clean_room
        merged.duration += one.duration
        merged.crosshair_run = _merge_run(merged.crosshair_run, one.crosshair_run)
        merged.telemetry_run = _merge_run(merged.telemetry_run, one.telemetry_run)
        print(
            f"  [{index + 1}/{len(nodeids)}] {nodeid} -> "
            + ", ".join(sorted({c.verdict.value for c in one.classifications})),
            flush=True,
        )
    return merged


def _retry_no_signal(build, run_root: str, report: PipelineReport, extra: int) -> None:
    """Give every no_signal test more attempts, in place.

    Only `no_signal` is worth retrying: it is the one verdict that means "the
    solver ran and reported nothing", which non-determinism makes ambiguous. A
    trophy, a crash or a timeout already says what happened.
    """
    pending = [c for c in report.classifications if c.verdict is Verdict.NO_SIGNAL]
    for index, entry in enumerate(pending):
        for attempt in range(extra):
            slot = os.path.join(run_root, "retry", f"n{index:03d}-a{attempt:02d}")
            again = build(slot).run([entry.nodeid])
            found = next(
                (
                    c
                    for c in again.classifications
                    if c.nodeid == entry.nodeid and c.verdict is not Verdict.NO_SIGNAL
                ),
                None,
            )
            entry.attempts += 1
            if found is not None:
                found.attempts = entry.attempts
                report.classifications[report.classifications.index(entry)] = found
                print(
                    f"  retry {entry.nodeid} -> {found.verdict.value} "
                    f"on attempt {entry.attempts}",
                    flush=True,
                )
                break


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
        "--retry-no-signal",
        type=int,
        default=0,
        help=(
            "extra attempts for tests that come back no_signal. The search is "
            "not reproducible, so a single no_signal says nothing about "
            "whether the solver can reach the test. Retrying only those is "
            "far cheaper than repeating the whole selection."
        ),
    )
    parser.add_argument(
        "--per-test",
        action="store_true",
        help=(
            "give every test its own pipeline invocation, so the budget is per "
            "test rather than shared across the whole selection."
        ),
    )
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

    def build(root: str) -> Pipeline:
        return Pipeline(
            Runner(_build_sandbox(args), project_dir=project, run_root=root),
            crosshair_env=crosshair_env,
            validation_env=validation_env,
            config=config,
        )

    if args.per_test:
        targets = list(args.nodeids) or build(run_root).runner.collect(
            crosshair_env, extra_args=config.pytest_args
        )
        report = _run_per_test(build, run_root, targets)
    else:
        report = build(run_root).run(args.nodeids or None)
    if args.retry_no_signal > 0:
        _retry_no_signal(build, run_root, report, args.retry_no_signal)

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
