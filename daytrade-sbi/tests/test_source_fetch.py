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
    _fetch_source,
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


def test_curl_argv_is_fixed_and_safe(tmp_path):
    body_path = tmp_path / "body.raw"
    argv = curl_argv(
        "https://www.jpx.co.jp/x",
        user_agent_value="daytrade/1.0",
        body_path=body_path,
    )
    assert argv[0] == "curl"
    # The body goes to a file; stdout is reserved for --write-out metadata.
    assert argv[argv.index("--output") + 1] == str(body_path)
    assert argv[argv.index("--write-out") + 1] == "%{http_code} %{content_type}"
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


def _fake_curl(monkeypatch, body: bytes, stdout: bytes = b"200 text/html", code: int = 0):
    """Stand in for curl: write ``body`` to --output, print metadata on stdout.

    This mirrors the real contract exactly -- two separate streams -- so a body
    can never masquerade as metadata.
    """
    captured: dict[str, object] = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        from pathlib import Path as _Path

        _Path(argv[argv.index("--output") + 1]).write_bytes(body)
        return subprocess.CompletedProcess(argv, code, stdout, b"")

    monkeypatch.setenv(source_fetch.USER_AGENT_ENV_VAR, "daytrade/1.0")
    monkeypatch.setattr(subprocess, "run", fake_run)
    return captured


def test_curl_transport_runs_without_a_shell(monkeypatch):
    captured = _fake_curl(monkeypatch, b"body")
    result = source_fetch.curl_transport("https://www.jpx.co.jp/x")
    assert result.http_status == 200
    assert result.content_type == "text/html"
    assert result.body == b"body"
    assert captured["kwargs"]["shell"] is False
    assert isinstance(captured["argv"], list)


@pytest.mark.parametrize(
    "body",
    [
        pytest.param(b"<html>no trailing newline</html>", id="no-trailing-newline"),
        pytest.param(b"<html>trailing newline</html>\n", id="trailing-newline"),
        pytest.param(bytes(range(256)), id="binary-bytes"),
        pytest.param(b"prefix\n200\ntext/html", id="body-looks-like-write-out"),
        pytest.param(b"\n200\ntext/html", id="body-is-exactly-write-out"),
        pytest.param(b"", id="empty-body"),
        pytest.param(b"a\n" * 1000, id="many-newlines"),
    ],
)
def test_curl_body_without_newline_is_not_corrupted(monkeypatch, body):
    """The body is read from a file, never sliced out of a shared stdout
    stream, so no byte sequence in the body can corrupt it or be confused
    with the transport metadata."""
    _fake_curl(monkeypatch, body, stdout=b"200 text/html; charset=utf-8")
    result = source_fetch.curl_transport("https://www.jpx.co.jp/x")
    assert result.body == body
    assert result.http_status == 200
    assert result.content_type == "text/html; charset=utf-8"


def test_curl_transport_never_delimiter_parses_the_body(monkeypatch):
    """A body that *is* a plausible write-out trailer still reports the real
    status code from stdout, not the one embedded in the page."""
    _fake_curl(monkeypatch, b"whatever\n404\ntext/plain", stdout=b"200 text/html")
    result = source_fetch.curl_transport("https://www.jpx.co.jp/x")
    assert result.http_status == 200
    assert result.body == b"whatever\n404\ntext/plain"


def test_curl_transport_timeout_is_access_failed(monkeypatch, tmp_path):
    def fake_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 30)

    monkeypatch.setenv(source_fetch.USER_AGENT_ENV_VAR, "daytrade/1.0")
    monkeypatch.setattr(subprocess, "run", fake_run)
    result = source_fetch.curl_transport("https://www.jpx.co.jp/x")
    assert result.exit_code == source_fetch.CURL_TIMEOUT_EXIT_CODE
    assert status_for_http_status(result.http_status) == "ACCESS_FAILED"


def test_write_out_metadata_parsing_is_strict():
    assert source_fetch.parse_write_out(b"200 text/html") == (200, "text/html")
    assert source_fetch.parse_write_out(b"404 ") == (404, None)
    assert source_fetch.parse_write_out(b"") == (None, None)
    assert source_fetch.parse_write_out(b"not-a-status") == (None, None)
    assert source_fetch.parse_write_out(b"999 x") == (None, None)


