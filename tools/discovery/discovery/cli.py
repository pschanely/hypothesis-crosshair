"""Command line entry point for stages 1-3."""

import argparse
import json
import os
import shlex
import sys
import time
import uuid
from dataclasses import asdict, replace
from typing import Dict, List, Optional

from . import cluster as cluster_mod
from . import harness
from . import outcomes as outcomes_mod
from . import provenance
from . import store as store_mod
from . import telemetry
from . import triage as triage_mod
from .model import Classification, RunResult, SearchProgress, Verdict
from .pipeline import Pipeline, PipelineConfig, PipelineReport, stats_for
from .runner import CollectionFailed, EnvSpec, Runner
from .sandbox import DockerSandbox, Limits, LocalSandbox, Sandbox, docker_available
from .store import Store, cache_key, classification_from_payload

_HEADLINE_ORDER = [
    Verdict.TROPHY_CANDIDATE,
    Verdict.CROSSHAIR_FALSE_POSITIVE,
    Verdict.CROSSHAIR_FALSE_NEGATIVE,
    Verdict.CROSSHAIR_CRASH,
    Verdict.CROSSHAIR_TIMEOUT,
    Verdict.OBSERVER_EFFECT,
    Verdict.PENDING_VALIDATION,
    Verdict.SHARED_FIND,
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
    lines.extend(_clue_section(report))
    lines.extend(_cluster_section(report))
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


def _clue_section(report: PipelineReport) -> List[str]:
    """Leads from the observability tier, each with the run that would settle it.

    These are not verdicts and cannot become one here: the tier that produced
    them realizes symbolic draws, so it is not the run being judged.
    """
    if report.telemetry_run is None:
        return []
    # Observability names a test by its Hypothesis property; everything else
    # here names it by its pytest node id.
    by_nodeid = {}
    verdicts = {}
    for item in report.classifications:
        # A node id can carry an observer_effect entry alongside its real
        # verdict, and that entry says nothing about what the solver found.
        if item.verdict is not Verdict.OBSERVER_EFFECT or item.nodeid not in verdicts:
            verdicts[item.nodeid] = item.verdict.value
        stats = stats_for(report.telemetry_run.telemetry, item.nodeid)
        if stats is not None:
            by_nodeid[item.nodeid] = stats
    found = telemetry.clues_from(by_nodeid, verdicts)
    if not found:
        return []
    lines = [f"clues from the observability tier  ({len(found)}, never verdicts)"]
    for clue in found:
        lines.append(f"    {clue.nodeid}")
        lines.append(f"        {clue.observation}")
        lines.append(f"        -> {clue.follow_up}")
    lines.append("")
    return lines


#: Node ids listed under a cluster before the rest are summarized.
_CLUSTER_SAMPLE = 5


def _clusters_of(report: PipelineReport) -> List[cluster_mod.Cluster]:
    details = report.crosshair_run.outcomes if report.crosshair_run else {}
    return cluster_mod.cluster(report.classifications, details, report.project_dir)


def _cluster_section(report: PipelineReport) -> List[str]:
    """Failures grouped by defect rather than by test.

    One defect reaches many of a project's property tests, so the verdict list
    above counts tests while this counts bugs.
    """
    groups = _clusters_of(report)
    if not groups:
        return []
    lines = [f"failure clusters  ({len(groups)})"]
    for group in groups:
        lines.append(f"    [{group.size}] {group.signature.describe()}")
        for nodeid in group.nodeids[:_CLUSTER_SAMPLE]:
            lines.append(f"        {nodeid}")
        if group.size > _CLUSTER_SAMPLE:
            lines.append(f"        ... and {group.size - _CLUSTER_SAMPLE} more")
    lines.append("")
    return lines


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


class _Selection:
    """Hands out a selection's tests once each, recording nothing."""

    def __init__(self, nodeids: List[str]) -> None:
        self._left = list(nodeids)
        self.total = len(self._left)

    def next(self) -> Optional[str]:
        return self._left.pop(0) if self._left else None

    def completed(self) -> List[Classification]:
        return []

    def reuse(self, nodeid: str) -> Optional[Classification]:
        return None

    def record(self, nodeid: str, items: List[Classification]) -> None:
        pass


class _Journal:
    """Hands out a selection's tests from the store, recording each verdict.

    Work is claimed before it runs and retired after, so a run killed partway
    through resumes at the test it was on rather than at the beginning, and a
    test that reliably destroys its worker is abandoned instead of retried
    forever.
    """

    def __init__(
        self,
        store: Store,
        run_id: str,
        project: str,
        commit: str,
        versions: Dict[str, str],
        refresh: bool = False,
    ) -> None:
        self.store = store
        self.run_id = run_id
        self.project = project
        self.commit = commit
        self.versions = versions
        self.refresh = refresh
        self.total = 0

    def key(self, nodeid: str) -> str:
        return cache_key(
            commit_sha=self.commit,
            nodeid=nodeid,
            crosshair_version=self.versions.get("crosshair", provenance.UNKNOWN),
            plugin_version=self.versions.get("plugin", provenance.UNKNOWN),
            python_version=self.versions.get("python", provenance.UNKNOWN),
        )

    def prepare(self, nodeids: List[str]) -> None:
        self.total = len(nodeids)
        self.store.enqueue(
            self.run_id,
            store_mod.RUN_TEST,
            [(self.key(n), {"nodeid": n, "project": self.project}) for n in nodeids],
        )

    def next(self) -> Optional[str]:
        item = self.store.claim(
            self.run_id, store_mod.RUN_TEST, time.time(), lease_seconds=0.0
        )
        return item["payload"]["nodeid"] if item else None

    def completed(self) -> List[Classification]:
        """Verdicts this run already holds, from the process that recorded them."""
        return [
            classification_from_payload(payload)
            for payload in self.store.verdicts(self.run_id)
        ]

    def reuse(self, nodeid: str) -> Optional[Classification]:
        if self.refresh:
            return None
        payload = self.store.cached(self.key(nodeid))
        if payload is None:
            return None
        found = classification_from_payload(payload)
        self.store.complete(self.key(nodeid), self.run_id, found)
        return found

    def record(self, nodeid: str, items: List[Classification]) -> None:
        for item in items:
            if item.nodeid == nodeid:
                self.store.complete(self.key(nodeid), self.run_id, item)
                return
        self.store.abandon(
            self.run_id,
            store_mod.RUN_TEST,
            self.key(nodeid),
            "the run produced no verdict for it",
        )


def _run_per_test(
    build, run_root: str, nodeids: List[str], journal=None
) -> PipelineReport:
    """Run each test in its own invocation and merge the reports.

    The budgets in ``PipelineConfig`` apply to one pytest invocation, so a
    selection of N tests shares a single wall-clock allowance and a single
    ``max_examples``. Past a handful of tests that guarantees the solver arm is
    killed mid-run, which the classifier can only report as a timeout for every
    test in the batch.

    With a ``journal``, the order and the stopping point come from the store,
    and a test whose verdict is already cached is reported without running.
    """
    if journal is None:
        journal = _Selection(nodeids)
    else:
        journal.prepare(nodeids)
    merged = PipelineReport(project_dir="")
    for known in journal.completed():
        merged.collected.append(known.nodeid)
        merged.eligible.append(known.nodeid)
        merged.classifications.append(known)
    index = len(merged.classifications)
    while True:
        nodeid = journal.next()
        if nodeid is None:
            break
        index += 1
        known = journal.reuse(nodeid)
        if known is not None:
            merged.collected.append(nodeid)
            merged.eligible.append(nodeid)
            merged.classifications.append(known)
            print(
                f"  [{index}/{journal.total}] {nodeid} -> "
                f"{known.verdict.value} (cached)",
                flush=True,
            )
            continue
        slot = os.path.join(run_root, f"t{index - 1:03d}")
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
        journal.record(nodeid, one.classifications)
        print(
            f"  [{index}/{journal.total}] {nodeid} -> "
            + ", ".join(sorted({c.verdict.value for c in one.classifications})),
            flush=True,
        )
    return merged


#: Verdicts that say less than the no_signal they would replace.
#:
#: A no_signal means the solver ran and reported nothing. A timeout means it
#: never finished, and a missing baseline that it never started, so accepting
#: one as the outcome of a retry trades an answer for a non-answer.
_LESS_INFORMATIVE_THAN_NO_SIGNAL = frozenset(
    {
        Verdict.CROSSHAIR_TIMEOUT,
        Verdict.NO_BASELINE_RESULT,
        Verdict.QUARANTINED_UNSTABLE,
    }
)


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
                    if c.nodeid == entry.nodeid
                    and c.verdict is not Verdict.NO_SIGNAL
                    and c.verdict not in _LESS_INFORMATIVE_THAN_NO_SIGNAL
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


def _triage(
    store: Store,
    run_id: str,
    report: PipelineReport,
    args,
    config: PipelineConfig,
    commit: str,
    versions: Dict[str, str],
) -> Optional[outcomes_mod.Routing]:
    """Hand each failure cluster to the configured decider, then route it."""
    groups = _clusters_of(report)
    if not groups:
        return None
    triage_mod.enqueue_clusters(store, run_id, groups, report.project_dir)
    outcome = triage_mod.run_triage(
        store,
        run_id,
        triage_mod.CommandDecider(shlex.split(args.triage_command)),
        time.time,
        budget=args.triage_budget,
    )
    print(f"triage  ({outcome.total} clusters)", file=sys.stderr)
    for key, verdict in outcome.decided.items():
        print(
            f"    {verdict.category.value} ({verdict.confidence:.2f}) {key}: "
            f"{verdict.reasoning[:100]}",
            file=sys.stderr,
        )
    for key, reason in outcome.rejected.items():
        print(f"    REJECTED {key}: {reason[:140]}", file=sys.stderr)
    return outcomes_mod.route(
        groups,
        outcome.decided,
        report.classifications,
        project=report.project_dir,
        commit=commit,
        crosshair_version=versions.get("crosshair", ""),
        baseline_examples=config.baseline_max_examples,
        baseline_seeds=len(config.baseline_seeds),
    )


def _routing_json(routing: Optional[outcomes_mod.Routing]) -> dict:
    if routing is None:
        return {}
    return {
        "trophies": [
            {
                **asdict(trophy.evidence),
                "reasoning": trophy.reasoning,
                "confidence": trophy.confidence,
                "found_on": trophy.found_on,
                "why_random_search_misses_it": trophy.why_random_search_misses_it,
                "human_review_required": True,
            }
            for trophy in routing.trophies
        ],
        "crosshair_defects": [
            {
                **asdict(defect.evidence),
                "reasoning": defect.reasoning,
                "confidence": defect.confidence,
            }
            for defect in routing.crosshair_defects
        ],
        "dismissed": [asdict(d.evidence) for d in routing.dismissed],
        "needs_human": [asdict(d.evidence) for d in routing.needs_human],
        "withheld": [asdict(d.evidence) for d in routing.withheld],
    }


def _routing_section(routing: outcomes_mod.Routing) -> List[str]:
    """Where each triaged cluster went, and why the withheld ones did not."""
    if routing.total == 0:
        return []
    lines = ["routing"]
    lines.append(f"    trophy drafts:      {len(routing.trophies)}")
    for trophy in routing.trophies:
        lines.append(
            f"        {trophy.evidence.frame or 'unknown frame'} "
            f"({', '.join(trophy.evidence.nodeids[:3])})"
        )
        lines.append(f"            {trophy.why_random_search_misses_it}")
    lines.append(f"    crosshair defects:  {len(routing.crosshair_defects)}")
    for defect in routing.crosshair_defects:
        lines.append(
            f"        {defect.evidence.frame or 'unknown frame'}: "
            f"{defect.reasoning[:90]}"
        )
    lines.append(f"    dismissed:          {len(routing.dismissed)}")
    lines.append(f"    needs human:        {len(routing.needs_human)}")
    if routing.withheld:
        lines.append(f"    withheld:           {len(routing.withheld)}")
        lines.append(
            "        triage called these project bugs, but no test in them is a "
            "trophy_candidate:"
        )
        for held in routing.withheld:
            lines.append(
                f"        {held.evidence.frame or 'unknown frame'} "
                f"[{', '.join(held.evidence.classifier_verdicts) or 'no verdict'}]"
            )
    if routing.trophies:
        lines.append(
            "    NOTE: trophy drafts are for human review. This tool never "
            "reports anything to a third-party project."
        )
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
    parser.add_argument(
        "--resume",
        default=None,
        metavar="RUN_ID",
        help=(
            "continue the run with this id instead of starting one, skipping "
            "the tests it already has verdicts for. Requires --store and "
            "--per-test."
        ),
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help=(
            "run every test even where a verdict is already cached for this "
            "commit and these versions."
        ),
    )
    parser.add_argument(
        "--triage-command",
        default=None,
        help=(
            "shell command that triages one failure cluster. It receives the "
            "cluster as JSON on stdin and must print a JSON object with "
            "category, confidence and reasoning. Requires --store."
        ),
    )
    parser.add_argument(
        "--triage-budget",
        type=int,
        default=triage_mod.DEFAULT_BUDGET,
        help="clusters to triage in this invocation",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    parser.add_argument("nodeids", nargs="*", help="restrict to these node ids")
    args = parser.parse_args(argv)

    if args.resume and not (args.store and args.per_test):
        parser.error("--resume needs --store and --per-test")
    if args.triage_command and not args.store:
        parser.error("--triage-command needs --store to record its answers")

    project = os.path.abspath(args.project)
    run_id = args.resume or uuid.uuid4().hex[:12]
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

    store = Store(args.store) if args.store else None
    commit = provenance.project_commit(project)
    versions = provenance.environment_versions(crosshair_env.python_argv)
    if store is not None:
        store.record_run(
            run_id, project, commit, time.time(), {**versions, "run_root": run_root}
        )

    try:
        if args.per_test:
            targets = list(args.nodeids) or build(run_root).runner.collect(
                crosshair_env, extra_args=config.pytest_args
            )
            journal = (
                _Journal(store, run_id, project, commit, versions, args.refresh)
                if store is not None
                else None
            )
            report = _run_per_test(build, run_root, targets, journal)
        else:
            report = build(run_root).run(args.nodeids or None)
        if args.retry_no_signal > 0:
            _retry_no_signal(build, run_root, report, args.retry_no_signal)
        routing = None
        if store is not None and args.triage_command:
            routing = _triage(store, run_id, report, args, config, commit, versions)
        if store is not None:
            store.record_verdicts(run_id, report.classifications)
            counts = {
                kind: store.progress(run_id, kind)
                for kind in (store_mod.RUN_TEST, store_mod.TRIAGE_CLUSTER)
            }
            summary = ", ".join(
                f"{kind} {state}={n}"
                for kind, states in counts.items()
                for state, n in sorted(states.items())
            )
            print(
                f"run {run_id}: {summary or 'nothing queued'}",
                file=sys.stderr,
                flush=True,
            )
    except CollectionFailed as exc:
        repair = harness.plan(str(exc))
        print(f"collection failed: {str(exc)[:400]}", file=sys.stderr)
        if repair is None:
            print(
                "no known repair for this failure; it needs a person",
                file=sys.stderr,
            )
            return 2
        print(f"\nthe harness needs a repair -- {repair.describe()}", file=sys.stderr)
        print(f"  because {repair.rationale}", file=sys.stderr)
        if repair.packages:
            print(
                f"  install into the project environment, then re-run: "
                f"{' '.join(repair.packages)}",
                file=sys.stderr,
            )
        if repair.pytest_args:
            print(
                f"  and pass --pytest-arg {' --pytest-arg '.join(repair.pytest_args)}",
                file=sys.stderr,
            )
        return 3
    finally:
        if store is not None:
            store.close()

    if args.json:
        print(
            json.dumps(
                {
                    "run_id": run_id,
                    "project": project,
                    "collected": report.collected,
                    "eligible": report.eligible,
                    "observer_effect": report.observer_effect,
                    "clusters": [
                        {
                            "exception_type": group.signature.exception_type,
                            "frame": group.signature.frame,
                            "message": group.signature.message,
                            "nodeids": group.nodeids,
                            "examples": group.examples,
                            "sample": group.sample,
                        }
                        for group in _clusters_of(report)
                    ],
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
                    "routing": _routing_json(routing),
                },
                indent=2,
            )
        )
    else:
        print(_format(report))
        if routing is not None:
            print("\n".join(_routing_section(routing)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
