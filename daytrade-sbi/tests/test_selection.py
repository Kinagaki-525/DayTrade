import copy
from decimal import Decimal

import pytest

from src.config import load_strategy_config, strategy_config_sha256
from src.selection import (
    SelectionHardError,
    build_selection,
    canonicalize_decimal_ratio,
    compare_exact_ratios,
    evaluate_selection_rules,
    parse_positive_decimal,
    relative_tick_within_threshold,
    validate_rank1_integrity,
)
from tests.factories import (
    make_complete_ranking_payload,
    make_ranking_candidate,
    make_selection_config,
    make_selection_input_hashes,
)


def _config_with_selection(**kwargs):
    config = copy.deepcopy(load_strategy_config())
    config["selection"] = make_selection_config(**kwargs)
    return config


def _ranking_for(config):
    return make_complete_ranking_payload(
        strategy_version=config["strategy_version"],
        config_sha256=strategy_config_sha256(config),
    )


# --- Decimal / ratio helpers -------------------------------------------------


def test_parse_positive_decimal_rejects_bool():
    with pytest.raises(SelectionHardError, match="SELECTION_FEATURE_INVALID"):
        parse_positive_decimal(True, "x")


def test_parse_positive_decimal_rejects_non_positive():
    with pytest.raises(SelectionHardError, match="SELECTION_FEATURE_INVALID"):
        parse_positive_decimal("0", "x")
    with pytest.raises(SelectionHardError, match="SELECTION_FEATURE_INVALID"):
        parse_positive_decimal("-1", "x")


def test_parse_positive_decimal_rejects_nan_and_infinity():
    for bad in ("NaN", "Infinity", "-Infinity", ""):
        with pytest.raises(SelectionHardError, match="SELECTION_FEATURE_INVALID"):
            parse_positive_decimal(bad, "x")


def test_compare_exact_ratios_boundary_equality():
    assert compare_exact_ratios(1, 500, 1, 500) == 0
    assert compare_exact_ratios(1, 499, 1, 500) == 1
    assert compare_exact_ratios(1, 501, 1, 500) == -1


def test_canonicalize_decimal_ratio_reduces_exactly():
    num, den = canonicalize_decimal_ratio(Decimal("2"), Decimal("1000"))
    assert (num, den) == (1, 500)


def test_relative_tick_within_threshold_boundary_pass():
    assert relative_tick_within_threshold(
        numerator_yen=Decimal("1"),
        denominator_yen=Decimal("500"),
        threshold_numerator=1,
        threshold_denominator=500,
    )


def test_relative_tick_within_threshold_boundary_reject():
    assert not relative_tick_within_threshold(
        numerator_yen=Decimal("1"),
        denominator_yen=Decimal("499"),
        threshold_numerator=1,
        threshold_denominator=500,
    )


# --- Rank1 integrity ----------------------------------------------------------


def test_validate_rank1_integrity_accepts_valid_ranking():
    ranking = make_complete_ranking_payload()
    rank1 = validate_rank1_integrity(ranking)
    assert rank1["ticker"] == "1234"


def test_validate_rank1_integrity_rejects_cardinality_violation():
    candidates = [
        make_ranking_candidate(ticker="1111", final_rank=1),
        make_ranking_candidate(ticker="2222", final_rank=1),
    ]
    ranking = make_complete_ranking_payload(candidates=candidates)
    ranking["summary"]["input_count"] = 2
    ranking["summary"]["valid_input_count"] = 2
    ranking["summary"]["ranked_count"] = 2
    with pytest.raises(
        SelectionHardError,
        match="SELECTION_RANK1_CARDINALITY_INVALID|SELECTION_RANKING_CANDIDATES_INVALID",
    ):
        validate_rank1_integrity(ranking)


def test_validate_rank1_integrity_rejects_top_ranked_ticker_mismatch():
    ranking = make_complete_ranking_payload()
    ranking["summary"]["top_ranked_ticker"] = "9999"
    with pytest.raises(SelectionHardError, match="SELECTION_TOP_RANKED_TICKER_MISMATCH"):
        validate_rank1_integrity(ranking)


def test_validate_rank1_integrity_rejects_not_complete():
    ranking = make_complete_ranking_payload()
    ranking["ranking_status"] = "DATA_UNAVAILABLE"
    with pytest.raises(SelectionHardError, match="SELECTION_RANKING_NOT_COMPLETE"):
        validate_rank1_integrity(ranking)


def test_validate_rank1_integrity_rejects_feature_mismatch():
    ranking = make_complete_ranking_payload()
    ranking["candidates"][0]["feature_values"]["relative_tick_size"]["numerator_yen"] = "2"
    with pytest.raises(SelectionHardError, match="SELECTION_FEATURE_INVALID"):
        validate_rank1_integrity(ranking)


def test_validate_rank1_integrity_rejects_non_integral_turnover():
    ranking = make_complete_ranking_payload()
    ranking["candidates"][0]["feature_values"]["turnover_yen"] = "100.5"
    with pytest.raises(SelectionHardError, match="SELECTION_FEATURE_INVALID"):
        validate_rank1_integrity(ranking)


def test_validate_rank1_integrity_rejects_duplicate_tickers():
    candidates = [
        make_ranking_candidate(ticker="1234", final_rank=1),
        make_ranking_candidate(ticker="1234", final_rank=2),
    ]
    ranking = make_complete_ranking_payload(candidates=candidates)
    ranking["summary"]["input_count"] = 2
    ranking["summary"]["valid_input_count"] = 2
    ranking["summary"]["ranked_count"] = 2
    with pytest.raises(SelectionHardError, match="SELECTION_RANKING_CANDIDATES_INVALID"):
        validate_rank1_integrity(ranking)


