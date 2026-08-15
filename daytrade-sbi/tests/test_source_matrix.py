from copy import deepcopy

import pytest

from src.config import load_yaml
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH, validate_source_matrix


def test_default_source_matrix_is_valid():
    payload = load_yaml(DEFAULT_SOURCE_MATRIX_PATH)

    result = validate_source_matrix(payload)

    assert result.valid is True
    assert result.errors == ()


def test_tdnet_is_rule_dependent_primary_source():
    payload = load_yaml(DEFAULT_SOURCE_MATRIX_PATH)
    tdnet = next(
        source for source in payload["sources"] if source["source_id"] == "JPX_TDNET"
    )

    assert tdnet["role"] == "PRIMARY"
    assert tdnet["criticality"] == "RULE_DEPENDENT"


def test_source_matrix_rejects_tdnet_non_primary_rule_dependent_setup():
    payload = deepcopy(load_yaml(DEFAULT_SOURCE_MATRIX_PATH))
    tdnet = next(
        source for source in payload["sources"] if source["source_id"] == "JPX_TDNET"
    )
    tdnet["role"] = "CONTEXT"
    tdnet["criticality"] = "CONTEXT"

    result = validate_source_matrix(payload)

    assert result.valid is False
    assert any("JPX_TDNET" in error for error in result.errors)


def test_source_matrix_requires_company_ir_disclosure_source():
    payload = deepcopy(load_yaml(DEFAULT_SOURCE_MATRIX_PATH))
    payload["sources"] = [source for source in payload["sources"] if source["source_id"] != "COMPANY_IR_DISCLOSURE"]

    result = validate_source_matrix(payload)

    assert result.valid is False
    assert any("COMPANY_IR_DISCLOSURE" in error for error in result.errors)


def test_source_matrix_rejects_duplicate_source_id():
    payload = deepcopy(load_yaml(DEFAULT_SOURCE_MATRIX_PATH))
    payload["sources"].append(dict(payload["sources"][0]))

    result = validate_source_matrix(payload)

    assert result.valid is False
    assert "duplicate source_id" in result.errors[0]


def test_source_matrix_rejects_runtime_substitution():
    payload = deepcopy(load_yaml(DEFAULT_SOURCE_MATRIX_PATH))
    payload["source_change_policy"]["runtime_substitution_allowed"] = True

    result = validate_source_matrix(payload)

    assert result.valid is False
    assert "source_matrix.schema.json validation failed" in result.errors[0]


def test_source_matrix_rejects_changed_tdnet_bootstrap_lookback():
    payload = deepcopy(load_yaml(DEFAULT_SOURCE_MATRIX_PATH))
    payload["tdnet_bootstrap_lookback_days"] = 2

    result = validate_source_matrix(payload)

    assert result.valid is False
    assert "tdnet_bootstrap_lookback_days" in result.errors[0]
    assert "1 was expected" in result.errors[0]


def test_source_matrix_rejects_changed_market_research_version():
    payload = deepcopy(load_yaml(DEFAULT_SOURCE_MATRIX_PATH))
    payload["market_research_version"] = "market-research-v1"

    result = validate_source_matrix(payload)

    assert result.valid is False
    assert "market_research_version" in result.errors[0]
    assert "'market-research-v2' was expected" in result.errors[0]


@pytest.mark.parametrize(
    "source_id", ["YAHOO_JP_HISTORY", "YAHOO_JP_NEWS", "YAHOO_JP_QUOTE"]
)
def test_yahoo_dot_t_sources_share_the_same_tse_confirmation_precondition(source_id):
    payload = load_yaml(DEFAULT_SOURCE_MATRIX_PATH)
    source = next(s for s in payload["sources"] if s["source_id"] == source_id)
    notes = source.get("notes", "")

    assert ".T" in source["url_template"]
    assert "TSE_LISTING_CONFIRMATION_PRECONDITION" in notes, (
        f"{source_id} notes must carry the shared TSE-confirmation precondition marker"
    )
    assert "Tokyo Stock Exchange" in notes
    assert "ALL_MARKETS" in notes
    assert "guessed" in notes


