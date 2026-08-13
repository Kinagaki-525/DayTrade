from __future__ import annotations

import pytest

from src.network_policy import (
    ALLOWED_HOSTS,
    NetworkPolicyError,
    approved_issuer_hosts,
    canonical_request_url,
    load_issuer_domain_registry,
    validate_request_url,
)


REGISTRY = {
    "registry_schema_version": 1,
    "approval_policy": {"human_approved_only": True, "auto_discovery_allowed": False},
    "issuers": [
        {
            "ticker": "7203",
            "issuer_name": "Example Corp",
            "approved_hosts": ["ir.example.co.jp"],
            "approved_by": "human-reviewer",
            "approved_at": "2026-08-12",
        }
    ],
}


def _code(exc_info) -> str:
    return exc_info.value.code


def test_allowed_host_https_is_accepted():
    request = validate_request_url(
        "https://www.jpx.co.jp/listing/co-search/", source_id="JPX_LISTED_COMPANY"
    )
    assert request.host == "www.jpx.co.jp"
    assert request.scheme == "https"


@pytest.mark.parametrize("host", sorted(ALLOWED_HOSTS))
def test_every_allowed_host_is_reachable(host):
    assert validate_request_url(f"https://{host}/x", source_id="JPX_CALENDAR").host == host


def test_unauthorized_host_is_rejected():
    with pytest.raises(NetworkPolicyError) as exc_info:
        validate_request_url("https://evil.example.com/x", source_id="JPX_CALENDAR")
    assert _code(exc_info) == "NETWORK_POLICY_HOST_NOT_ALLOWED"


def test_lookalike_subdomain_of_allowed_host_is_rejected():
    """Exact match only: no suffix matching, no wildcard subdomains."""
    with pytest.raises(NetworkPolicyError) as exc_info:
        validate_request_url("https://evil.www.jpx.co.jp/x", source_id="JPX_CALENDAR")
    assert _code(exc_info) == "NETWORK_POLICY_HOST_NOT_ALLOWED"


def test_plain_http_is_rejected():
    with pytest.raises(NetworkPolicyError) as exc_info:
        validate_request_url("http://www.jpx.co.jp/x", source_id="JPX_CALENDAR")
    assert _code(exc_info) == "NETWORK_POLICY_SCHEME_FORBIDDEN"


@pytest.mark.parametrize("url", ["https://192.168.0.1/x", "https://8.8.8.8/x"])
def test_raw_ip_host_is_rejected(url):
    with pytest.raises(NetworkPolicyError) as exc_info:
        validate_request_url(url, source_id="JPX_CALENDAR")
    assert _code(exc_info) == "NETWORK_POLICY_RAW_IP_FORBIDDEN"


def test_localhost_is_rejected():
    with pytest.raises(NetworkPolicyError) as exc_info:
        validate_request_url("https://localhost/x", source_id="JPX_CALENDAR")
    assert _code(exc_info) == "NETWORK_POLICY_LOCAL_HOST_FORBIDDEN"


def test_userinfo_credentials_are_rejected():
    with pytest.raises(NetworkPolicyError) as exc_info:
        validate_request_url(
            "https://user:pass@www.jpx.co.jp/x", source_id="JPX_CALENDAR"
        )
    assert _code(exc_info) == "NETWORK_POLICY_USERINFO_FORBIDDEN"


def test_explicit_port_is_rejected():
    with pytest.raises(NetworkPolicyError) as exc_info:
        validate_request_url("https://www.jpx.co.jp:8443/x", source_id="JPX_CALENDAR")
    assert _code(exc_info) == "NETWORK_POLICY_PORT_FORBIDDEN"


def test_fragment_is_stripped_from_the_request_url():
    request = validate_request_url(
        "https://kabutan.jp/stock/kabuka?code=7203#history", source_id="KABUTAN_HISTORY"
    )
    assert request.url == "https://kabutan.jp/stock/kabuka?code=7203"
    assert canonical_request_url("https://kabutan.jp/a#b") == "https://kabutan.jp/a"


def test_issuer_source_requires_an_approved_domain():
    request = validate_request_url(
        "https://ir.example.co.jp/ir/",
        source_id="COMPANY_IR",
        ticker="7203",
        issuer_registry=REGISTRY,
    )
    assert request.host == "ir.example.co.jp"


def test_unregistered_ticker_is_issuer_domain_not_approved():
    with pytest.raises(NetworkPolicyError) as exc_info:
        validate_request_url(
            "https://ir.example.co.jp/ir/",
            source_id="COMPANY_IR",
            ticker="9999",
            issuer_registry=REGISTRY,
        )
    assert _code(exc_info) == "ISSUER_DOMAIN_NOT_APPROVED"


def test_issuer_host_mismatch_is_rejected():
    with pytest.raises(NetworkPolicyError) as exc_info:
        validate_request_url(
            "https://ir.other.co.jp/ir/",
            source_id="COMPANY_IR",
            ticker="7203",
            issuer_registry=REGISTRY,
        )
    assert _code(exc_info) == "ISSUER_DOMAIN_HOST_MISMATCH"


def test_issuer_source_cannot_borrow_the_general_allowlist():
    """An issuer source must not fall back to the generic allowed hosts."""
    with pytest.raises(NetworkPolicyError) as exc_info:
        validate_request_url(
            "https://www.jpx.co.jp/ir/",
            source_id="COMPANY_IR",
            ticker="7203",
            issuer_registry=REGISTRY,
        )
    assert _code(exc_info) == "ISSUER_DOMAIN_HOST_MISMATCH"


def test_approved_issuer_hosts_requires_an_entry():
    with pytest.raises(NetworkPolicyError):
        approved_issuer_hosts("0000", REGISTRY)


def test_shipped_issuer_registry_is_schema_valid_and_human_approved_only():
    registry = load_issuer_domain_registry()
    assert registry["approval_policy"]["human_approved_only"] is True
    assert registry["approval_policy"]["auto_discovery_allowed"] is False
