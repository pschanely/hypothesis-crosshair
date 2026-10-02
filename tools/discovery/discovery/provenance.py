"""What a verdict is a verdict *about*.

A cached verdict is only reusable against the same code, solver and plugin, so
the cache key needs all three. These read them off the project checkout and the
interpreter that will run the solver arm.
"""

import json
import subprocess
from typing import Dict, List

#: Reported when a value cannot be read.
#:
#: An unknown component must not collide with a known one, or a verdict
#: recorded before a version could be read would be served after an upgrade.
UNKNOWN = "unknown"

_VERSION_PROBE = """
import json, platform
out = {"python": platform.python_version()}
for name, dist in (("crosshair", "crosshair-tool"), ("plugin", "hypothesis-crosshair")):
    try:
        from importlib.metadata import version
        out[name] = version(dist)
    except Exception:
        out[name] = "unknown"
print(json.dumps(out))
"""


def project_commit(project_dir: str) -> str:
    """The checked-out commit, or ``UNKNOWN`` outside a git work tree."""
    try:
        done = subprocess.run(
            ["git", "-C", project_dir, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return UNKNOWN
    return done.stdout.strip() if done.returncode == 0 else UNKNOWN


def environment_versions(python_argv: List[str]) -> Dict[str, str]:
    """CrossHair, plugin and Python versions in the solver arm's interpreter."""
    blank = {"python": UNKNOWN, "crosshair": UNKNOWN, "plugin": UNKNOWN}
    try:
        done = subprocess.run(
            list(python_argv) + ["-c", _VERSION_PROBE],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return blank
    if done.returncode != 0:
        return blank
    try:
        found = json.loads(done.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return blank
    return {name: str(found.get(name, UNKNOWN)) for name in blank}