# --- Rule evaluation ------------------------------------------------------


def test_evaluate_selection_rules_both_evaluated_no_short_circuit():
    ranking = make_complete_ranking_payload()
    rank1 = validate_rank1_integrity(ranking)
    config = make_selection_config(threshold_yen=999_999_999, threshold_numerator=1, threshold_denominator=1)
    evaluations = evaluate_selection_rules(rank1, config)
    assert len(evaluations) == 2
    assert [e["reason_code"] for e in evaluations] == ["TURNOVER", "RELATIVE_TICK"]
    assert evaluations[0]["status"] == "REJECT"
    assert evaluations[1]["status"] == "PASS"


# --- build_selection --------------------------------------------------------


def test_build_selection_selected_when_both_rules_pass():
    config = _config_with_selection()
    ranking = _ranking_for(config)
    hashes = make_selection_input_hashes()
    payload = build_selection(ranking=ranking, config=config, input_hashes=hashes)
    assert payload["selection_status"] == "SELECTED"
    assert payload["selected_ticker"] == "1234"
    assert payload["reason_codes"] == ["SELECTION_ALL_RULES_PASSED"]


def test_build_selection_no_trade_when_turnover_rejected():
    config = _config_with_selection(threshold_yen=999_999_999_999)
    ranking = _ranking_for(config)
    hashes = make_selection_input_hashes()
    payload = build_selection(ranking=ranking, config=config, input_hashes=hashes)
    assert payload["selection_status"] == "NO_TRADE"
    assert payload["selected_ticker"] is None
    assert payload["reason_codes"] == ["TURNOVER"]


def test_build_selection_no_trade_when_tick_rejected():
    config = _config_with_selection(threshold_numerator=1, threshold_denominator=100000)
    ranking = _ranking_for(config)
    hashes = make_selection_input_hashes()
    payload = build_selection(ranking=ranking, config=config, input_hashes=hashes)
    assert payload["selection_status"] == "NO_TRADE"
    assert payload["reason_codes"] == ["RELATIVE_TICK"]


def test_build_selection_no_trade_both_rejected_canonical_order():
    config = _config_with_selection(
        threshold_yen=999_999_999_999, threshold_numerator=1, threshold_denominator=100000
    )
    ranking = _ranking_for(config)
    hashes = make_selection_input_hashes()
    payload = build_selection(ranking=ranking, config=config, input_hashes=hashes)
    assert payload["reason_codes"] == ["TURNOVER", "RELATIVE_TICK"]


def test_build_selection_rejects_when_disabled():
    config = _config_with_selection(enabled=False)
    ranking = _ranking_for(config)
    hashes = make_selection_input_hashes()
    with pytest.raises(SelectionHardError, match="SELECTION_CONFIG_DISABLED"):
        build_selection(ranking=ranking, config=config, input_hashes=hashes)


def test_build_selection_rejects_hash_chain_mismatch():
    config = _config_with_selection()
    ranking = _ranking_for(config)
    hashes = make_selection_input_hashes(strategy_snapshot_sha256="9" * 64)
    with pytest.raises(SelectionHardError, match="SELECTION_HASH_CHAIN_MISMATCH"):
        build_selection(ranking=ranking, config=config, input_hashes=hashes)


def test_build_selection_does_not_mutate_inputs():
    config = _config_with_selection()
    ranking = _ranking_for(config)
    hashes = make_selection_input_hashes()
    ranking_before = copy.deepcopy(ranking)
    config_before = copy.deepcopy(config)
    hashes_before = copy.deepcopy(hashes)
    build_selection(ranking=ranking, config=config, input_hashes=hashes)
    assert ranking == ranking_before
    assert config == config_before
    assert hashes == hashes_before


def test_build_selection_is_deterministic_except_generated_at():
    config = _config_with_selection()
    ranking = _ranking_for(config)
    hashes = make_selection_input_hashes()
    first = build_selection(ranking=ranking, config=config, input_hashes=hashes)
    second = build_selection(ranking=ranking, config=config, input_hashes=hashes)
    first.pop("generated_at")
    second.pop("generated_at")
    assert first == second


def test_build_selection_no_fallback_to_rank2_when_rank1_rejected():
    """A strong Rank 2 candidate must never become selected_ticker."""
    candidates = [
        make_ranking_candidate(
            ticker="1111",
            final_rank=1,
            turnover_yen="1",  # will fail the turnover rule
        ),
        make_ranking_candidate(
            ticker="2222",
            final_rank=2,
            turnover_yen="999999999",  # would easily pass if it were evaluated
        ),
    ]
    config = _config_with_selection(threshold_yen=1000)
    ranking = make_complete_ranking_payload(
        strategy_version=config["strategy_version"],
        config_sha256=strategy_config_sha256(config),
        candidates=candidates,
    )
    ranking["summary"]["input_count"] = 2
    ranking["summary"]["valid_input_count"] = 2
    ranking["summary"]["ranked_count"] = 2
    hashes = make_selection_input_hashes()
    payload = build_selection(ranking=ranking, config=config, input_hashes=hashes)
    assert payload["selection_status"] == "NO_TRADE"
    assert payload["selected_ticker"] is None
    assert payload["rank1_ticker"] == "1111"
