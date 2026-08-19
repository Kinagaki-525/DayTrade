"""Seccomp Human Runtime Acceptance v2: the differential probe.

No real socket enforcement is exercised here -- ``socket.socket`` is always
monkeypatched. These tests only pin the probe's decision logic and its CLI
contract; the actual Sandbox-in/Sandbox-out comparison is a Human Runtime
Smoke step performed on a provisioned host after merge.
"""

from __future__ import annotations

import errno
import socket

import pytest

from src import claude_seccomp_probe as probe


# --------------------------------------------------------- probe function ---


def test_probe_returns_allowed_when_af_unix_socket_creation_succeeds(monkeypatch):
    created = []

    class FakeSocket:
        def close(self):
            created.append("closed")

    def fake_socket(family, kind):
        return FakeSocket()

    monkeypatch.setattr(socket, "socket", fake_socket)
    assert probe.probe_unix_socket_creation() == probe.RESULT_ALLOWED


def test_probe_closes_socket_when_creation_succeeds(monkeypatch):
    calls = {"closed": False}

    class FakeSocket:
        def close(self):
            calls["closed"] = True

    monkeypatch.setattr(socket, "socket", lambda family, kind: FakeSocket())
    probe.probe_unix_socket_creation()
    assert calls["closed"] is True


def test_probe_returns_blocked_only_for_eperm(monkeypatch):
    def raise_eperm(family, kind):
        raise OSError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(socket, "socket", raise_eperm)
    assert probe.probe_unix_socket_creation() == probe.RESULT_BLOCKED_EPERM


def test_probe_does_not_treat_eacces_as_seccomp_success(monkeypatch):
    def raise_eacces(family, kind):
        raise OSError(errno.EACCES, "Permission denied")

    monkeypatch.setattr(socket, "socket", raise_eacces)
    with pytest.raises(OSError):
        probe.probe_unix_socket_creation()


@pytest.mark.parametrize("code", [errno.EINVAL, errno.ENOSYS, errno.ENOMEM])
def test_probe_does_not_treat_other_oserror_as_seccomp_success(monkeypatch, code):
    def raise_other(family, kind):
        raise OSError(code, "some other failure")

    monkeypatch.setattr(socket, "socket", raise_other)
    with pytest.raises(OSError):
        probe.probe_unix_socket_creation()


def test_probe_uses_af_unix_stream_socket(monkeypatch):
    seen = {}

    class FakeSocket:
        def close(self):
            pass

    def fake_socket(family, kind):
        seen["family"] = family
        seen["kind"] = kind
        return FakeSocket()

    monkeypatch.setattr(socket, "socket", fake_socket)
    probe.probe_unix_socket_creation()
    assert seen["family"] == socket.AF_UNIX
    assert seen["kind"] == socket.SOCK_STREAM


# ------------------------------------------------------------------- CLI ---


def _patch_result(monkeypatch, result=None, raise_error=None):
    def fake_probe():
        if raise_error is not None:
            raise raise_error
        return result

    monkeypatch.setattr(probe, "probe_unix_socket_creation", fake_probe)


def test_expect_unsandboxed_accepts_allowed(monkeypatch, capsys):
    _patch_result(monkeypatch, result=probe.RESULT_ALLOWED)
    code = probe.main(["--expect-unsandboxed"])
    assert code == 0
    out = capsys.readouterr()
    assert out.out == f"DAYTRADE_SECCOMP_PROBE={probe.RESULT_ALLOWED}\n"


def test_expect_unsandboxed_rejects_blocked_eperm(monkeypatch, capsys):
    _patch_result(monkeypatch, result=probe.RESULT_BLOCKED_EPERM)
    code = probe.main(["--expect-unsandboxed"])
    assert code == 2
    out = capsys.readouterr()
    assert probe.UNEXPECTED_RESULT_MARKER in out.err


def test_expect_sandboxed_accepts_blocked_eperm(monkeypatch, capsys):
    _patch_result(monkeypatch, result=probe.RESULT_BLOCKED_EPERM)
    code = probe.main(["--expect-sandboxed"])
    assert code == 0
    out = capsys.readouterr()
    assert out.out == f"DAYTRADE_SECCOMP_PROBE={probe.RESULT_BLOCKED_EPERM}\n"


def test_expect_sandboxed_rejects_allowed(monkeypatch, capsys):
    _patch_result(monkeypatch, result=probe.RESULT_ALLOWED)
    code = probe.main(["--expect-sandboxed"])
    assert code == 2
    out = capsys.readouterr()
    assert probe.SANDBOX_SECCOMP_NOT_ENFORCED_MARKER in out.err


@pytest.mark.parametrize("code", [errno.EINVAL, errno.ENOSYS, errno.ENOMEM])
def test_unexpected_oserror_exits_nonzero(monkeypatch, capsys, code):
    _patch_result(monkeypatch, raise_error=OSError(code, "boom"))
    for flag in ("--expect-unsandboxed", "--expect-sandboxed"):
        result = probe.main([flag])
        assert result == 2
        out = capsys.readouterr()
        assert probe.UNEXPECTED_RESULT_MARKER in out.err


def test_both_flags_together_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit):
        probe.main(["--expect-unsandboxed", "--expect-sandboxed"])


def test_no_flags_is_an_argparse_error(capsys):
    with pytest.raises(SystemExit):
        probe.main([])


# ------------------------------------------------------- security statics ---


PROBE_SOURCE_PATH = "src/claude_seccomp_probe.py"


def _probe_source() -> str:
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    return (project_root / "src" / "claude_seccomp_probe.py").read_text("utf-8")


def _launcher_source() -> str:
    from pathlib import Path

    project_root = Path(__file__).resolve().parents[1]
    return (project_root / "scripts" / "claude-seccomp-probe").read_text("utf-8")


@pytest.mark.parametrize(
    "forbidden",
    [
        ".connect(",
        ".connect_ex(",
        ".bind(",
        ".listen(",
        ".accept(",
        ".send(",
        ".sendto(",
        ".recv(",
        "subprocess",
        "requests",
        "httpx",
        "urllib",
        "os.system",
    ],
)
def test_probe_module_never_gains_dangerous_operations(forbidden):
    assert forbidden not in _probe_source()


@pytest.mark.parametrize(
    "forbidden",
    ["/etc/daytrade-seccomp-verified", "DAYTRADE_SECCOMP_VERIFIED"],
)
def test_probe_module_never_references_the_marker(forbidden):
    assert forbidden not in _probe_source()


@pytest.mark.parametrize(
    "forbidden",
    ["/etc/daytrade-seccomp-verified", "DAYTRADE_SECCOMP_VERIFIED"],
)
def test_launcher_script_never_references_the_marker(forbidden):
    assert forbidden not in _launcher_source()


def test_probe_module_never_writes_a_file():
    text = _probe_source()
    for creator in ("write_text(", "write_bytes(", ".touch(", "open("):
        assert creator not in text


def test_launcher_script_is_a_thin_entry_point():
    text = _launcher_source()
    assert "from src.claude_seccomp_probe import main" in text
    for creator in ("write_text(", "write_bytes(", ".touch(", "open("):
        assert creator not in text
    for forbidden in ("sudo", "apt-get", "apt ", " pip ", "npm ", "curl", "wget", "socat", " nc ", "netcat"):
        assert forbidden not in text
