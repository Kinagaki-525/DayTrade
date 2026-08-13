from __future__ import annotations

import copy
import hashlib
import json

import pytest
import yaml

from src import cli
from src.config import DEFAULT_CONFIG_PATH, load_strategy_config, strategy_config_sha256
from src.contracts import RUN_ARTIFACT_ALLOWLIST
from src.source_matrix import DEFAULT_SOURCE_MATRIX_PATH

from tests.test_cli import complete_pipeline_summary
from tests.test_ranking import (
    PREVIOUS_TRADING_DAY,
    TARGET_DATE,
    _build_full_case,
    _write_json_file as _write_ranking_json_file,
    _write_ranking_inputs,
)


def _full_case_for_config(config, config_path, specs):
    """_build_full_case() bakes in the default config's config_sha256 /
    strategy_snapshot_sha256; re-point those at the (selection-enabled)
    config actually used by these tests before writing/hashing."""
    event_gate, candidates, market_data, sources = _build_full_case(specs)
    config_sha256 = strategy_config_sha256(config)
    event_gate["config_sha256"] = config_sha256
    candidates["config_sha256"] = config_sha256
    event_gate["input_hashes"]["strategy_snapshot_sha256"] = hashlib.sha256(
        config_path.read_bytes()
    ).hexdigest()
    return event_gate, candidates, market_data, sources


