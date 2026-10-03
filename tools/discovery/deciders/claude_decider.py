#!/usr/bin/env python3
"""Triage one failure cluster by asking Claude Code to read the project.

Reads a cluster as JSON on stdin and prints a triage answer as JSON, which is
the contract ``--triage-command`` expects. Run it with no arguments.

The model is given read-only access to the project under test, because this
judgment needs the source and not just the traceback. It is given no ability
to write or execute: the project is third-party code the pipeline treats as
untrusted, and nothing here needs to run it.
"""

import json
import os
import re
import subprocess
import sys

MODEL = os.environ.get("HCD_DECIDER_MODEL", "claude-sonnet-5-5")
TIMEOUT = float(os.environ.get("HCD_DECIDER_TIMEOUT", "240"))

#: Tools the model may use. Reading only -- see the module docstring.
ALLOWED_TOOLS = "Read,Grep,Glob"

PROMPT = """\
You are triaging one cluster of test failures for a bug-hunting pipeline.

The pipeline runs a third-party project's Hypothesis property tests twice:
once on Hypothesis's own random generator (the baseline), and once on
CrossHair, a symbolic-execution backend. This cluster is a failure, grouped
by where it happened and what it said.

Decide which of these it is, and answer with JSON only.

- "project_bug": the library under test is wrong. Its own property test is
  reasonable and the code does not satisfy it.
- "overstrong_property": the library is fine; the test asserts something
  stronger than the library promises, or depends on something outside its
  control (unseeded randomness, wall-clock time, dict or set ordering,
  platform specifics, floating-point exactness).
- "crosshair_artifact": the failure is caused by CrossHair or its Hypothesis
  plugin rather than by the project. Signs include an exception type from
  `crosshair.*`, a traceback through crosshair internals, a symbolic value
  leaking into code that cannot handle it, or behaviour that could not occur
  with ordinary concrete values.
- "unclear": you cannot tell from what you can see.

Judging a project's correct code to be a bug is the most costly error here,
because a person may then report it to that project. Answering "unclear" is
always safe and is the right answer whenever you are not reasonably sure.
Prefer it over a guess.

The project source is at: {project}
You may read it. Do not modify anything and do not run anything.

The cluster:

{cluster}

Reply with exactly one JSON object and no other text:
{{"category": "<one of the four>", "confidence": <0.0 to 1.0>,
 "reasoning": "<one or two sentences>", "evidence": ["<file:line you read>"]}}
"""

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)


def main() -> int:
    item = json.load(sys.stdin)
    project = item.get("project") or "."
    readable = {k: v for k, v in item.items() if k != "project"}
    prompt = PROMPT.format(
        project=project, cluster=json.dumps(readable, indent=1)[:12000]
    )

    argv = [
        "claude",
        "-p",
        prompt,
        "--model",
        MODEL,
        "--allowedTools",
        ALLOWED_TOOLS,
    ]
    if os.path.isdir(project):
        argv += ["--add-dir", project]

    done = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT)
    if done.returncode != 0:
        print(done.stderr.strip()[:500], file=sys.stderr)
        return 1

    # The model is asked for bare JSON, but a stray sentence around it should
    # cost a retry rather than the cluster.
    match = _JSON_RE.search(done.stdout)
    if not match:
        print(f"no JSON in answer: {done.stdout.strip()[:500]}", file=sys.stderr)
        return 1
    print(match.group(0))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
