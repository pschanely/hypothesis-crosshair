"""Stages 1-3: collect, gate on the baseline, run both tiers, classify.

Deterministic end to end. No model is involved in any decision made here.
"""

import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from . import classify as classify_mod
from . import telemetry
from .model import Arm, Classification, Outcome, RunResult, Tier, Verdict
from .runner import EnvSpec, Runner, RunSpec
from .sandbox import Limits
from .validate import ValidationResult, Validator


@dataclass
class PipelineConfig:
    baseline_seeds: Sequence[int] = (1, 2, 3)
    baseline_max_examples: int = 200
    crosshair_max_examples: int = 100
    validation_max_examples: int = 200
    baseline_limits: Limits = field(default_factory=lambda: Limits(wall_seconds=600))
    crosshair_limits: Limits = field(default_factory=lambda: Limits(wall_seconds=900))
    run_telemetry_tier: bool = True
    pytest_args: Sequence[str] = ()


@dataclass
class PipelineReport:
    project_dir: str
    collected: List[str] = field(default_factory=list)
    eligible: List[str] = field(default_factory=list)
    classifications: List[Classification] = field(default_factory=list)
    observer_effect: List[str] = field(default_factory=list)
    baseline_runs: List[RunResult] = field(default_factory=list)
    crosshair_run: Optional[RunResult] = None
    telemetry_run: Optional[RunResult] = None
    validations: Dict[str, "ValidationResult"] = field(default_factory=dict)
    clean_room: str = ""
    duration: float = 0.0

    def by_verdict(self, verdict: Verdict) -> List[Classification]:
        return [c for c in self.classifications if c.verdict is verdict]

    @property
    def trophies(self) -> List[Classification]:
        return self.by_verdict(Verdict.TROPHY_CANDIDATE)

    @property
    def crosshair_defects(self) -> List[Classification]:
        return [c for c in self.classifications if c.is_crosshair_defect]


