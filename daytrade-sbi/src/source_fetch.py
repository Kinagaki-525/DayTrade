"""Deterministic HTTP source acquisition.

The only outbound transport in this repository. It runs ``curl`` through
``subprocess`` with ``shell=False`` (no shell interpretation, no shell config
file sourcing), performs a single ``GET``, never follows redirects, never
retries, and writes the *unmodified* response bytes to disk together with
their SHA256.

No AI component transcribes anything here: bytes in, bytes on disk, plus a
small deterministic metadata record.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from src.file_io import atomic_write_bytes
from src.network_policy import NetworkPolicyError, validate_request_url


CONNECT_TIMEOUT_SECONDS = 10
TOTAL_TIMEOUT_SECONDS = 30
MAX_RETRIES = 0
MAX_RESPONSE_BYTES = 25 * 1024 * 1024  # 25 MiB
USER_AGENT_ENV_VAR = "DAYTRADE_HTTP_USER_AGENT"

SOURCE_PAGES_DIRNAME = "source_pages"
GLOBAL_CANDIDATE_TOKEN = "GLOBAL"
_SHA256_PREFIX_LENGTH = 16

_CANDIDATE_TOKEN_PATTERN = re.compile(r"^[0-9A-Z]+$")

#: HTTP status -> SourceStatus. Anything not listed maps to ACCESS_FAILED.
HTTP_STATUS_TO_SOURCE_STATUS: dict[int, str] = {
    200: "FOUND",
    301: "ACCESS_FAILED",
    302: "ACCESS_FAILED",
    303: "ACCESS_FAILED",
    307: "ACCESS_FAILED",
    308: "ACCESS_FAILED",
    400: "ACCESS_FAILED",
    401: "ACCESS_FAILED",
    403: "ACCESS_FAILED",
    404: "NOT_FOUND",
    410: "NOT_FOUND",
    425: "NOT_YET_AVAILABLE",
    429: "ACCESS_FAILED",
    500: "ACCESS_FAILED",
    502: "ACCESS_FAILED",
    503: "NOT_YET_AVAILABLE",
    504: "ACCESS_FAILED",
}


class SourceFetchError(RuntimeError):
    """Raised when acquisition cannot even be attempted (policy/config)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class TransportResult:
    """Raw outcome of one transport invocation."""

    exit_code: int
    http_status: int | None
    content_type: str | None
    body: bytes


@dataclass(frozen=True)
class FetchResult:
    """Deterministic result of one acquisition attempt."""

    url: str
    source_id: str
    candidate_code: str | None
    status: str
    http_status: int | None
    content_type: str | None
    transport_exit_code: int
    body: bytes
    source_page_sha256: str | None
    source_page_size_bytes: int | None
    source_page_path: str | None
    acquisition_method: str = "HTTP_GET"
    notes: tuple[str, ...] = ()


def status_for_http_status(http_status: int | None) -> str:
    if http_status is None:
        return "ACCESS_FAILED"
    return HTTP_STATUS_TO_SOURCE_STATUS.get(http_status, "ACCESS_FAILED")


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def user_agent() -> str:
    """The User-Agent, sourced *only* from the environment.

    There is no hard-coded default: a missing/blank value is a hard error, so
    that acquisition never silently runs under curl's own identity.
    """
    value = os.environ.get(USER_AGENT_ENV_VAR)
    if value is None or not value.strip() or value != value.strip():
        raise SourceFetchError(
            "HTTP_USER_AGENT_NOT_CONFIGURED",
            f"{USER_AGENT_ENV_VAR} must be set to a non-empty trimmed value",
        )
    return value


def source_page_filename(
    source_id: str,
    candidate_code: str | None,
    sha256_hex_value: str,
) -> str:
    """``<SOURCE_ID>__<candidate-or-GLOBAL>__<sha256prefix16>.raw``."""
    if not re.fullmatch(r"[A-Z0-9_]+", source_id or ""):
        raise SourceFetchError(
            "SOURCE_PAGE_FILENAME_INVALID",
            f"source_id is not a canonical identifier: {source_id!r}",
        )
    token = GLOBAL_CANDIDATE_TOKEN if candidate_code is None else candidate_code
    if not _CANDIDATE_TOKEN_PATTERN.match(token):
        raise SourceFetchError(
            "SOURCE_PAGE_FILENAME_INVALID",
            f"candidate token is not canonical: {token!r}",
        )
    if not re.fullmatch(r"[0-9a-f]{64}", sha256_hex_value or ""):
        raise SourceFetchError(
            "SOURCE_PAGE_FILENAME_INVALID",
            "sha256 must be a 64 character lowercase hex digest",
        )
    prefix = sha256_hex_value[:_SHA256_PREFIX_LENGTH]
    return f"{source_id}__{token}__{prefix}.raw"


def curl_argv(url: str, *, user_agent_value: str) -> list[str]:
    """The exact, fixed curl argument vector. No shell, no config, no cookies."""
    return [
        "curl",
        "--disable",              # never read curlrc
        "--silent",
        "--show-error",
        "--get",
        "--request", "GET",
        "--no-location",          # never follow redirects
        "--proto", "=https",
        "--tlsv1.2",
        "--ssl-reqd",
        "--retry", str(MAX_RETRIES),
        "--connect-timeout", str(CONNECT_TIMEOUT_SECONDS),
        "--max-time", str(TOTAL_TIMEOUT_SECONDS),
        "--max-filesize", str(MAX_RESPONSE_BYTES),
        "--user-agent", user_agent_value,
        "--write-out", "%{http_code}\\n%{content_type}",
        "--output", "-",
        "--url", url,
    ]


