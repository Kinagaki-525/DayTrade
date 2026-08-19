"""Claude Code Seccomp Human Runtime Acceptance v2 -- the differential probe.

This module has exactly one job: create an ``AF_UNIX``/``SOCK_STREAM`` socket
and report whether the OS allowed or blocked it. It never connects, binds,
listens, accepts, sends, or receives; it never touches the filesystem. A
Human runs this module's CLI once outside the Claude Sandbox and once inside
it, compares the two results, and only a Human -- never this module --
records that acceptance elsewhere.

Nothing here is Business Logic for the DayTrade pipeline. Nothing here talks
to the network. Nothing here writes a file. This module is deliberately kept
ignorant of any attestation artifact.
"""

from __future__ import annotations

import argparse
import errno
import socket
import sys
from typing import NoReturn

RESULT_ALLOWED = "UNIX_SOCKET_CREATE_ALLOWED"
RESULT_BLOCKED_EPERM = "UNIX_SOCKET_CREATE_BLOCKED_EPERM"

PROBE_OUTPUT_PREFIX = "DAYTRADE_SECCOMP_PROBE="
UNEXPECTED_RESULT_MARKER = "DAYTRADE_SECCOMP_PROBE_UNEXPECTED_RESULT"
SANDBOX_SECCOMP_NOT_ENFORCED_MARKER = "DAYTRADE_SANDBOX_SECCOMP_NOT_ENFORCED"


def probe_unix_socket_creation() -> str:
    """Create one AF_UNIX/SOCK_STREAM socket and report the outcome.

    Returns :data:`RESULT_ALLOWED` if creation succeeded (the socket is
    closed immediately afterward) or :data:`RESULT_BLOCKED_EPERM` if it
    failed with exactly ``errno.EPERM``. Any other ``OSError`` is not this
    function's to interpret -- it propagates so the caller can fail closed
    instead of guessing that some other permission error means the same
    thing as a seccomp filter.
    """
    try:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    except OSError as error:
        if error.errno == errno.EPERM:
            return RESULT_BLOCKED_EPERM
        raise
    else:
        sock.close()
        return RESULT_ALLOWED


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "DayTrade Seccomp Human Runtime Acceptance v2: create a single "
            "AF_UNIX/SOCK_STREAM socket and report whether the OS allowed or "
            "blocked it. No connect/bind/listen/send/recv, no network, no "
            "file write, no marker write."
        )
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--expect-unsandboxed",
        action="store_true",
        help="Run from a normal Linux/WSL shell outside the Claude Sandbox.",
    )
    group.add_argument(
        "--expect-sandboxed",
        action="store_true",
        help="Run from inside the Claude Code Bash Sandbox.",
    )
    return parser


def _exit(code: int) -> NoReturn:
    sys.exit(code)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        result = probe_unix_socket_creation()
    except OSError as error:
        sys.stderr.write(
            f"{UNEXPECTED_RESULT_MARKER}: unexpected OSError "
            f"(errno={error.errno}): {error.strerror}\n"
        )
        return 2

    if args.expect_unsandboxed:
        if result == RESULT_ALLOWED:
            sys.stdout.write(f"{PROBE_OUTPUT_PREFIX}{RESULT_ALLOWED}\n")
            return 0
        sys.stderr.write(
            f"{UNEXPECTED_RESULT_MARKER}: expected {RESULT_ALLOWED}, got {result}\n"
        )
        return 2

    # args.expect_sandboxed
    if result == RESULT_BLOCKED_EPERM:
        sys.stdout.write(f"{PROBE_OUTPUT_PREFIX}{RESULT_BLOCKED_EPERM}\n")
        return 0
    sys.stderr.write(
        f"{SANDBOX_SECCOMP_NOT_ENFORCED_MARKER}: expected "
        f"{RESULT_BLOCKED_EPERM}, got {result}\n"
    )
    return 2


if __name__ == "__main__":  # pragma: no cover
    _exit(main())