class Pipeline:
    def __init__(
        self,
        runner: Runner,
        *,
        crosshair_env: EnvSpec,
        validation_env: Optional[EnvSpec] = None,
        config: Optional[PipelineConfig] = None,
    ) -> None:
        self.runner = runner
        self.crosshair_env = crosshair_env
        self.validation_env = validation_env
        self.config = config or PipelineConfig()
        self.validator = Validator(runner.sandbox, runner.project_dir, runner.run_root)
        if validation_env is not None and validation_env.has_crosshair:
            raise ValueError(
                "the validation environment must not have hypothesis-crosshair "
                "installed; selecting a different backend is not a clean room"
            )

    def run(self, nodeids: Optional[Sequence[str]] = None) -> PipelineReport:
        started = time.monotonic()
        report = PipelineReport(project_dir=self.runner.project_dir)

        if self.validation_env is not None:
            check = self.validator.preflight(self.validation_env)
            report.clean_room = check.detail
            if not check.clean:
                raise RuntimeError(
                    "the validation environment is not a clean room: " + check.detail
                )

        inventory = self.runner.collect(
            self.crosshair_env, extra_args=self.config.pytest_args
        )
        report.collected = select(inventory, nodeids) if nodeids else inventory
        if not report.collected:
            report.duration = time.monotonic() - started
            return report

        report.baseline_runs = self._baseline(report.collected)
        gate = classify_mod.baseline_gate(report.baseline_runs, report.collected)
        testable = [
            nodeid
            for nodeid, verdict in gate.items()
            if verdict.stability is not classify_mod.Stability.UNSTABLE
        ]
        report.eligible = [n for n in testable if gate[n].eligible]

        unstable = [
            classify_mod.classify(
                nodeid,
                baseline=gate[nodeid],
                crosshair_run=RunResult(Arm.CROSSHAIR, Tier.A_VERDICT, 0, 0.0),
            )
            for nodeid, verdict in gate.items()
            if verdict.stability is classify_mod.Stability.UNSTABLE
        ]

        if not testable:
            report.classifications = unstable
            report.duration = time.monotonic() - started
            return report

        report.crosshair_run = self._crosshair(testable, Tier.A_VERDICT)
        if self.config.run_telemetry_tier:
            report.telemetry_run = self._crosshair(testable, Tier.B_TELEMETRY)
            report.observer_effect = classify_mod.detect_observer_effect(
                report.crosshair_run, report.telemetry_run, testable
            )

        pending = [
            nodeid
            for nodeid in testable
            if classify_mod.needs_validation(
                Outcome.PASSED if gate[nodeid].eligible else Outcome.FAILED,
                report.crosshair_run.outcome_of(nodeid),
            )
        ]
        validations: Dict[str, Outcome] = {}
        if pending and self.validation_env is not None:
            examples = {
                nodeid: (report.crosshair_run.outcomes[nodeid].falsifying_example or "")
                for nodeid in pending
                if nodeid in report.crosshair_run.outcomes
            }
            report.validations = self.validator.validate(
                examples, self.validation_env, limits=self.config.baseline_limits
            )
            validations = {
                nodeid: result.outcome
                for nodeid, result in report.validations.items()
                if result.conclusive
            }

        stats_by_name = report.telemetry_run.telemetry if report.telemetry_run else {}
        results = list(unstable)
        for nodeid in testable:
            results.append(
                classify_mod.classify(
                    nodeid,
                    baseline=gate[nodeid],
                    crosshair_run=report.crosshair_run,
                    validation=validations.get(nodeid),
                    stats=_stats_for(stats_by_name, nodeid),
                )
            )
        for nodeid in report.observer_effect:
            results.append(
                Classification(
                    nodeid=nodeid,
                    verdict=Verdict.OBSERVER_EFFECT,
                    baseline=gate[nodeid].outcomes[0],
                    crosshair=report.crosshair_run.outcome_of(nodeid),
                    rationale=(
                        "outcome differs between the verdict tier and the "
                        "observability tier at the same seed"
                    ),
                )
            )
        report.classifications = results
        report.duration = time.monotonic() - started
        return report

    def _baseline(self, nodeids: Sequence[str]) -> List[RunResult]:
        return [
            self.runner.run(
                RunSpec(
                    arm=Arm.BASELINE,
                    tier=Tier.A_VERDICT,
                    env=self.crosshair_env,
                    nodeids=nodeids,
                    backend="hypothesis",
                    max_examples=self.config.baseline_max_examples,
                    seed=seed,
                    extra_args=self.config.pytest_args,
                    limits=self.config.baseline_limits,
                )
            )
            for seed in self.config.baseline_seeds
        ]

    def _crosshair(self, nodeids: Sequence[str], tier: Tier) -> RunResult:
        """Run the solver arm.

        Both tiers use the same seed so that an outcome disagreement between
        them is attributable to observability rather than to a different search.
        """
        return self.runner.run(
            RunSpec(
                arm=Arm.CROSSHAIR,
                tier=tier,
                env=self.crosshair_env,
                nodeids=nodeids,
                backend="crosshair",
                max_examples=self.config.crosshair_max_examples,
                extra_args=self.config.pytest_args,
                seed=self.config.baseline_seeds[0],
                limits=self.config.crosshair_limits,
            )
        )


def select(inventory: Sequence[str], selectors: Sequence[str]) -> List[str]:
    """Resolve user-supplied selectors against collected node ids.

    A selector may be a full node id or any path prefix of one, so that a file
    or directory selects every Hypothesis test beneath it.
    """
    chosen = []
    for nodeid in inventory:
        for selector in selectors:
            if (
                nodeid == selector
                or nodeid.startswith(selector.rstrip("/") + "/")
                or (nodeid.startswith(selector) and selector.endswith(".py"))
            ):
                chosen.append(nodeid)
                break
    return chosen


def _stats_for(stats_by_name: Dict[str, object], nodeid: str):
    """Match observability's property name against a pytest node id."""
    name = nodeid.split("::")[-1].split("[")[0]
    direct = stats_by_name.get(nodeid) or stats_by_name.get(name)
    if direct is not None:
        return direct
    for key, value in stats_by_name.items():
        if key.split("::")[-1].split("[")[0] == name:
            return value
    return None
