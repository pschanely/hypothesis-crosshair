"""Process isolation for running untrusted third-party test suites.

Two backends: :class:`DockerSandbox` is the supported one, and
:class:`LocalSandbox` runs directly on the host for development only. The
local backend must be requested explicitly; nothing selects it by default.
"""

import os
import shutil
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence


@dataclass
class ExecResult:
    returncode: int
    stdout: str
    stderr: str
    duration: float
    timed_out: bool = False

    @property
    def killed_by_signal(self) -> Optional[int]:
        return -self.returncode if self.returncode < 0 else None


@dataclass
class Limits:
    """Resource ceilings applied to every sandboxed process."""

    wall_seconds: int = 900
    memory_mb: int = 4096
    cpus: float = 1.0
    pids: int = 256
    output_bytes: int = 4_000_000


class Sandbox(ABC):
    @abstractmethod
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        network: bool = False,
        limits: Optional[Limits] = None,
    ) -> ExecResult:
        ...


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit // 2] + "\n...[truncated]...\n" + text[-limit // 2 :]


class DockerSandbox(Sandbox):
    """Runs each command in a throwaway, locked-down container.

    Network access is off unless a caller explicitly asks for it, which only
    the dependency-installation step does.
    """

    def __init__(
        self,
        image: str,
        *,
        workdir_mount: str = "/work",
        docker: str = "docker",
        extra_args: Sequence[str] = (),
    ) -> None:
        self.image = image
        self.workdir_mount = workdir_mount
        self.docker = docker
        self.extra_args = list(extra_args)

    def build_argv(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        network: bool = False,
        limits: Optional[Limits] = None,
    ) -> List[str]:
        limits = limits or Limits()
        cmd = [
            self.docker,
            "run",
            "--rm",
            "--interactive=false",
            f"--network={'bridge' if network else 'none'}",
            "--read-only",
            "--tmpfs=/tmp:rw,noexec,nosuid,size=1g",
            f"--tmpfs={self.workdir_mount}/.scratch:rw,nosuid,size=1g",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--pids-limit={limits.pids}",
            f"--memory={limits.memory_mb}m",
            f"--memory-swap={limits.memory_mb}m",
            f"--cpus={limits.cpus}",
            "--user=65534:65534",
            f"--volume={cwd}:{self.workdir_mount}:rw",
            f"--workdir={self.workdir_mount}",
        ]
        for key, value in sorted((env or {}).items()):
            cmd.append(f"--env={key}={value}")
        cmd.extend(self.extra_args)
        cmd.append(self.image)
        cmd.extend(argv)
        return cmd

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        network: bool = False,
        limits: Optional[Limits] = None,
    ) -> ExecResult:
        limits = limits or Limits()
        cmd = self.build_argv(argv, cwd=cwd, env=env, network=network, limits=limits)
        return _spawn(cmd, cwd=None, env=os.environ.copy(), limits=limits)


class LocalSandbox(Sandbox):
    """Runs commands directly on the host. Provides no isolation.

    Intended for developing the pipeline against code you already trust.
    """

    def __init__(self, *, i_understand_this_is_unsafe: bool = False) -> None:
        if not i_understand_this_is_unsafe:
            raise RuntimeError(
                "LocalSandbox provides no isolation and must be requested "
                "explicitly with i_understand_this_is_unsafe=True"
            )

    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: str,
        env: Optional[Dict[str, str]] = None,
        network: bool = False,
        limits: Optional[Limits] = None,
    ) -> ExecResult:
        limits = limits or Limits()
        merged = os.environ.copy()
        merged.update(env or {})
        return _spawn(list(argv), cwd=cwd, env=merged, limits=limits)


def _rlimit_setter(limits: Limits):
    def apply() -> None:
        os.setsid()
        try:
            import resource

            soft = limits.memory_mb * 1024 * 1024
            resource.setrlimit(resource.RLIMIT_AS, (soft, soft))
            resource.setrlimit(resource.RLIMIT_NPROC, (limits.pids, limits.pids))
        except Exception:
            pass

    return apply


def _spawn(
    cmd: List[str], *, cwd: Optional[str], env: Dict[str, str], limits: Limits
) -> ExecResult:
    """Run to completion, escalating a timeout straight to SIGKILL.

    CrossHair installs a bytecode tracer, and a wedged tracer may never run a
    SIGTERM handler, so the whole process group is killed outright.
    """
    started = time.monotonic()
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors="replace",
        preexec_fn=_rlimit_setter(limits),
    )
    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=limits.wall_seconds)
    except subprocess.TimeoutExpired:
        timed_out = True
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        stdout, stderr = proc.communicate()
    return ExecResult(
        returncode=proc.returncode,
        stdout=_truncate(stdout or "", limits.output_bytes),
        stderr=_truncate(stderr or "", limits.output_bytes),
        duration=time.monotonic() - started,
        timed_out=timed_out,
    )


def docker_available(docker: str = "docker") -> bool:
    if shutil.which(docker) is None:
        return False
    probe = subprocess.run(
        [docker, "info"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return probe.returncode == 0
