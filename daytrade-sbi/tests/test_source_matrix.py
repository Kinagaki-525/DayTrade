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
