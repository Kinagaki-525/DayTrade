from __future__ import annotations

import hashlib
import subprocess

import pytest

from src import source_fetch
from src.source_fetch import (
    CONNECT_TIMEOUT_SECONDS,
    MAX_RESPONSE_BYTES,
    MAX_RETRIES,
    TOTAL_TIMEOUT_SECONDS,
    FetchResult,
    SourceFetchError,
    TransportResult,
    curl_argv,
    fetch_source,
    source_page_filename,
    status_for_http_status,
    verify_source_page,
)


PAGE = b"<html><body>hello</body></html>"


def _transport(
    body: bytes = PAGE,
    status: int | None = 200,
    exit_code: int = 0,
    content_type: str | None = "text/html; charset=utf-8",
):
    def _run(url: str) -> TransportResult:
        return TransportResult(
            exit_code=exit_code,
            http_status=status,
            content_type=content_type,
            body=body,
        )

    return _run


def test_curl_argv_is_fixed_and_safe():
    argv = curl_argv("https://www.jpx.co.jp/x", user_agent_value="daytrade/1.0")
    assert argv[0] == "curl"
    assert "--disable" in argv  # never read curlrc
    assert "--no-location" in argv  # never follow redirects
    assert argv[argv.index("--retry") + 1] == str(MAX_RETRIES) == "0"
    assert argv[argv.index("--connect-timeout") + 1] == str(CONNECT_TIMEOUT_SECONDS)
    assert argv[argv.index("--max-time") + 1] == str(TOTAL_TIMEOUT_SECONDS)
    assert argv[argv.index("--max-filesize") + 1] == str(MAX_RESPONSE_BYTES)
    assert argv[argv.index("--user-agent") + 1] == "daytrade/1.0"
    assert argv[argv.index("--request") + 1] == "GET"
    # no cookies, no auth, no insecure TLS
    for forbidden in ("--cookie", "--user", "--insecure", "-k", "--header"):
        assert forbidden not in argv


def test_user_agent_must_come_from_the_environment(monkeypatch):
    monkeypatch.delenv(source_fetch.USER_AGENT_ENV_VAR, raising=False)
    with pytest.raises(SourceFetchError) as exc_info:
        source_fetch.user_agent()
    assert exc_info.value.code == "HTTP_USER_AGENT_NOT_CONFIGURED"

    monkeypatch.setenv(source_fetch.USER_AGENT_ENV_VAR, "  ")
    with pytest.raises(SourceFetchError):
        source_fetch.user_agent()

    monkeypatch.setenv(source_fetch.USER_AGENT_ENV_VAR, "daytrade/1.0")
    assert source_fetch.user_agent() == "daytrade/1.0"


def test_curl_transport_runs_without_a_shell(monkeypatch):
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(argv, 0, b"body\n200\ntext/html", b"")

    monkeypatch.setenv(source_fetch.USER_AGENT_ENV_VAR, "daytrade/1.0")
    monkeypatch.setattr(subprocess, "run", fake_run)

    result = source_fetch.curl_transport("https://www.jpx.co.jp/x")
    assert result.http_status == 200
    assert result.body == b"body"
    assert captured["kwargs"]["shell"] is False
    assert isinstance(captured["argv"], list)


@pytest.mark.parametrize(
    "http_status,expected",
    [
        (200, "FOUND"),
        (301, "ACCESS_FAILED"),
        (302, "ACCESS_FAILED"),
        (403, "ACCESS_FAILED"),
        (404, "NOT_FOUND"),
        (410, "NOT_FOUND"),
        (425, "NOT_YET_AVAILABLE"),
        (429, "ACCESS_FAILED"),
        (500, "ACCESS_FAILED"),
        (503, "NOT_YET_AVAILABLE"),
        (None, "ACCESS_FAILED"),
        (418, "ACCESS_FAILED"),
    ],
)
def test_http_status_maps_to_source_status(http_status, expected):
    assert status_for_http_status(http_status) == expected


