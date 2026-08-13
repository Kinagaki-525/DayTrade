"""Source Matrix v3 validation and v2 backward compatibility."""

from __future__ import annotations

import copy

import pytest

from src.config import load_yaml
from src.contracts import validate_json_document
from src.source_matrix import (
    AI_CLASSIFICATION_SOURCE_IDS,
    DEFAULT_SOURCE_MATRIX_PATH,
    ISSUER_SCOPED_SOURCE_IDS,
    is_v3,
    load_source_matrix,
    source_by_id,
    validate_source_matrix,
)


MATRIX = load_source_matrix()


def _mutated(source_id: str, **acquisition_changes):
    payload = copy.deepcopy(MATRIX)
    for definition in payload["sources"]:
        if definition["source_id"] == source_id:
            if acquisition_changes.get("_drop"):
                definition.pop("acquisition")
            else:
                definition["acquisition"].update(acquisition_changes)
    return payload


def test_shipped_matrix_is_v3_and_valid():
    assert is_v3(MATRIX)
    assert validate_source_matrix(MATRIX).valid
    assert MATRIX["market_research_version"] == "market-research-v2"
    assert MATRIX["source_change_policy"]["runtime_substitution_allowed"] is False


def test_every_source_declares_an_acquisition_block():
    for definition in MATRIX["sources"]:
        acquisition = definition["acquisition"]
        assert acquisition["transport"] == "HTTP_GET"
        assert acquisition["mode"] in {
            "DETERMINISTIC",
            "DETERMINISTIC_THEN_AI_CLASSIFICATION",
        }


def test_missing_acquisition_block_is_rejected_in_v3():
    result = validate_source_matrix(_mutated("YAHOO_JP_QUOTE", _drop=True))
    assert not result.valid
    assert any("acquisition" in error for error in result.errors)


def test_unknown_mode_is_rejected():
    result = validate_source_matrix(_mutated("YAHOO_JP_QUOTE", mode="AI_ONLY"))
    assert not result.valid


def test_unknown_transport_is_rejected():
    result = validate_source_matrix(_mutated("YAHOO_JP_QUOTE", transport="HTTP_POST"))
    assert not result.valid


def test_invalid_network_scope_is_rejected():
    result = validate_source_matrix(_mutated("YAHOO_JP_QUOTE", network_scope="ANY_HOST"))
    assert not result.valid


def test_issuer_scope_may_not_be_claimed_by_a_non_issuer_source():
    result = validate_source_matrix(
        _mutated("YAHOO_JP_QUOTE", network_scope="ISSUER_DOMAIN_REGISTRY")
    )
    assert not result.valid


def test_parser_mismatch_is_rejected():
    result = validate_source_matrix(_mutated("YAHOO_JP_QUOTE", parser="jpx.calendar"))
    assert not result.valid
    assert any("SOURCE_PARSER_MISMATCH" in error for error in result.errors)


def test_ai_classification_is_limited_to_the_four_event_sources():
    assert len(AI_CLASSIFICATION_SOURCE_IDS) == 4
    definitions = source_by_id(MATRIX)
    for source_id, definition in definitions.items():
        mode = definition["acquisition"]["mode"]
        expected = (
            "DETERMINISTIC_THEN_AI_CLASSIFICATION"
            if source_id in AI_CLASSIFICATION_SOURCE_IDS
            else "DETERMINISTIC"
        )
        assert mode == expected, source_id


def test_ai_classification_cannot_be_granted_to_a_market_data_source():
    result = validate_source_matrix(
        _mutated("YAHOO_JP_HISTORY", mode="DETERMINISTIC_THEN_AI_CLASSIFICATION")
    )
    assert not result.valid
    assert any("AI classification is not permitted" in error for error in result.errors)


def test_issuer_sources_use_the_registry_scope():
    definitions = source_by_id(MATRIX)
    for source_id in ISSUER_SCOPED_SOURCE_IDS:
        assert definitions[source_id]["acquisition"]["network_scope"] == "ISSUER_DOMAIN_REGISTRY"


def test_runtime_substitution_cannot_be_enabled():
    payload = copy.deepcopy(MATRIX)
    payload["source_change_policy"]["runtime_substitution_allowed"] = True
    result = validate_source_matrix(payload)
    assert not result.valid


# ------------------------------------------------------ v2 compatibility ---


def _as_v2(payload: dict) -> dict:
    downgraded = copy.deepcopy(payload)
    downgraded["source_matrix_schema_version"] = 2
    for definition in downgraded["sources"]:
        definition.pop("acquisition", None)
    return downgraded


def test_a_v2_matrix_still_loads_and_validates():
    v2 = _as_v2(MATRIX)
    validate_json_document(v2, "source_matrix.schema.json")
    assert validate_source_matrix(v2).valid
    assert not is_v3(v2)


def test_v2_documents_are_not_forced_to_declare_acquisition():
    v2 = _as_v2(MATRIX)
    assert all("acquisition" not in d for d in v2["sources"])
    assert validate_source_matrix(v2).valid


def test_v2_schema_still_rejects_what_it_always_rejected():
    v2 = _as_v2(MATRIX)
    v2["market_research_version"] = "market-research-v3"
    with pytest.raises(ValueError):
        validate_json_document(v2, "source_matrix.schema.json")


def test_raw_yaml_is_loadable_by_the_legacy_path():
    payload = load_yaml(DEFAULT_SOURCE_MATRIX_PATH)
    assert payload["source_matrix_schema_version"] == 3
