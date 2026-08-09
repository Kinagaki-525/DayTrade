from copy import deepcopy

from src.config import load_yaml
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH, validate_source_matrix


def test_default_source_matrix_is_valid():
    payload = load_yaml(DEFAULT_SOURCE_MATRIX_PATH)

    result = validate_source_matrix(payload)

    assert result.valid is True
    assert result.errors == ()


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