def curl_transport(url: str) -> TransportResult:
    """Default transport: one ``curl`` subprocess, ``shell=False``."""
    argv = curl_argv(url, user_agent_value=user_agent())
    completed = subprocess.run(  # noqa: S603 - fixed argv, shell=False
        argv,
        shell=False,
        capture_output=True,
        timeout=TOTAL_TIMEOUT_SECONDS + CONNECT_TIMEOUT_SECONDS,
        env={"PATH": os.environ.get("PATH", "")},
        check=False,
    )
    body, http_status, content_type = _split_write_out(completed.stdout)
    return TransportResult(
        exit_code=completed.returncode,
        http_status=http_status,
        content_type=content_type,
        body=body,
    )


def _split_write_out(stdout: bytes) -> tuple[bytes, int | None, str | None]:
    """Split the trailing ``--write-out`` trailer off the response body."""
    # The trailer is exactly the last two newline separated fields.
    pieces = stdout.rsplit(b"\n", 2)
    if len(pieces) != 3:
        return stdout, None, None
    body, status_bytes, content_type_bytes = pieces
    try:
        http_status = int(status_bytes.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError):
        return stdout, None, None
    try:
        content_type = content_type_bytes.decode("ascii").strip() or None
    except UnicodeDecodeError:
        content_type = None
    return body, http_status, content_type


def fetch_source(
    url: str,
    *,
    source_id: str,
    candidate_code: str | None = None,
    ticker: str | None = None,
    issuer_registry: dict[str, Any] | None = None,
    run_dir: Path | None = None,
    transport: Callable[[str], TransportResult] = curl_transport,
) -> FetchResult:
    """Validate, fetch once, and persist the unmodified response bytes.

    The response body is never decoded, normalized, or rewritten before it is
    hashed and written; the hash on the returned record is the hash of exactly
    what landed on disk.
    """
    try:
        request = validate_request_url(
            url,
            source_id=source_id,
            ticker=ticker,
            issuer_registry=issuer_registry,
        )
    except NetworkPolicyError as exc:
        return FetchResult(
            url=str(url),
            source_id=source_id,
            candidate_code=candidate_code,
            status="ACCESS_FAILED",
            http_status=None,
            content_type=None,
            transport_exit_code=-1,
            body=b"",
            source_page_sha256=None,
            source_page_size_bytes=None,
            source_page_path=None,
            notes=(exc.code,),
        )

    result = transport(request.url)
    if not isinstance(result, TransportResult):  # pragma: no cover - defensive
        raise SourceFetchError(
            "TRANSPORT_CONTRACT_VIOLATION",
            "transport must return a TransportResult",
        )

    if result.exit_code != 0:
        return FetchResult(
            url=request.url,
            source_id=source_id,
            candidate_code=candidate_code,
            status="ACCESS_FAILED",
            http_status=result.http_status,
            content_type=result.content_type,
            transport_exit_code=result.exit_code,
            body=b"",
            source_page_sha256=None,
            source_page_size_bytes=None,
            source_page_path=None,
            notes=("TRANSPORT_FAILED",),
        )

    if len(result.body) > MAX_RESPONSE_BYTES:
        return FetchResult(
            url=request.url,
            source_id=source_id,
            candidate_code=candidate_code,
            status="ACCESS_FAILED",
            http_status=result.http_status,
            content_type=result.content_type,
            transport_exit_code=result.exit_code,
            body=b"",
            source_page_sha256=None,
            source_page_size_bytes=None,
            source_page_path=None,
            notes=("RESPONSE_TOO_LARGE",),
        )

    status = status_for_http_status(result.http_status)
    if status != "FOUND":
        return FetchResult(
            url=request.url,
            source_id=source_id,
            candidate_code=candidate_code,
            status=status,
            http_status=result.http_status,
            content_type=result.content_type,
            transport_exit_code=result.exit_code,
            body=b"",
            source_page_sha256=None,
            source_page_size_bytes=None,
            source_page_path=None,
            notes=(),
        )

    digest = sha256_hex(result.body)
    filename = source_page_filename(source_id, candidate_code, digest)
    relative_path = f"{SOURCE_PAGES_DIRNAME}/{filename}"
    if run_dir is not None:
        atomic_write_bytes(Path(run_dir) / SOURCE_PAGES_DIRNAME / filename, result.body)

    return FetchResult(
        url=request.url,
        source_id=source_id,
        candidate_code=candidate_code,
        status="FOUND",
        http_status=result.http_status,
        content_type=result.content_type,
        transport_exit_code=result.exit_code,
        body=result.body,
        source_page_sha256=digest,
        source_page_size_bytes=len(result.body),
        source_page_path=relative_path,
    )


def verify_source_page(
    run_dir: Path,
    source_page_path: str,
    expected_sha256: str,
) -> bytes:
    """Re-read a stored raw page and verify its hash.

    Raises ``SOURCE_PAGE_HASH_MISMATCH`` on any difference. There is no
    silent repair and no re-fetch.
    """
    target = (Path(run_dir) / source_page_path).resolve()
    base = Path(run_dir).resolve()
    try:
        target.relative_to(base)
    except ValueError:
        raise SourceFetchError(
            "SOURCE_PAGE_PATH_ESCAPES_RUN_DIR",
            f"source_page_path must stay under the run directory: {source_page_path}",
        ) from None
    if not target.is_file():
        raise SourceFetchError(
            "SOURCE_PAGE_MISSING",
            f"stored raw source page does not exist: {source_page_path}",
        )
    data = target.read_bytes()
    actual = sha256_hex(data)
    if actual != expected_sha256:
        raise SourceFetchError(
            "SOURCE_PAGE_HASH_MISMATCH",
            f"{source_page_path}: expected {expected_sha256}, found {actual}",
        )
    return data