def test_yahoo_jp_quote_is_primary_trade_critical_turnover_source():
    payload = load_yaml(DEFAULT_SOURCE_MATRIX_PATH)
    yahoo_jp_quote = next(
        source for source in payload["sources"] if source["source_id"] == "YAHOO_JP_QUOTE"
    )

    assert yahoo_jp_quote["role"] == "PRIMARY"
    assert yahoo_jp_quote["criticality"] == "TRADE_CRITICAL"
    assert yahoo_jp_quote["information_type"] == "TURNOVER"


def test_source_matrix_rejects_missing_yahoo_jp_quote():
    payload = deepcopy(load_yaml(DEFAULT_SOURCE_MATRIX_PATH))
    payload["sources"] = [
        source for source in payload["sources"] if source["source_id"] != "YAHOO_JP_QUOTE"
    ]

    result = validate_source_matrix(payload)

    assert result.valid is False
    assert any("YAHOO_JP_QUOTE" in error for error in result.errors)


def test_source_matrix_rejects_yahoo_jp_quote_wrong_role():
    payload = deepcopy(load_yaml(DEFAULT_SOURCE_MATRIX_PATH))
    yahoo_jp_quote = next(
        source for source in payload["sources"] if source["source_id"] == "YAHOO_JP_QUOTE"
    )
    yahoo_jp_quote["role"] = "SECONDARY"

    result = validate_source_matrix(payload)

    assert result.valid is False
    assert any("YAHOO_JP_QUOTE must be PRIMARY role" in error for error in result.errors)


def test_source_matrix_rejects_yahoo_jp_quote_wrong_criticality():
    payload = deepcopy(load_yaml(DEFAULT_SOURCE_MATRIX_PATH))
    yahoo_jp_quote = next(
        source for source in payload["sources"] if source["source_id"] == "YAHOO_JP_QUOTE"
    )
    yahoo_jp_quote["criticality"] = "CONTEXT"

    result = validate_source_matrix(payload)

    assert result.valid is False
    assert any(
        "YAHOO_JP_QUOTE must be TRADE_CRITICAL criticality" in error for error in result.errors
    )


def test_source_matrix_rejects_yahoo_jp_quote_wrong_information_type():
    payload = deepcopy(load_yaml(DEFAULT_SOURCE_MATRIX_PATH))
    yahoo_jp_quote = next(
        source for source in payload["sources"] if source["source_id"] == "YAHOO_JP_QUOTE"
    )
    yahoo_jp_quote["information_type"] = "OHLCV"

    result = validate_source_matrix(payload)

    assert result.valid is False
    assert any(
        "YAHOO_JP_QUOTE must have information_type TURNOVER" in error for error in result.errors
    )


def test_yahoo_jp_quote_documents_tse_only_suffix_policy():
    """HIGH-2: the .T suffix is TSE-specific while Discovery is ALL_MARKETS.
    Until per-candidate market identity is a machine-checkable contract, the
    operational rule (never guess a suffix, never record FOUND for an
    unconfirmed venue) lives in the Source Matrix notes, so it cannot be
    dropped silently."""
    payload = load_yaml(DEFAULT_SOURCE_MATRIX_PATH)
    yahoo_jp_quote = next(
        source for source in payload["sources"] if source["source_id"] == "YAHOO_JP_QUOTE"
    )

    assert yahoo_jp_quote["url_template"].endswith("{ticker}.T")
    notes = yahoo_jp_quote["notes"]
    assert "Tokyo Stock Exchange" in notes
    assert "ALL_MARKETS" in notes
    assert "guessed" in notes


def test_discovery_market_filter_and_schema_version_are_unchanged():
    """HIGH-2 guardrails: the unresolved suffix question must not be 'fixed'
    by narrowing Discovery to TSE, and documenting the policy in notes does
    not change the Source Matrix schema version."""
    payload = load_yaml(DEFAULT_SOURCE_MATRIX_PATH)

    assert payload["default_discovery_market_filter"] == "ALL_MARKETS"
    # v3 adds the deterministic acquisition block (transport/parser binding).
    # It must not have been reached by narrowing Discovery to TSE.
    assert payload["source_matrix_schema_version"] == 3
