"""The install summaries' runtime-API hint keeps the token off argv.

The runtime may require its token on loopback, so a token-less curl hint
no longer works. The hint pipes the header into curl (-H @-) and prints
only the command that reads it, never a token.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from anolis_workbench.cli import provision_cli
from anolis_workbench.core import deploy, executor

FAKE_TOKEN = "fake-runtime-token-for-tests-0000"
HEADER_CMD = "sudo sed -n 's/^ANOLIS_API_TOKEN=/Authorization: Bearer /p' /etc/anolis/runtime.env"


@pytest.fixture(autouse=True)
def _tokens_in_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANOLIS_API_TOKEN", FAKE_TOKEN)
    monkeypatch.setenv("ANOLIS_WORKBENCH_RUNTIME_TOKEN", FAKE_TOKEN)


def _result() -> deploy.DeployResult:
    return deploy.DeployResult(project_name="p", runtime_version="0.1.47", prefix="/opt/anolis", output="")


def _assert_hint(out: str, url: str) -> None:
    assert FAKE_TOKEN not in out
    assert "curl http" not in out, "a token-less curl hint is left"
    lines = out.splitlines()
    pipe = next(i for i, line in enumerate(lines) if f"curl -s -H @- {url}" in line)
    assert HEADER_CMD in lines[pipe - 1] and lines[pipe - 1].rstrip().endswith("\\")
    # The only Authorization text is the sed replacement that builds it.
    assert out.count("Authorization: Bearer") == out.count(HEADER_CMD)


def test_local_install_hint(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(provision_cli, "_provision_workspace", lambda args: tmp_path)
    monkeypatch.setattr(provision_cli.deploy, "deploy_local", lambda **kwargs: _result())
    monkeypatch.setattr(sys, "argv", ["anolis-provision", "install"])
    assert provision_cli._run_install(provision_cli._parse_args()) == 0
    _assert_hint(capsys.readouterr().out, "http://127.0.0.1:8080/v0/runtime/status")


class _FakeSSH:
    def __init__(self, **kwargs: object) -> None:
        pass

    def run(self, cmd: list[str], **kwargs: object) -> executor.RunResult:
        return executor.RunResult(returncode=0, stdout="x86_64\n", stderr="")


def test_remote_install_hint(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(executor, "SubprocessSSHExecutor", _FakeSSH)
    monkeypatch.setattr(provision_cli, "_provision_workspace", lambda args: tmp_path)
    monkeypatch.setattr(provision_cli.deploy, "deploy_remote", lambda **kwargs: _result())
    monkeypatch.setattr(sys, "argv", ["anolis-provision", "remote", "--target", "someone@192.0.2.10"])
    assert provision_cli._run_remote(provision_cli._parse_args()) == 0
    out = capsys.readouterr().out
    _assert_hint(out, "http://192.0.2.10:8080/v0/runtime/status")
    assert f'ssh someone@192.0.2.10 "{HEADER_CMD}"' in out
