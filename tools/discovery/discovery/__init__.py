"""Deterministic pipeline for running third-party Hypothesis tests under CrossHair."""

from .model import Arm, Classification, Outcome, Tier, Verdict
from .pipeline import Pipeline, PipelineConfig, PipelineReport
from .runner import EnvSpec, Runner, RunSpec
from .sandbox import DockerSandbox, Limits, LocalSandbox, docker_available
from .store import Store, cache_key

__all__ = [
    "Arm",
    "Classification",
    "DockerSandbox",
    "EnvSpec",
    "Limits",
    "LocalSandbox",
    "Outcome",
    "Pipeline",
    "PipelineConfig",
    "PipelineReport",
    "RunSpec",
    "Runner",
    "Store",
    "Tier",
    "Verdict",
    "cache_key",
    "docker_available",
]
