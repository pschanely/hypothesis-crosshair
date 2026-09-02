import pytest
from discovery.sandbox import DockerSandbox, Limits, LocalSandbox


def argv(**kwargs):
    box = DockerSandbox(image="python:3.12-slim")
    return box.build_argv(["python", "-m", "pytest"], cwd="/srv/proj", **kwargs)


def test_execution_has_no_network_by_default():
    assert "--network=none" in argv()


def test_network_is_only_available_when_asked_for():
    assert "--network=bridge" in argv(network=True)


@pytest.mark.parametrize(
    "flag",
    [
        "--rm",
        "--read-only",
        "--cap-drop=ALL",
        "--security-opt=no-new-privileges",
        "--user=65534:65534",
    ],
)
def test_hardening_flags_are_always_present(flag):
    assert flag in argv()


def test_resource_ceilings_are_applied():
    line = argv(limits=Limits(memory_mb=512, cpus=2.0, pids=64))
    assert "--memory=512m" in line
    assert "--memory-swap=512m" in line  # no swap escape hatch
    assert "--cpus=2.0" in line
    assert "--pids-limit=64" in line


def test_the_container_runtime_socket_is_never_mounted():
    assert not any("docker.sock" in token for token in argv())


def test_environment_is_passed_explicitly():
    assert "--env=HCD_BACKEND=crosshair" in argv(env={"HCD_BACKEND": "crosshair"})


def test_local_sandbox_must_be_requested_deliberately():
    with pytest.raises(RuntimeError, match="no isolation"):
        LocalSandbox()
    assert LocalSandbox(i_understand_this_is_unsafe=True) is not None
