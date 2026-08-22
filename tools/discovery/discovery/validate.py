"""Clean-room replay of a CrossHair finding.

Runs the reported falsifying example directly against the test's undecorated
body, in an environment where ``hypothesis-crosshair`` is absent. Calling the
example explicitly — rather than replaying a choice sequence from Hypothesis's
example database — keeps the check independent of Hypothesis internals and
makes it self-evidencing: either the example ran, or the result is inconclusive.
"""

import json
import os
from dataclasses import dataclass
from typing import Dict, Optional

from .model import Outcome
from .runner import EnvSpec
from .sandbox import Limits, Sandbox

_REPRO_TEMPLATE = '''"""Standalone reproduction. Runs without CrossHair installed."""

import importlib
import json
import sys
import traceback

sys.path.insert(0, {project!r})

RESULTS = {{}}
CASES = {cases!r}

for nodeid, module_name, attr_path, call_args in CASES:
    entry = {{"nodeid": nodeid}}
    try:
        target = importlib.import_module(module_name)
        for part in attr_path:
            target = getattr(target, part)
        inner = getattr(getattr(target, "hypothesis", None), "inner_test", target)
    except Exception as exc:
        entry["status"] = "inconclusive"
        entry["detail"] = "could not resolve test: {{!r}}".format(exc)
        RESULTS[nodeid] = entry
        continue
    namespace = {{"inner": inner}}
    try:
        exec("inner(" + call_args + ")", vars(sys.modules[module_name]), namespace)
    except TypeError as exc:
        # A signature mismatch means the example was never actually applied.
        if "argument" in str(exc):
            entry["status"] = "inconclusive"
            entry["detail"] = "could not apply example: {{!r}}".format(exc)
        else:
            entry["status"] = "reproduced"
            entry["exception_type"] = type(exc).__name__
            entry["detail"] = traceback.format_exc()[-2000:]
    except BaseException as exc:
        entry["status"] = "reproduced"
        entry["exception_type"] = type(exc).__name__
        entry["detail"] = traceback.format_exc()[-2000:]
    else:
        entry["status"] = "clean"
    RESULTS[nodeid] = entry

with open({out!r}, "w") as handle:
    json.dump(RESULTS, handle)
'''


@dataclass
class ValidationResult:
    nodeid: str
    outcome: Outcome
    status: str
    detail: str = ""
    exception_type: Optional[str] = None

    @property
    def conclusive(self) -> bool:
        return self.status in ("reproduced", "clean")


def split_example(example: str) -> Optional[str]:
    """Extract the argument source from a reported falsifying example."""
    if not example:
        return None
    start = example.find("(")
    end = example.rfind(")")
    if start == -1 or end == -1 or end <= start:
        return None
    return example[start + 1 : end].strip()


def nodeid_to_target(nodeid: str) -> Optional[tuple]:
    """Split a pytest node id into an importable module and attribute path."""
    parts = nodeid.split("::")
    path = parts[0]
    if not path.endswith(".py"):
        return None
    module = os.path.splitext(path)[0].replace(os.sep, ".").replace("/", ".")
    attrs = [p.split("[")[0] for p in parts[1:]]
    if not attrs:
        return None
    return module, attrs


def build_cases(examples: Dict[str, str]) -> list:
    cases = []
    for nodeid, example in examples.items():
        target = nodeid_to_target(nodeid)
        args = split_example(example or "")
        if target is None or args is None:
            continue
        cases.append((nodeid, target[0], target[1], args))
    return cases


class Validator:
    def __init__(self, sandbox: Sandbox, project_dir: str, run_root: str) -> None:
        self.sandbox = sandbox
        self.project_dir = project_dir
        self.run_root = os.path.join(run_root, "validation")
        os.makedirs(self.run_root, exist_ok=True)

    def validate(
        self,
        examples: Dict[str, str],
        env: EnvSpec,
        *,
        limits: Optional[Limits] = None,
    ) -> Dict[str, ValidationResult]:
        if env.has_crosshair:
            raise ValueError("validation requires an environment without the plugin")
        cases = build_cases(examples)
        results = {
            nodeid: ValidationResult(
                nodeid,
                Outcome.NOT_RUN,
                "inconclusive",
                "no usable falsifying example was reported",
            )
            for nodeid in examples
        }
        if not cases:
            return results

        out_path = os.path.join(self.run_root, "repro_results.json")
        script_path = os.path.join(self.run_root, "repro.py")
        with open(script_path, "w") as handle:
            handle.write(
                _REPRO_TEMPLATE.format(
                    project=self.project_dir, cases=cases, out=out_path
                )
            )
        self.sandbox.run(
            [*env.python_argv, script_path],
            cwd=self.project_dir,
            env={"PYTHONDONTWRITEBYTECODE": "1"},
            network=False,
            limits=limits or Limits(wall_seconds=300),
        )
        if not os.path.exists(out_path):
            return results
        with open(out_path) as handle:
            payload = json.load(handle)
        for nodeid, entry in payload.items():
            status = entry.get("status", "inconclusive")
            results[nodeid] = ValidationResult(
                nodeid=nodeid,
                outcome={
                    "reproduced": Outcome.FAILED,
                    "clean": Outcome.PASSED,
                }.get(status, Outcome.NOT_RUN),
                status=status,
                detail=entry.get("detail", ""),
                exception_type=entry.get("exception_type"),
            )
        return results

    def repro_script_path(self) -> str:
        return os.path.join(self.run_root, "repro.py")