def test_fetch_stores_unmodified_bytes_with_their_sha256(tmp_path):
    result = fetch_source(
        "https://www.jpx.co.jp/listing/co-search/",
        source_id="JPX_LISTED_COMPANY",
        candidate_code="7203",
        run_dir=tmp_path,
        transport=_transport(),
    )
    assert isinstance(result, FetchResult)
    assert result.status == "FOUND"
    assert result.source_page_sha256 == hashlib.sha256(PAGE).hexdigest()
    assert result.source_page_size_bytes == len(PAGE)

    stored = tmp_path / result.source_page_path
    assert stored.read_bytes() == PAGE
    assert stored.name.startswith("JPX_LISTED_COMPANY__7203__")
    assert stored.name.endswith(".raw")


def test_global_source_page_uses_the_GLOBAL_token(tmp_path):
    result = fetch_source(
        "https://www.jpx.co.jp/corporate/about-jpx/calendar/",
        source_id="JPX_CALENDAR",
        candidate_code=None,
        run_dir=tmp_path,
        transport=_transport(),
    )
    assert "__GLOBAL__" in result.source_page_path


def test_source_page_filename_scheme():
    digest = "a" * 64
    assert (
        source_page_filename("YAHOO_JP_QUOTE", "7203", digest)
        == f"YAHOO_JP_QUOTE__7203__{'a' * 16}.raw"
    )
    with pytest.raises(SourceFetchError):
        source_page_filename("bad id", "7203", digest)
    with pytest.raises(SourceFetchError):
        source_page_filename("YAHOO_JP_QUOTE", "../etc", digest)


def test_redirect_is_not_followed_and_becomes_access_failed(tmp_path):
    result = fetch_source(
        "https://www.jpx.co.jp/x",
        source_id="JPX_CALENDAR",
        run_dir=tmp_path,
        transport=_transport(body=b"", status=302),
    )
    assert result.status == "ACCESS_FAILED"
    assert result.source_page_path is None


def test_policy_violation_never_reaches_the_transport(tmp_path):
    def exploding_transport(url: str):  # pragma: no cover - must not run
        raise AssertionError("transport must not be called for a rejected URL")

    result = fetch_source(
        "http://evil.example.com/x",
        source_id="JPX_CALENDAR",
        run_dir=tmp_path,
        transport=exploding_transport,
    )
    assert result.status == "ACCESS_FAILED"
    assert result.notes == ("NETWORK_POLICY_SCHEME_FORBIDDEN",)


def test_transport_failure_is_access_failed(tmp_path):
    result = fetch_source(
        "https://www.jpx.co.jp/x",
        source_id="JPX_CALENDAR",
        run_dir=tmp_path,
        transport=_transport(body=b"", status=None, exit_code=28),
    )
    assert result.status == "ACCESS_FAILED"
    assert result.transport_exit_code == 28
    assert result.notes == ("TRANSPORT_FAILED",)


def test_oversized_response_is_rejected(tmp_path):
    oversized = b"x" * (MAX_RESPONSE_BYTES + 1)
    result = fetch_source(
        "https://www.jpx.co.jp/x",
        source_id="JPX_CALENDAR",
        run_dir=tmp_path,
        transport=_transport(body=oversized),
    )
    assert result.status == "ACCESS_FAILED"
    assert result.notes == ("RESPONSE_TOO_LARGE",)


def test_verify_source_page_detects_a_one_byte_tamper(tmp_path):
    result = fetch_source(
        "https://www.jpx.co.jp/x",
        source_id="JPX_CALENDAR",
        run_dir=tmp_path,
        transport=_transport(),
    )
    assert verify_source_page(tmp_path, result.source_page_path, result.source_page_sha256)

    stored = tmp_path / result.source_page_path
    tampered = bytearray(stored.read_bytes())
    tampered[0] = tampered[0] ^ 0x01
    stored.write_bytes(bytes(tampered))

    with pytest.raises(SourceFetchError) as exc_info:
        verify_source_page(tmp_path, result.source_page_path, result.source_page_sha256)
    assert exc_info.value.code == "SOURCE_PAGE_HASH_MISMATCH"


def test_verify_source_page_rejects_paths_escaping_the_run_dir(tmp_path):
    with pytest.raises(SourceFetchError) as exc_info:
        verify_source_page(tmp_path, "../outside.raw", "0" * 64)
    assert exc_info.value.code == "SOURCE_PAGE_PATH_ESCAPES_RUN_DIR"