def _write_json_file(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _enabled_selection_config_path(tmp_path, *, minimum_turnover_yen=1000):
    config = copy.deepcopy(load_strategy_config())
    config["selection"] = {
        "enabled": True,
        "version": "selection-v1",
        "candidate_policy": "rank1_only",
        "fallback_policy": "none",
        "rule_logic": "all",
        "missing_data_policy": "fail_closed",
        "rules": {
            "minimum_turnover_yen": {"operator": ">=", "threshold_yen": minimum_turnover_yen},
            "maximum_relative_tick_size": {
                "operator": "<=",
                "threshold_ratio": {"numerator": 1, "denominator": 1},
            },
        },
    }
    path = tmp_path / "strategy.yaml"
    path.write_text(yaml.safe_dump(config, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path, config


def _build_real_ranking_chain(tmp_path, config_path, config, specs):
    """Build a genuine, hash-consistent ranking.json via the real
    build-ranking CLI (not a hand-forged ranking payload),
    so build-selection's Ranking Trust Chain re-verification has real
    upstream artifacts to check against. Returns a dict of all the paths
    involved, ready to be passed straight into a build-selection argv."""
    event_gate, candidates, market_data, sources = _full_case_for_config(config, config_path, specs)
    event_gate_path, candidates_path, market_data_path, sources_path = _write_ranking_inputs(
        tmp_path, event_gate, candidates, market_data, sources
    )
    ranking_path = tmp_path / "ranking.json"
    assert (
        cli.main(
            [
                "build-ranking",
                "--event-gate",
                str(event_gate_path),
                "--candidates",
                str(candidates_path),
                "--market-data",
                str(market_data_path),
                "--sources",
                str(sources_path),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(ranking_path),
            ]
        )
        == 0
    )
    return {
        "ranking_path": ranking_path,
        "event_gate_path": event_gate_path,
        "candidates_path": candidates_path,
        "market_data_path": market_data_path,
        "sources_path": sources_path,
        "config_path": config_path,
    }


def _build_selection_argv(paths, output_path, *, ranking_path=None, source_matrix_path=None, config_path=None):
    return [
        "build-selection",
        "--ranking",
        str(ranking_path or paths["ranking_path"]),
        "--event-gate",
        str(paths["event_gate_path"]),
        "--candidates",
        str(paths["candidates_path"]),
        "--market-data",
        str(paths["market_data_path"]),
        "--sources",
        str(paths["sources_path"]),
        "--source-matrix",
        str(source_matrix_path or DEFAULT_SOURCE_MATRIX_PATH),
        "--config",
        str(config_path or paths["config_path"]),
        "--output",
        str(output_path),
    ]


def test_cli_build_selection_selected(tmp_path):
    config_path, config = _enabled_selection_config_path(tmp_path)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    output_path = tmp_path / "selection.json"

    result = cli.main(_build_selection_argv(paths, output_path))

    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["selection_status"] == "SELECTED"
    assert payload["selected_ticker"] == "1234"
    assert "selection.json" in RUN_ARTIFACT_ALLOWLIST


def test_cli_build_selection_disabled_config_produces_no_output(tmp_path):
    config = load_strategy_config()
    paths = _build_real_ranking_chain(
        tmp_path,
        DEFAULT_CONFIG_PATH,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    output_path = tmp_path / "selection.json"

    raised = False
    try:
        cli.main(_build_selection_argv(paths, output_path))
    except ValueError as exc:
        raised = True
        assert "SELECTION_CONFIG_DISABLED" in str(exc)
    assert raised
    assert not output_path.exists()


def test_cli_build_selection_no_partial_output_on_hard_error(tmp_path):
    """A schema-valid but hand-tampered ranking.json (raw SHA256 no longer
    matching ranking.input_hashes.strategy_snapshot_sha256) must Hard Error
    via the Ranking Trust Chain re-verification -- selection.json must not
    be produced."""
    config_path, config = _enabled_selection_config_path(tmp_path)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    ranking = json.loads(paths["ranking_path"].read_text(encoding="utf-8"))
    ranking["input_hashes"]["strategy_snapshot_sha256"] = "0" * 64
    tampered_ranking_path = tmp_path / "tampered_ranking.json"
    _write_json_file(tampered_ranking_path, ranking)
    output_path = tmp_path / "selection.json"

    raised = False
    try:
        cli.main(_build_selection_argv(paths, output_path, ranking_path=tampered_ranking_path))
    except ValueError as exc:
        raised = True
        assert "RANKING_INPUT_HASHES_MISMATCH" in str(exc)
    assert raised
    assert not output_path.exists()


def test_cli_build_selection_no_partial_overwrite_on_hard_error(tmp_path):
    """A Hard Error must never overwrite (even partially) a pre-existing
    output file: byte-for-byte, not just 'file still exists'."""
    config_path, config = _enabled_selection_config_path(tmp_path)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    ranking = json.loads(paths["ranking_path"].read_text(encoding="utf-8"))
    ranking["input_hashes"]["strategy_snapshot_sha256"] = "0" * 64
    tampered_ranking_path = tmp_path / "tampered_ranking.json"
    _write_json_file(tampered_ranking_path, ranking)
    output_path = tmp_path / "selection.json"
    sentinel_bytes = b"SENTINEL: pre-existing selection.json content, must survive untouched\n"
    output_path.write_bytes(sentinel_bytes)

    with pytest.raises(ValueError):
        cli.main(_build_selection_argv(paths, output_path, ranking_path=tampered_ranking_path))
    assert output_path.read_bytes() == sentinel_bytes


# ---------------------------------------------------------------------------
# Case C Ranking Trust Chain: build-selection must fully re-verify
# ranking.json's provenance before ever calling build_selection().
# ---------------------------------------------------------------------------


def test_cli_build_selection_full_chain_matches_real_selection_rules(tmp_path):
    """Full happy path: real build-ranking -> real build-selection. Asserts
    evaluated_ticker is the genuine Rank1 ticker and selected_ticker /
    selection_status match what the Selection rules legitimately produce
    for that Rank1's real feature values."""
    config_path, config = _enabled_selection_config_path(tmp_path)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [
            {"ticker": "AA01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
            {"ticker": "AA02", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"},
        ],
    )
    ranking = json.loads(paths["ranking_path"].read_text(encoding="utf-8"))
    rank1_ticker = next(c["ticker"] for c in ranking["candidates"] if c["final_rank"] == 1)

    output_path = tmp_path / "selection.json"
    assert cli.main(_build_selection_argv(paths, output_path)) == 0
    selection = json.loads(output_path.read_text(encoding="utf-8"))
    assert selection["evaluated_ticker"] == rank1_ticker
    assert selection["selection_status"] == "SELECTED"
    assert selection["selected_ticker"] == rank1_ticker


@pytest.mark.parametrize(
    "target_file",
    ["event_gate", "candidates", "market_data", "sources", "source_matrix", "config"],
)
def test_cli_build_selection_upstream_schema_valid_mutation_is_hard_error(tmp_path, target_file):
    """Raw-byte-mutating (in a schema-valid way) exactly one of the six
    claimed Ranking inputs, after ranking.json was generated, must Hard
    Error via the hash check -- proving the check is real, not a no-op --
    and must not produce selection.json."""
    config_path, config = _enabled_selection_config_path(tmp_path)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )

    if target_file == "source_matrix":
        # Can't mutate the real repo config/source_matrix.yaml in place;
        # copy it locally and point the argv at the mutated copy instead.
        target_path = tmp_path / "source_matrix_mutated.yaml"
        target_path.write_bytes(DEFAULT_SOURCE_MATRIX_PATH.read_bytes())
    else:
        path_key = "config_path" if target_file == "config" else f"{target_file}_path"
        target_path = paths[path_key]

    original_bytes = target_path.read_bytes()
    if target_file == "config":
        # A schema-valid mutation: append a harmless trailing comment line.
        mutated_bytes = original_bytes + b"\n# mutated for test\n"
    elif target_file == "source_matrix":
        mutated_bytes = original_bytes + b"\n# mutated for test\n"
    else:
        payload = json.loads(original_bytes.decode("utf-8"))
        # Any schema-valid mutation that changes raw bytes: bump generated_at
        # if present, else fall back to a comment-safe JSON round-trip with
        # different key ordering (still changes raw bytes).
        if "generated_at" in payload:
            payload["generated_at"] = "2099-01-01T00:00:00+00:00"
        mutated_bytes = (json.dumps(payload, ensure_ascii=False, indent=4) + "\n").encode("utf-8")
        assert mutated_bytes != original_bytes

    target_path.write_bytes(mutated_bytes)

    output_path = tmp_path / "selection.json"
    argv_paths = dict(paths)
    source_matrix_arg = target_path if target_file == "source_matrix" else None
    with pytest.raises(ValueError, match="RANKING_INPUT_HASHES_MISMATCH"):
        cli.main(_build_selection_argv(argv_paths, output_path, source_matrix_path=source_matrix_arg))
    assert not output_path.exists()


def test_cli_build_selection_semantic_equivalent_raw_byte_mutation_is_hard_error(tmp_path):
    """A raw-byte change (trailing whitespace) that does NOT change the
    parsed/semantic content of an upstream file must still Hard Error via
    the hash mismatch -- proving hash verification is byte-provenance-based,
    not semantic-equivalence-based."""
    config_path, config = _enabled_selection_config_path(tmp_path)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    market_data_path = paths["market_data_path"]
    original_bytes = market_data_path.read_bytes()
    # Trailing whitespace: json.loads() parses this identically to the
    # original, so the *semantic* content is unchanged, but the raw bytes
    # (and therefore the SHA256) differ.
    market_data_path.write_bytes(original_bytes + b"   \n")
    assert json.loads(market_data_path.read_text(encoding="utf-8")) == json.loads(
        original_bytes.decode("utf-8")
    )

    output_path = tmp_path / "selection.json"
    with pytest.raises(ValueError, match="RANKING_INPUT_HASHES_MISMATCH"):
        cli.main(_build_selection_argv(paths, output_path))
    assert not output_path.exists()


def test_cli_build_selection_forged_ranking_is_hard_error(tmp_path):
    """A schema-valid but hand-tampered ranking.json (a field the existing
    Ranking Contract functions will catch) must Hard Error via
    validate_ranking_preconditions/validate_ranking_output_contract even
    though the upstream artifacts are left completely untouched -- proving
    build-selection never trusts ranking.json's own claims about the world
    without re-deriving them."""
    config_path, config = _enabled_selection_config_path(tmp_path)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    ranking = json.loads(paths["ranking_path"].read_text(encoding="utf-8"))
    # Flip the real Rank1's final_rank so it no longer matches what the
    # (untouched) upstream artifacts actually justify.
    for candidate in ranking["candidates"]:
        if candidate["final_rank"] == 1:
            candidate["final_rank"] = 2
    ranking["summary"]["top_ranked_ticker"] = "DOES_NOT_EXIST"
    tampered_ranking_path = tmp_path / "forged_ranking.json"
    _write_json_file(tampered_ranking_path, ranking)

    output_path = tmp_path / "selection.json"
    with pytest.raises(ValueError):
        cli.main(_build_selection_argv(paths, output_path, ranking_path=tampered_ranking_path))
    assert not output_path.exists()


# ---------------------------------------------------------------------------
# P0-2 / P0-3: build-selection-recommendation trust-chain tests.
# ---------------------------------------------------------------------------


def _full_v6_chain(tmp_path, *, ticker: str = "AA01"):
    """Build a complete, real, self-consistent Config v6 chain: ranking.json
    -> selection.json (SELECTED) -> recommendation.json (v2, TRADE), all via
    the real CLI commands over real files on disk, so tests exercise the
    genuine trust chain rather than a monkeypatched shortcut."""
    config_path, config = _enabled_selection_config_path(tmp_path)
    event_gate, candidates, market_data, sources = _full_case_for_config(
        config,
        config_path,
        [{"ticker": ticker, "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    event_gate_path, candidates_path, market_data_path, sources_path = _write_ranking_inputs(
        tmp_path, event_gate, candidates, market_data, sources
    )

    ranking_path = tmp_path / "ranking.json"
    assert (
        cli.main(
            [
                "build-ranking",
                "--event-gate",
                str(event_gate_path),
                "--candidates",
                str(candidates_path),
                "--market-data",
                str(market_data_path),
                "--sources",
                str(sources_path),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(ranking_path),
            ]
        )
        == 0
    )

    selection_path = tmp_path / "selection.json"
    assert (
        cli.main(
            [
                "build-selection",
                "--ranking",
                str(ranking_path),
                "--event-gate",
                str(event_gate_path),
                "--candidates",
                str(candidates_path),
                "--market-data",
                str(market_data_path),
                "--sources",
                str(sources_path),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(selection_path),
            ]
        )
        == 0
    )
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    assert selection["selection_status"] == "SELECTED"

    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    _write_ranking_json_file(
        candidate_pipeline_path,
        {
            "schema_version": 1,
            "target_date": TARGET_DATE,
            "generated_at": "2026-08-09T21:30:00+00:00",
            "strategy_version": config["strategy_version"],
            "config_sha256": strategy_config_sha256(config),
            "summary": complete_pipeline_summary(),
            "candidates": [],
        },
    )

    research_window_path = tmp_path / "research_window.json"
    _write_ranking_json_file(
        research_window_path,
        {
            "schema_version": 1,
            "target_date": TARGET_DATE,
            "previous_trading_day": PREVIOUS_TRADING_DAY,
            "research_cutoff": "2026-08-09T21:00:00+00:00",
            "post_cutoff_information_status": "NO_NON_BUSINESS_GAP",
            "research_window": {
                "run_type": "FIRST_RUN",
                "window_start": "2026-08-09T00:00:00+00:00",
                "window_end": "2026-08-09T21:00:00+00:00",
                "previous_research_cutoff": None,
                "previous_run_date": None,
                "bootstrap_lookback_days": 5,
            },
        },
    )

    recommendation_path = tmp_path / "recommendation.json"
    assert (
        cli.main(
            [
                "build-selection-recommendation",
                "--ranking",
                str(ranking_path),
                "--selection",
                str(selection_path),
                "--event-gate",
                str(event_gate_path),
                "--candidates",
                str(candidates_path),
                "--candidate-pipeline",
                str(candidate_pipeline_path),
                "--market-data",
                str(market_data_path),
                "--research-window",
                str(research_window_path),
                "--sources",
                str(sources_path),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(recommendation_path),
            ]
        )
        == 0
    )

    return {
        "config_path": config_path,
        "config": config,
        "ranking_path": ranking_path,
        "selection_path": selection_path,
        "event_gate_path": event_gate_path,
        "candidates_path": candidates_path,
        "candidate_pipeline_path": candidate_pipeline_path,
        "market_data_path": market_data_path,
        "research_window_path": research_window_path,
        "sources_path": sources_path,
        "recommendation_path": recommendation_path,
    }


def test_cli_build_selection_recommendation_full_chain_succeeds(tmp_path):
    chain = _full_v6_chain(tmp_path)
    recommendation = json.loads(chain["recommendation_path"].read_text(encoding="utf-8"))
    assert recommendation["schema_version"] == 2
    assert recommendation["decision"] == "TRADE"
    assert recommendation["ticker"] == "AA01"


def test_cli_build_selection_recommendation_forged_selection_is_hard_error(tmp_path):
    """The critical regression test: a real ranking.json where Rank 1 would
    legitimately fail Selection's rules (NO_TRADE), paired with a
    hand-forged selection.json that claims SELECTED and fabricates
    self-consistent input_hashes/reason_codes. build-selection-recommendation
    must raise a Hard Error via validate_selection_output_contract rather
    than ever producing a TRADE recommendation."""
    # Turnover (raw_value "1" thousand-yen -> 1000 yen canonical) far below
    # the configured threshold_yen -> real Selection rules legitimately
    # reject Rank 1.
    config_path, config = _enabled_selection_config_path(tmp_path, minimum_turnover_yen=1_000_000)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "1"}],
    )
    ranking_path = paths["ranking_path"]

    ranking_sha256 = hashlib.sha256(ranking_path.read_bytes()).hexdigest()
    strategy_snapshot_sha256 = hashlib.sha256(config_path.read_bytes()).hexdigest()

    # Ground truth: what Selection would really produce for this ranking.
    real_selection_path = tmp_path / "real_selection.json"
    assert cli.main(_build_selection_argv(paths, real_selection_path)) == 0
    real_selection = json.loads(real_selection_path.read_text(encoding="utf-8"))
    assert real_selection["selection_status"] == "NO_TRADE"

    # Forge a selection.json that claims SELECTED, with self-consistent
    # (correctly computed) input_hashes so the attacker also faked the hash
    # check -- only the business content (status/reason_codes/rule
    # evaluations) is wrong.
    forged_selection = copy.deepcopy(real_selection)
    forged_selection["selection_status"] = "SELECTED"
    forged_selection["selected_ticker"] = "1234"
    forged_selection["reason_codes"] = ["SELECTION_ALL_RULES_PASSED"]
    for rule in forged_selection["rule_evaluations"]:
        rule["result"] = "PASS"
        rule["reason_code"] = None
    assert forged_selection["input_hashes"] == {
        "ranking_sha256": ranking_sha256,
        "strategy_snapshot_sha256": strategy_snapshot_sha256,
    }
    forged_selection_path = tmp_path / "selection.json"
    _write_json_file(forged_selection_path, forged_selection)

    output_path = tmp_path / "recommendation.json"
    with pytest.raises(ValueError, match="SELECTION_OUTPUT_CONTRACT_MISMATCH"):
        cli.main(
            [
                "build-selection-recommendation",
                "--ranking",
                str(ranking_path),
                "--selection",
                str(forged_selection_path),
                "--event-gate",
                str(paths["event_gate_path"]),
                "--candidates",
                str(tmp_path / "candidates.json"),
                "--candidate-pipeline",
                str(tmp_path / "candidate_pipeline.json"),
                "--market-data",
                str(tmp_path / "market_data.json"),
                "--research-window",
                str(tmp_path / "research_window.json"),
                "--sources",
                str(tmp_path / "sources.json"),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_build_selection_recommendation_input_hashes_mismatch_is_hard_error(tmp_path):
    config_path, config = _enabled_selection_config_path(tmp_path)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    ranking_path = paths["ranking_path"]

    real_selection_path = tmp_path / "real_selection.json"
    assert cli.main(_build_selection_argv(paths, real_selection_path)) == 0
    tampered = json.loads(real_selection_path.read_text(encoding="utf-8"))
    tampered["input_hashes"]["ranking_sha256"] = "9" * 64
    tampered_path = tmp_path / "selection.json"
    _write_json_file(tampered_path, tampered)

    output_path = tmp_path / "recommendation.json"
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_INPUT_HASHES_MISMATCH"):
        cli.main(
            [
                "build-selection-recommendation",
                "--ranking",
                str(ranking_path),
                "--selection",
                str(tampered_path),
                "--event-gate",
                str(paths["event_gate_path"]),
                "--candidates",
                str(tmp_path / "candidates.json"),
                "--candidate-pipeline",
                str(tmp_path / "candidate_pipeline.json"),
                "--market-data",
                str(tmp_path / "market_data.json"),
                "--research-window",
                str(tmp_path / "research_window.json"),
                "--sources",
                str(tmp_path / "sources.json"),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(output_path),
            ]
        )
    assert not output_path.exists()


def test_cli_build_selection_recommendation_forged_ranking_with_self_consistent_selection_hash_is_hard_error(
    tmp_path,
):
    """The single highest-value negative test in this slice: an attacker
    tampers ranking.json's business content (flips Rank 1's final_rank) AND
    also 'fixes up' selection.json's input_hashes.ranking_sha256 to match the
    tampered ranking's new actual SHA256, so Selection's own hash chain looks
    perfectly self-consistent. build-selection-recommendation must still
    Hard Error, because load_and_verify_ranking_trust_chain() re-verifies
    ranking.json against its OWN claimed upstream artifacts (event_gate/
    candidates/market_data/sources/source_matrix/config) BEFORE Selection's
    hash chain is even examined -- proving 'attacker made everything
    downstream self-consistent' is not sufficient."""
    config_path, config = _enabled_selection_config_path(tmp_path)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"}],
    )
    ranking_path = paths["ranking_path"]

    real_selection_path = tmp_path / "real_selection.json"
    assert cli.main(_build_selection_argv(paths, real_selection_path)) == 0
    real_selection = json.loads(real_selection_path.read_text(encoding="utf-8"))

    # Tamper ranking.json's business content (never touching the upstream
    # event_gate/candidates/market_data/sources/config files themselves).
    forged_ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    for candidate in forged_ranking["candidates"]:
        if candidate["final_rank"] == 1:
            candidate["final_rank"] = 2
    forged_ranking["summary"]["top_ranked_ticker"] = "DOES_NOT_EXIST"
    forged_ranking_path = tmp_path / "forged_ranking.json"
    _write_json_file(forged_ranking_path, forged_ranking)
    forged_ranking_sha256 = hashlib.sha256(forged_ranking_path.read_bytes()).hexdigest()
    assert forged_ranking_sha256 != hashlib.sha256(ranking_path.read_bytes()).hexdigest()

    # Attacker also "fixes up" selection.json's ranking_sha256 to match the
    # forged ranking.json's new actual hash, so Selection's own hash chain
    # remains self-consistent with the forged ranking.
    forged_selection = copy.deepcopy(real_selection)
    forged_selection["input_hashes"]["ranking_sha256"] = forged_ranking_sha256
    forged_selection_path = tmp_path / "selection.json"
    _write_json_file(forged_selection_path, forged_selection)

    output_path = tmp_path / "recommendation.json"
    with pytest.raises(ValueError) as exc_info:
        cli.main(
            [
                "build-selection-recommendation",
                "--ranking",
                str(forged_ranking_path),
                "--selection",
                str(forged_selection_path),
                "--event-gate",
                str(paths["event_gate_path"]),
                "--candidates",
                str(paths["candidates_path"]),
                "--candidate-pipeline",
                str(tmp_path / "candidate_pipeline.json"),
                "--market-data",
                str(paths["market_data_path"]),
                "--research-window",
                str(tmp_path / "research_window.json"),
                "--sources",
                str(paths["sources_path"]),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(output_path),
            ]
        )
    # The failure must come from the Ranking Trust Chain re-verification
    # (validate_ranking_output_contract, run inside
    # load_and_verify_ranking_trust_chain), NOT from a Selection-level check
    # -- proving Ranking is re-verified before Selection's hash chain is
    # even consulted.
    assert "SELECTION_" not in str(exc_info.value)
    assert not output_path.exists()


# ---------------------------------------------------------------------------
# P1-4: broad cross-artifact validation for build-selection-recommendation.
# ---------------------------------------------------------------------------


def _run_selection_recommendation(chain, tmp_path, *, overrides=None, output_name="recommendation2.json"):
    """Re-run build-selection-recommendation over chain's artifacts, with an
    optional {arg_flag: replacement_path} override for one input file."""
    argv = [
        "build-selection-recommendation",
        "--ranking",
        str(chain["ranking_path"]),
        "--selection",
        str(chain["selection_path"]),
        "--event-gate",
        str(chain["event_gate_path"]),
        "--candidates",
        str(chain["candidates_path"]),
        "--candidate-pipeline",
        str(chain["candidate_pipeline_path"]),
        "--market-data",
        str(chain["market_data_path"]),
        "--research-window",
        str(chain["research_window_path"]),
        "--sources",
        str(chain["sources_path"]),
        "--source-matrix",
        str(DEFAULT_SOURCE_MATRIX_PATH),
        "--config",
        str(chain["config_path"]),
        "--output",
        str(tmp_path / output_name),
    ]
    if overrides:
        for flag, replacement_path in overrides.items():
            index = argv.index(flag)
            argv[index + 1] = str(replacement_path)
    return cli.main(argv)


def _tamper_json_file(src_path, tmp_path, name, mutate):
    payload = json.loads(src_path.read_text(encoding="utf-8"))
    mutate(payload)
    tampered_path = tmp_path / name
    _write_json_file(tampered_path, payload)
    return tampered_path


# Note: candidates.json / market_data.json / sources.json are now also
# Ranking's own claimed upstream inputs (re-verified by
# load_and_verify_ranking_trust_chain, which build-selection-recommendation
# now runs FIRST, before its own P1-4 cross-artifact checks). Mutating one
# of them is therefore caught earlier, by the Ranking Trust Chain's raw-byte
# hash/target_date re-verification, rather than by P1-4 -- both are
# legitimate Hard Errors, so these tests accept either.
_RANKING_TRUST_CHAIN_ERRORS = "RANKING_INPUT_HASHES_MISMATCH|RANKING_TARGET_DATE_MISMATCH"


def test_selection_recommendation_candidates_target_date_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = _tamper_json_file(
        chain["candidates_path"], tmp_path, "bad_candidates.json", lambda p: p.__setitem__("target_date", "2099-01-01")
    )
    with pytest.raises(
        ValueError,
        match=f"SELECTION_RECOMMENDATION_TARGET_DATE_MISMATCH|{_RANKING_TRUST_CHAIN_ERRORS}",
    ):
        _run_selection_recommendation(chain, tmp_path, overrides={"--candidates": tampered})


def test_selection_recommendation_candidate_pipeline_target_date_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = _tamper_json_file(
        chain["candidate_pipeline_path"],
        tmp_path,
        "bad_pipeline.json",
        lambda p: p.__setitem__("target_date", "2099-01-01"),
    )
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_TARGET_DATE_MISMATCH"):
        _run_selection_recommendation(chain, tmp_path, overrides={"--candidate-pipeline": tampered})


def test_selection_recommendation_market_data_target_date_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = _tamper_json_file(
        chain["market_data_path"], tmp_path, "bad_market_data.json", lambda p: p.__setitem__("target_date", "2099-01-01")
    )
    with pytest.raises(
        ValueError,
        match=f"SELECTION_RECOMMENDATION_TARGET_DATE_MISMATCH|{_RANKING_TRUST_CHAIN_ERRORS}",
    ):
        _run_selection_recommendation(chain, tmp_path, overrides={"--market-data": tampered})


def test_selection_recommendation_research_window_target_date_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = _tamper_json_file(
        chain["research_window_path"],
        tmp_path,
        "bad_research_window.json",
        lambda p: p.__setitem__("target_date", "2099-01-01"),
    )
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_TARGET_DATE_MISMATCH"):
        _run_selection_recommendation(chain, tmp_path, overrides={"--research-window": tampered})


def test_selection_recommendation_sources_target_date_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = _tamper_json_file(
        chain["sources_path"], tmp_path, "bad_sources.json", lambda p: p.__setitem__("target_date", "2099-01-01")
    )
    with pytest.raises(
        ValueError,
        match=f"SELECTION_RECOMMENDATION_TARGET_DATE_MISMATCH|{_RANKING_TRUST_CHAIN_ERRORS}",
    ):
        _run_selection_recommendation(chain, tmp_path, overrides={"--sources": tampered})


def test_selection_recommendation_ranking_target_date_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    # ranking.target_date differing from selection/candidates/etc. is caught
    # by the P0-2 output-contract recompute before P1-4 even runs; both are
    # legitimate Hard Errors here, so accept either error code.
    tampered = _tamper_json_file(
        chain["ranking_path"], tmp_path, "bad_ranking.json", lambda p: p.__setitem__("target_date", "2099-01-01")
    )
    with pytest.raises(
        ValueError,
        match="SELECTION_RECOMMENDATION_TARGET_DATE_MISMATCH|SELECTION_OUTPUT_CONTRACT_MISMATCH|"
        f"SELECTION_RECOMMENDATION_INPUT_HASHES_MISMATCH|{_RANKING_TRUST_CHAIN_ERRORS}",
    ):
        _run_selection_recommendation(chain, tmp_path, overrides={"--ranking": tampered})


def test_selection_recommendation_strategy_version_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = _tamper_json_file(
        chain["candidates_path"], tmp_path, "bad_candidates_sv.json", lambda p: p.__setitem__("strategy_version", "other-v")
    )
    with pytest.raises(
        ValueError,
        match=f"SELECTION_RECOMMENDATION_STRATEGY_VERSION_MISMATCH|{_RANKING_TRUST_CHAIN_ERRORS}",
    ):
        _run_selection_recommendation(chain, tmp_path, overrides={"--candidates": tampered})


def test_selection_recommendation_config_sha256_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = _tamper_json_file(
        chain["candidate_pipeline_path"], tmp_path, "bad_pipeline_cs.json", lambda p: p.__setitem__("config_sha256", "9" * 64)
    )
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_CONFIG_SHA256_MISMATCH"):
        _run_selection_recommendation(chain, tmp_path, overrides={"--candidate-pipeline": tampered})


def test_selection_recommendation_pipeline_not_complete_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = _tamper_json_file(
        chain["candidate_pipeline_path"],
        tmp_path,
        "bad_pipeline_complete.json",
        lambda p: p["summary"].__setitem__("pipeline_complete", False),
    )
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_PIPELINE_NOT_COMPLETE"):
        _run_selection_recommendation(chain, tmp_path, overrides={"--candidate-pipeline": tampered})


def test_selection_recommendation_screening_not_complete_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = _tamper_json_file(
        chain["candidate_pipeline_path"],
        tmp_path,
        "bad_pipeline_screening.json",
        lambda p: p["summary"].__setitem__("screening_complete", False),
    )
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_SCREENING_NOT_COMPLETE"):
        _run_selection_recommendation(chain, tmp_path, overrides={"--candidate-pipeline": tampered})


def test_selection_recommendation_previous_trading_day_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = _tamper_json_file(
        chain["research_window_path"],
        tmp_path,
        "bad_research_window_ptd.json",
        lambda p: p.__setitem__("previous_trading_day", "2020-01-01"),
    )
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_PREVIOUS_TRADING_DAY_MISMATCH"):
        _run_selection_recommendation(chain, tmp_path, overrides={"--research-window": tampered})


# ---------------------------------------------------------------------------
# P0-2 / P0-3: risk-check trust-chain tests.
# ---------------------------------------------------------------------------


def _run_risk_check(
    chain,
    tmp_path,
    *,
    extra_args=None,
    selection_path=None,
    ranking_path=None,
    event_gate_path=None,
    research_window_path=None,
):
    output_path = tmp_path / "risk_result.json"
    argv = [
        "risk-check",
        "--recommendation",
        str(chain["recommendation_path"]),
        "--candidates",
        str(chain["candidates_path"]),
        "--candidate-pipeline",
        str(chain["candidate_pipeline_path"]),
        "--market-data",
        str(chain["market_data_path"]),
        "--sources",
        str(chain["sources_path"]),
        "--config",
        str(chain["config_path"]),
        "--output",
        str(output_path),
        "--current-positions",
        "0",
        "--trades-today",
        "0",
    ]
    if selection_path is not False:
        argv += ["--selection", str(selection_path or chain["selection_path"])]
    if ranking_path is not False:
        argv += ["--ranking", str(ranking_path or chain["ranking_path"])]
    if event_gate_path is not False:
        argv += ["--event-gate", str(event_gate_path or chain["event_gate_path"])]
    if research_window_path is not False:
        argv += [
            "--research-window",
            str(research_window_path or chain["research_window_path"]),
        ]
    if extra_args:
        argv += extra_args
    return cli.main(argv), output_path


def test_risk_check_full_chain_proceeds_to_risk_rules(tmp_path):
    chain = _full_v6_chain(tmp_path)
    result, output_path = _run_risk_check(chain, tmp_path)
    assert result == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["decision"] == "TRADE"
    # This fixture is fully deterministic (fixed turnover/tick-size/position
    # inputs, no wall-clock dependence), so it must produce exactly one real
    # Risk outcome -- verified by actually running it, not guessed from
    # reading src/risk.py.
    assert payload["status"] == "PASS"
    assert payload["violations"] == []


def test_risk_check_selection_recommendation_preconditions_previous_trading_day_mismatch_is_hard_error_and_atomic(
    tmp_path,
):
    """P1-4 (validate_selection_recommendation_preconditions) must run in
    Case C's risk-check path too: research_window.previous_trading_day not
    matching selection.previous_trading_day is invisible to the
    recompute-and-compare output-contract check (build_selection_recommendation
    never reflects previous_trading_day in the Recommendation's own output
    fields), so only the dedicated preconditions check catches it. This test
    also doubles as the atomic-write proof: the pre-seeded output file must
    be untouched, byte for byte, after the Hard Error."""
    chain = _full_v6_chain(tmp_path)
    tampered_research_window = _tamper_json_file(
        chain["research_window_path"],
        tmp_path,
        "risk_bad_research_window_ptd.json",
        lambda p: p.__setitem__("previous_trading_day", "2020-01-01"),
    )

    output_path = tmp_path / "risk_result.json"
    sentinel = b"SENTINEL-NOT-A-REAL-RISK-RESULT"
    output_path.write_bytes(sentinel)

    argv = [
        "risk-check",
        "--recommendation",
        str(chain["recommendation_path"]),
        "--candidates",
        str(chain["candidates_path"]),
        "--candidate-pipeline",
        str(chain["candidate_pipeline_path"]),
        "--market-data",
        str(chain["market_data_path"]),
        "--sources",
        str(chain["sources_path"]),
        "--config",
        str(chain["config_path"]),
        "--output",
        str(output_path),
        "--current-positions",
        "0",
        "--trades-today",
        "0",
        "--selection",
        str(chain["selection_path"]),
        "--ranking",
        str(chain["ranking_path"]),
        "--event-gate",
        str(chain["event_gate_path"]),
        "--research-window",
        str(tampered_research_window),
    ]
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_PREVIOUS_TRADING_DAY_MISMATCH"):
        cli.main(argv)

    assert output_path.read_bytes() == sentinel


def test_risk_check_selection_recommendation_preconditions_target_date_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered_research_window = _tamper_json_file(
        chain["research_window_path"],
        tmp_path,
        "risk_bad_research_window_td.json",
        lambda p: p.__setitem__("target_date", "2099-01-01"),
    )
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_TARGET_DATE_MISMATCH"):
        _run_risk_check(chain, tmp_path, research_window_path=tampered_research_window)


def test_risk_check_selection_recommendation_preconditions_screening_not_complete_is_hard_error(tmp_path):
    """A self-consistent attack: candidate_pipeline.summary.screening_complete
    is flipped to False, and recommendation.pipeline_summary is updated to
    match it exactly, so the earlier validate_recommendation_pipeline_link
    check (which merely compares the two for equality) does NOT catch this
    on its own. Only the new validate_selection_recommendation_preconditions
    call -- which inspects screening_complete's actual value, not just
    cross-artifact equality -- can catch it."""
    chain = _full_v6_chain(tmp_path)

    candidate_pipeline = json.loads(chain["candidate_pipeline_path"].read_text(encoding="utf-8"))
    candidate_pipeline["summary"]["screening_complete"] = False
    assert candidate_pipeline["summary"]["pipeline_complete"] is True
    tampered_pipeline_path = tmp_path / "risk_bad_pipeline_screening.json"
    _write_json_file(tampered_pipeline_path, candidate_pipeline)

    recommendation = json.loads(chain["recommendation_path"].read_text(encoding="utf-8"))
    recommendation["pipeline_summary"] = copy.deepcopy(candidate_pipeline["summary"])
    tampered_recommendation_path = tmp_path / "risk_bad_recommendation_screening.json"
    _write_json_file(tampered_recommendation_path, recommendation)

    # Confirm the mutated recommendation.json is still schema-valid on its
    # own terms, so a schema failure can't accidentally make this test pass
    # for the wrong reason.
    from src.contracts import validate_json_document

    validate_json_document(recommendation, "recommendation.schema.json")

    # Confirm this attack artifact really is self-consistent with the
    # earlier, weaker link check: it alone must NOT raise.
    from src.contracts import validate_recommendation_pipeline_link

    validate_recommendation_pipeline_link(recommendation, candidate_pipeline)

    output_path = tmp_path / "risk_result.json"
    argv = [
        "risk-check",
        "--recommendation",
        str(tampered_recommendation_path),
        "--candidates",
        str(chain["candidates_path"]),
        "--candidate-pipeline",
        str(tampered_pipeline_path),
        "--market-data",
        str(chain["market_data_path"]),
        "--sources",
        str(chain["sources_path"]),
        "--config",
        str(chain["config_path"]),
        "--output",
        str(output_path),
        "--current-positions",
        "0",
        "--trades-today",
        "0",
        "--selection",
        str(chain["selection_path"]),
        "--ranking",
        str(chain["ranking_path"]),
        "--event-gate",
        str(chain["event_gate_path"]),
        "--research-window",
        str(chain["research_window_path"]),
    ]
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_SCREENING_NOT_COMPLETE"):
        cli.main(argv)


def test_risk_check_v6_v2_without_selection_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    with pytest.raises(ValueError, match="RISK_SELECTION_REQUIRED"):
        _run_risk_check(chain, tmp_path, selection_path=False)


def test_risk_check_v6_v2_without_ranking_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    with pytest.raises(ValueError, match="RISK_RANKING_REQUIRED"):
        _run_risk_check(chain, tmp_path, ranking_path=False)


def test_risk_check_v6_v2_without_event_gate_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    with pytest.raises(ValueError, match="RISK_SELECTION_EVENT_GATE_REQUIRED"):
        _run_risk_check(chain, tmp_path, event_gate_path=False)


def test_risk_check_v6_v2_without_research_window_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    with pytest.raises(ValueError, match="RISK_SELECTION_RESEARCH_WINDOW_REQUIRED"):
        _run_risk_check(chain, tmp_path, research_window_path=False)


def test_risk_check_selection_sha256_tamper_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    tampered = json.loads(chain["selection_path"].read_text(encoding="utf-8"))
    # Tamper the file after recommendation.json was built against the
    # original bytes: recommendation.selection_sha256 still names the
    # original hash, but the real (current) file hash now differs.
    tampered["evaluated_ticker"] = tampered["evaluated_ticker"]
    tampered_path = tmp_path / "tampered_selection.json"
    tampered_text = json.dumps(tampered, ensure_ascii=False, indent=2) + "\n \n"
    tampered_path.write_text(tampered_text, encoding="utf-8")
    with pytest.raises(ValueError, match="RECOMMENDATION_SELECTION_LINK"):
        _run_risk_check(chain, tmp_path, selection_path=tampered_path)


def test_risk_check_ranking_sha256_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    other_ranking = json.loads(chain["ranking_path"].read_text(encoding="utf-8"))
    other_ranking["generated_at"] = "2026-08-09T21:31:00+09:00"
    other_ranking_path = tmp_path / "other_ranking.json"
    _write_json_file(other_ranking_path, other_ranking)
    with pytest.raises(ValueError, match="SELECTION_OUTPUT_CONTRACT_MISMATCH|SELECTION_HASH_CHAIN_MISMATCH"):
        _run_risk_check(chain, tmp_path, ranking_path=other_ranking_path)


def test_risk_check_ticker_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    recommendation = json.loads(chain["recommendation_path"].read_text(encoding="utf-8"))
    recommendation["ticker"] = "ZZ99"
    tampered_recommendation_path = tmp_path / "tampered_recommendation.json"
    _write_json_file(tampered_recommendation_path, recommendation)
    tampered_chain = dict(chain)
    tampered_chain["recommendation_path"] = tampered_recommendation_path
    with pytest.raises(ValueError):
        _run_risk_check(tampered_chain, tmp_path)


def test_risk_check_target_date_mismatch_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    selection = json.loads(chain["selection_path"].read_text(encoding="utf-8"))
    # A selection.json with a different target_date than its own recorded
    # ranking_sha256 would actually validate against would fail the output
    # contract recompute; simulate via a differently-dated recommendation
    # instead, which validate_recommendation_selection_link also protects.
    recommendation = json.loads(chain["recommendation_path"].read_text(encoding="utf-8"))
    recommendation["target_date"] = "2099-01-01"
    tampered_recommendation_path = tmp_path / "tampered_recommendation.json"
    _write_json_file(tampered_recommendation_path, recommendation)
    tampered_chain = dict(chain)
    tampered_chain["recommendation_path"] = tampered_recommendation_path
    with pytest.raises(ValueError):
        _run_risk_check(tampered_chain, tmp_path)


def test_risk_check_forged_selection_is_hard_error(tmp_path):
    chain = _full_v6_chain(tmp_path)
    forged = json.loads(chain["selection_path"].read_text(encoding="utf-8"))
    forged["reason_codes"] = ["SELECTION_TURNOVER_BELOW_MINIMUM"]
    for rule in forged["rule_evaluations"]:
        rule["result"] = "REJECT"
        rule["reason_code"] = (
            "SELECTION_TURNOVER_BELOW_MINIMUM"
            if rule["rule_id"] == "minimum_turnover_yen"
            else "SELECTION_RELATIVE_TICK_SIZE_ABOVE_MAXIMUM"
        )
    forged_path = tmp_path / "forged_selection.json"
    _write_json_file(forged_path, forged)
    with pytest.raises(ValueError, match="SELECTION_OUTPUT_CONTRACT_MISMATCH"):
        _run_risk_check(chain, tmp_path, selection_path=forged_path)


def test_risk_check_forged_recommendation_field_is_hard_error(tmp_path):
    """A Recommendation v2 whose Selection-link fields (target_date,
    strategy_version, config_sha256, decision, ticker, selection_sha256,
    selection_reasons) are each individually correct, but whose 'notes'
    field was independently hand-tampered, must still Hard Error --
    proving validate_selection_recommendation_output_contract's
    recompute-and-compare catches what validate_recommendation_selection_link
    alone does not (build_selection_recommendation() always emits
    notes=None; a non-null notes value can never be genuine)."""
    chain = _full_v6_chain(tmp_path)
    recommendation = json.loads(chain["recommendation_path"].read_text(encoding="utf-8"))
    assert recommendation["notes"] is None
    recommendation["notes"] = "hand-injected note, not produced by build_selection_recommendation"
    tampered_recommendation_path = tmp_path / "tampered_notes_recommendation.json"
    _write_json_file(tampered_recommendation_path, recommendation)
    tampered_chain = dict(chain)
    tampered_chain["recommendation_path"] = tampered_recommendation_path
    with pytest.raises(ValueError, match="SELECTION_RECOMMENDATION_OUTPUT_CONTRACT_MISMATCH"):
        _run_risk_check(tampered_chain, tmp_path)


def test_risk_check_case_c_atomic_write_on_hard_error(tmp_path):
    """A pre-existing risk_result.json is left byte-for-byte unchanged when
    a Case C trust-chain Hard Error occurs."""
    chain = _full_v6_chain(tmp_path)
    output_path = tmp_path / "risk_result.json"
    sentinel = '{"sentinel": true}'
    output_path.write_text(sentinel, encoding="utf-8")

    forged = json.loads(chain["selection_path"].read_text(encoding="utf-8"))
    forged["reason_codes"] = ["SELECTION_TURNOVER_BELOW_MINIMUM"]
    for rule in forged["rule_evaluations"]:
        rule["result"] = "REJECT"
        rule["reason_code"] = (
            "SELECTION_TURNOVER_BELOW_MINIMUM"
            if rule["rule_id"] == "minimum_turnover_yen"
            else "SELECTION_RELATIVE_TICK_SIZE_ABOVE_MAXIMUM"
        )
    forged_path = tmp_path / "forged_selection_atomic.json"
    _write_json_file(forged_path, forged)

    argv = [
        "risk-check",
        "--recommendation",
        str(chain["recommendation_path"]),
        "--candidates",
        str(chain["candidates_path"]),
        "--candidate-pipeline",
        str(chain["candidate_pipeline_path"]),
        "--market-data",
        str(chain["market_data_path"]),
        "--sources",
        str(chain["sources_path"]),
        "--config",
        str(chain["config_path"]),
        "--output",
        str(output_path),
        "--current-positions",
        "0",
        "--trades-today",
        "0",
        "--selection",
        str(forged_path),
        "--ranking",
        str(chain["ranking_path"]),
        "--event-gate",
        str(chain["event_gate_path"]),
        "--research-window",
        str(chain["research_window_path"]),
    ]
    with pytest.raises(ValueError, match="SELECTION_OUTPUT_CONTRACT_MISMATCH"):
        cli.main(argv)
    assert output_path.read_text(encoding="utf-8") == sentinel


def test_risk_check_no_rank2_fallback_on_reject(tmp_path):
    """Rank1=A is SELECTED; if Risk rejects it, no Rank2=B candidate is ever
    referenced anywhere, Selection is never re-run, and recommendation.json
    is never modified by risk-check."""
    config_path, config = _enabled_selection_config_path(tmp_path)
    event_gate, candidates, market_data, sources = _full_case_for_config(
        config,
        config_path,
        [
            {"ticker": "AA01", "previous_high": "400", "tick_size": "1", "raw_value": "50,000"},
            {"ticker": "AA02", "previous_high": "400", "tick_size": "1", "raw_value": "10,000"},
        ],
    )
    event_gate_path, candidates_path, market_data_path, sources_path = _write_ranking_inputs(
        tmp_path, event_gate, candidates, market_data, sources
    )

    ranking_path = tmp_path / "ranking.json"
    assert (
        cli.main(
            [
                "build-ranking",
                "--event-gate",
                str(event_gate_path),
                "--candidates",
                str(candidates_path),
                "--market-data",
                str(market_data_path),
                "--sources",
                str(sources_path),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(ranking_path),
            ]
        )
        == 0
    )
    ranking = json.loads(ranking_path.read_text(encoding="utf-8"))
    rank1_ticker = next(c["ticker"] for c in ranking["candidates"] if c["final_rank"] == 1)
    rank2_ticker = next(c["ticker"] for c in ranking["candidates"] if c["final_rank"] == 2)
    assert {rank1_ticker, rank2_ticker} == {"AA01", "AA02"}

    selection_path = tmp_path / "selection.json"
    assert (
        cli.main(
            [
                "build-selection",
                "--ranking",
                str(ranking_path),
                "--event-gate",
                str(event_gate_path),
                "--candidates",
                str(candidates_path),
                "--market-data",
                str(market_data_path),
                "--sources",
                str(sources_path),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(selection_path),
            ]
        )
        == 0
    )
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    assert selection["selected_ticker"] == rank1_ticker

    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    _write_ranking_json_file(
        candidate_pipeline_path,
        {
            "schema_version": 1,
            "target_date": TARGET_DATE,
            "generated_at": "2026-08-09T21:30:00+00:00",
            "strategy_version": config["strategy_version"],
            "config_sha256": strategy_config_sha256(config),
            "summary": complete_pipeline_summary(),
            "candidates": [],
        },
    )
    research_window_path = tmp_path / "research_window.json"
    _write_ranking_json_file(
        research_window_path,
        {
            "schema_version": 1,
            "target_date": TARGET_DATE,
            "previous_trading_day": PREVIOUS_TRADING_DAY,
            "research_cutoff": "2026-08-09T21:00:00+00:00",
            "post_cutoff_information_status": "NO_NON_BUSINESS_GAP",
            "research_window": {
                "run_type": "FIRST_RUN",
                "window_start": "2026-08-09T00:00:00+00:00",
                "window_end": "2026-08-09T21:00:00+00:00",
                "previous_research_cutoff": None,
                "previous_run_date": None,
                "bootstrap_lookback_days": 5,
            },
        },
    )
    recommendation_path = tmp_path / "recommendation.json"
    assert (
        cli.main(
            [
                "build-selection-recommendation",
                "--ranking",
                str(ranking_path),
                "--selection",
                str(selection_path),
                "--event-gate",
                str(event_gate_path),
                "--candidates",
                str(candidates_path),
                "--candidate-pipeline",
                str(candidate_pipeline_path),
                "--market-data",
                str(market_data_path),
                "--research-window",
                str(research_window_path),
                "--sources",
                str(sources_path),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(recommendation_path),
            ]
        )
        == 0
    )
    recommendation_before = recommendation_path.read_text(encoding="utf-8")
    assert json.loads(recommendation_before)["ticker"] == rank1_ticker

    chain = {
        "config_path": config_path,
        "recommendation_path": recommendation_path,
        "candidates_path": candidates_path,
        "candidate_pipeline_path": candidate_pipeline_path,
        "market_data_path": market_data_path,
        "sources_path": sources_path,
        "selection_path": selection_path,
        "ranking_path": ranking_path,
        "event_gate_path": event_gate_path,
        "research_window_path": research_window_path,
    }
    # current_positions == max_positions forces a REJECTED risk decision
    # regardless of market data, exercising the Risk REJECTED path.
    result, output_path = _run_risk_check(
        chain,
        tmp_path,
        extra_args=["--current-positions", "1", "--trades-today", "0"],
    )
    assert result == 0
    risk_result = json.loads(output_path.read_text(encoding="utf-8"))
    assert risk_result["decision"] == "TRADE"
    assert risk_result["ticker"] == rank1_ticker
    assert rank2_ticker not in json.dumps(risk_result)
    # recommendation.json is never modified by risk-check.
    assert recommendation_path.read_text(encoding="utf-8") == recommendation_before


def test_risk_check_case_c_no_trade_full_chain_reaches_not_applicable(tmp_path):
    """Selection rules legitimately reject Rank 1 (turnover below the
    configured threshold) -> Recommendation v2 NO_TRADE ->
    risk-check reaches NOT_APPLICABLE, but only after the FULL Ranking +
    Selection + Recommendation trust chain re-verification succeeds -- the
    whole point is proving a NO_TRADE recommendation is genuine before Risk
    is willing to say NOT_APPLICABLE for it."""
    config_path, config = _enabled_selection_config_path(tmp_path, minimum_turnover_yen=1_000_000)
    paths = _build_real_ranking_chain(
        tmp_path,
        config_path,
        config,
        [{"ticker": "1234", "previous_high": "400", "tick_size": "1", "raw_value": "1"}],
    )
    ranking_path = paths["ranking_path"]

    selection_path = tmp_path / "selection.json"
    assert cli.main(_build_selection_argv(paths, selection_path)) == 0
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    assert selection["selection_status"] == "NO_TRADE"

    candidate_pipeline_path = tmp_path / "candidate_pipeline.json"
    _write_ranking_json_file(
        candidate_pipeline_path,
        {
            "schema_version": 1,
            "target_date": TARGET_DATE,
            "generated_at": "2026-08-09T21:30:00+00:00",
            "strategy_version": config["strategy_version"],
            "config_sha256": strategy_config_sha256(config),
            "summary": complete_pipeline_summary(),
            "candidates": [],
        },
    )
    research_window_path = tmp_path / "research_window.json"
    _write_ranking_json_file(
        research_window_path,
        {
            "schema_version": 1,
            "target_date": TARGET_DATE,
            "previous_trading_day": PREVIOUS_TRADING_DAY,
            "research_cutoff": "2026-08-09T21:00:00+00:00",
            "post_cutoff_information_status": "NO_NON_BUSINESS_GAP",
            "research_window": {
                "run_type": "FIRST_RUN",
                "window_start": "2026-08-09T00:00:00+00:00",
                "window_end": "2026-08-09T21:00:00+00:00",
                "previous_research_cutoff": None,
                "previous_run_date": None,
                "bootstrap_lookback_days": 5,
            },
        },
    )

    recommendation_path = tmp_path / "recommendation.json"
    assert (
        cli.main(
            [
                "build-selection-recommendation",
                "--ranking",
                str(ranking_path),
                "--selection",
                str(selection_path),
                "--event-gate",
                str(paths["event_gate_path"]),
                "--candidates",
                str(paths["candidates_path"]),
                "--candidate-pipeline",
                str(candidate_pipeline_path),
                "--market-data",
                str(paths["market_data_path"]),
                "--research-window",
                str(research_window_path),
                "--sources",
                str(paths["sources_path"]),
                "--source-matrix",
                str(DEFAULT_SOURCE_MATRIX_PATH),
                "--config",
                str(config_path),
                "--output",
                str(recommendation_path),
            ]
        )
        == 0
    )
    recommendation = json.loads(recommendation_path.read_text(encoding="utf-8"))
    assert recommendation["decision"] == "NO_TRADE"

    chain = {
        "config_path": config_path,
        "recommendation_path": recommendation_path,
        "candidates_path": paths["candidates_path"],
        "candidate_pipeline_path": candidate_pipeline_path,
        "market_data_path": paths["market_data_path"],
        "sources_path": paths["sources_path"],
        "selection_path": selection_path,
        "ranking_path": ranking_path,
        "event_gate_path": paths["event_gate_path"],
        "research_window_path": research_window_path,
    }
    result, output_path = _run_risk_check(chain, tmp_path)
    assert result == 0
    risk_result = json.loads(output_path.read_text(encoding="utf-8"))
    assert risk_result["decision"] == "NO_TRADE"
    assert risk_result["status"] == "NOT_APPLICABLE"


@pytest.mark.parametrize(
    "target_file",
    ["event_gate", "candidates", "market_data", "sources", "source_matrix", "config"],
)
def test_selection_recommendation_upstream_mutation_is_hard_error(tmp_path, target_file):
    """After a valid selection.json exists, raw-byte-mutating (in a
    schema-valid way) one of event_gate/candidates/market_data/sources/
    source_matrix/strategy_snapshot before running
    build-selection-recommendation must Hard Error, even though
    selection.json's own hash chain still 'looks' self-consistent -- proving
    load_and_verify_ranking_trust_chain() is genuinely re-checked here too."""
    chain = _full_v6_chain(tmp_path)

    if target_file == "source_matrix":
        target_path = tmp_path / "source_matrix_mutated.yaml"
        target_path.write_bytes(DEFAULT_SOURCE_MATRIX_PATH.read_bytes())
        override_flag = "--source-matrix"
    else:
        path_key = "config_path" if target_file == "config" else f"{target_file}_path"
        target_path = chain[path_key]
        override_flag = "--config" if target_file == "config" else f"--{target_file.replace('_', '-')}"

    original_bytes = target_path.read_bytes()
    if target_file in ("config", "source_matrix"):
        mutated_bytes = original_bytes + b"\n# mutated for test\n"
    else:
        payload = json.loads(original_bytes.decode("utf-8"))
        if "generated_at" in payload:
            payload["generated_at"] = "2099-01-01T00:00:00+00:00"
        mutated_bytes = (json.dumps(payload, ensure_ascii=False, indent=4) + "\n").encode("utf-8")
        assert mutated_bytes != original_bytes

    if target_file == "source_matrix":
        target_path.write_bytes(mutated_bytes)
        overrides = {override_flag: target_path}
    else:
        # config is YAML, not JSON -- write bytes directly to a sibling copy.
        mutated_path = tmp_path / f"mutated_{target_file}"
        mutated_path.write_bytes(mutated_bytes)
        overrides = {override_flag: mutated_path}

    output_path = tmp_path / "recommendation_mutated.json"
    with pytest.raises(ValueError, match="RANKING_INPUT_HASHES_MISMATCH|SELECTION_RECOMMENDATION_INPUT_HASHES_MISMATCH"):
        _run_selection_recommendation(chain, tmp_path, overrides=overrides, output_name="recommendation_mutated.json")
    assert not output_path.exists()


@pytest.mark.parametrize(
    "target_flag",
    ["--candidates", "--candidate-pipeline", "--market-data", "--sources", "--ranking", "--event-gate"],
)
def test_risk_check_case_c_upstream_mutation_is_hard_error(tmp_path, target_flag):
    """After a valid Recommendation v2 exists, mutating one upstream file
    before risk-check must Hard Error, and risk_result.json must not be
    written."""
    chain = _full_v6_chain(tmp_path)
    key = {
        "--candidates": "candidates_path",
        "--candidate-pipeline": "candidate_pipeline_path",
        "--market-data": "market_data_path",
        "--sources": "sources_path",
        "--ranking": "ranking_path",
        "--event-gate": "event_gate_path",
    }[target_flag]
    target_path = chain[key]
    payload = json.loads(target_path.read_text(encoding="utf-8"))
    payload["target_date"] = "2099-01-01"
    mutated_path = tmp_path / f"mutated_{key}.json"
    _write_json_file(mutated_path, payload)

    output_path = tmp_path / "risk_result_mutated.json"
    argv = [
        "risk-check",
        "--recommendation",
        str(chain["recommendation_path"]),
        "--candidates",
        str(chain["candidates_path"]),
        "--candidate-pipeline",
        str(chain["candidate_pipeline_path"]),
        "--market-data",
        str(chain["market_data_path"]),
        "--sources",
        str(chain["sources_path"]),
        "--config",
        str(chain["config_path"]),
        "--output",
        str(output_path),
        "--current-positions",
        "0",
        "--trades-today",
        "0",
        "--selection",
        str(chain["selection_path"]),
        "--ranking",
        str(chain["ranking_path"]),
        "--event-gate",
        str(chain["event_gate_path"]),
        "--research-window",
        str(chain["research_window_path"]),
    ]
    flag_index = argv.index(target_flag)
    argv[flag_index + 1] = str(mutated_path)

    with pytest.raises(ValueError):
        cli.main(argv)
    assert not output_path.exists()