@pytest.mark.parametrize(
    "http_status,expected",
    [
        (200, "FOUND"),
        (301, "ACCESS_FAILED"),
        (302, "ACCESS_FAILED"),
        (403, "ACCESS_FAILED"),
        (404, "NOT_FOUND"),
        (410, "NOT_FOUND"),
        # 425/503 are ordinary access failures. There is no NOT_YET_AVAILABLE
        # status: inventing one would invite the retry loop the Request Budget
        # forbids.
        (425, "ACCESS_FAILED"),
        (429, "ACCESS_FAILED"),
        (500, "ACCESS_FAILED"),
        (502, "ACCESS_FAILED"),
        (503, "ACCESS_FAILED"),
        (504, "ACCESS_FAILED"),
        (None, "ACCESS_FAILED"),
        (418, "ACCESS_FAILED"),
    ],
)
def test_http_status_maps_to_source_status(http_status, expected):
    assert status_for_http_status(http_status) == expected


def test_fetch_stores_unmodified_bytes_with_their_sha256(tmp_path):
    result = _fetch_source(
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
    result = _fetch_source(
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
    result = _fetch_source(
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

    result = _fetch_source(
        "http://evil.example.com/x",
        source_id="JPX_CALENDAR",
        run_dir=tmp_path,
        transport=exploding_transport,
    )
    assert result.status == "ACCESS_FAILED"
    assert result.notes == ("NETWORK_POLICY_SCHEME_FORBIDDEN",)


def test_transport_timeout_is_access_failed(tmp_path):
    result = _fetch_source(
        "https://www.jpx.co.jp/x",
        source_id="JPX_CALENDAR",
        run_dir=tmp_path,
        transport=_transport(body=b"", status=None, exit_code=28),
    )
    assert result.status == "ACCESS_FAILED"
    assert result.transport_exit_code == 28
    assert result.notes == ("TRANSPORT_TIMEOUT",)


def test_curl_execution_failure_is_execution_failed(tmp_path):
    """A curl that could not run at all is EXECUTION_FAILED -- an
    infrastructure fault -- not a fact about the source."""
    result = _fetch_source(
        "https://www.jpx.co.jp/x",
        source_id="JPX_CALENDAR",
        run_dir=tmp_path,
        transport=_transport(body=b"", status=None, exit_code=127),
    )
    assert result.status == "EXECUTION_FAILED"
    assert result.notes == ("TRANSPORT_FAILED",)


def test_oversized_response_is_rejected(tmp_path):
    oversized = b"x" * (MAX_RESPONSE_BYTES + 1)
    result = _fetch_source(
        "https://www.jpx.co.jp/x",
        source_id="JPX_CALENDAR",
        run_dir=tmp_path,
        transport=_transport(body=oversized),
    )
    assert result.status == "ACCESS_FAILED"
    assert result.notes == ("RESPONSE_TOO_LARGE",)


def test_verify_source_page_detects_a_one_byte_tamper(tmp_path):
    result = _fetch_source(
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


# --------------------------------- FIX-004: the free-form URL boundary ---


def test_no_public_api_accepts_a_free_form_url():
    """A caller may name a ``source_id`` and a context. It may never name a URL.

    URL construction happens internally from the Source Matrix template + the
    ticker + the human-approved issuer registry. A public
    ``fetch_source(url, ...)`` would reopen exactly the hole the Source Matrix
    exists to close, so the low-level transport entry point is private.
    """
    assert not hasattr(source_fetch, "fetch_source")
    assert source_fetch._fetch_source.__name__.startswith("_")
    assert "fetch_source" not in getattr(source_fetch, "__all__", [])


def test_acquisition_module_exports_no_url_taking_entry_point():
    from src import source_acquisition

    exported = set(getattr(source_acquisition, "__all__", []))
    assert "_fetch_source" not in exported
    assert "fetch_source" not in exported
    # resolve_url is fine: it takes a Source Matrix *definition*, not a URL.
    import inspect

    signature = inspect.signature(source_acquisition.resolve_url)
    assert "url" not in signature.parameters
    assert "definition" in signature.parameters


def test_no_cli_command_accepts_a_url():
    """No CLI surface anywhere lets an operator or agent name a URL."""
    from src import cli

    parser = cli.build_parser()
    choices = parser._subparsers._group_actions[0].choices  # noqa: SLF001
    for name, subparser in choices.items():
        options = {
            option
            for action in subparser._actions
            for option in action.option_strings
        }
        for forbidden in ("--url", "--source-url", "--endpoint", "--host"):
            assert forbidden not in options, f"{name} exposes {forbidden}"
