from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from src.config import load_strategy_config, load_yaml
from src.contracts import (
    load_json_document,
    validate_ranking_output_contract,
    validate_ranking_preconditions,
)
from src.market import MarketDataRecord
from src.ranking import build_ranking
from src.screening import screen_market_record
from src.source_matrix import load_source_matrix


FIXTURE_DIR = Path("regression/2026-08-12-ranking-v1-complete/runs/2026-08-12")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_ranking_v1_regression_fixture_hashes_are_real_not_placeholders():
    """The recorded input_hashes in event_gate.json and ranking.json must be
    the actual sha256 of the sibling artifact bytes, not 'aaa...'-style
    placeholders.

    Only the artifacts a Minimal Ranking Regression actually ships are
    checked here. event_gate.input_hashes also records
    event_research_sha256/candidate_pipeline_sha256, but those are Opaque
    Upstream Provenance: the referenced files belong to the Event Gate
    pipeline, not to the Event Gate -> Ranking trust boundary this fixture
    reproduces, so they are deliberately not shipped and not re-verified.
    """
    event_gate = json.loads((FIXTURE_DIR / "event_gate.json").read_text(encoding="utf-8"))
    ranking = json.loads((FIXTURE_DIR / "ranking.json").read_text(encoding="utf-8"))

    assert not (FIXTURE_DIR / "event_research.json").exists()
    assert not (FIXTURE_DIR / "candidate_pipeline.json").exists()

    assert event_gate["input_hashes"]["candidates_sha256"] == _sha256(FIXTURE_DIR / "candidates.json")
    assert event_gate["input_hashes"]["sources_sha256"] == _sha256(FIXTURE_DIR / "sources.json")
    assert event_gate["input_hashes"]["strategy_snapshot_sha256"] == _sha256(
        FIXTURE_DIR / "strategy_snapshot.yaml"
    )

    assert ranking["input_hashes"]["event_gate_sha256"] == _sha256(FIXTURE_DIR / "event_gate.json")
    assert ranking["input_hashes"]["candidates_sha256"] == _sha256(FIXTURE_DIR / "candidates.json")
    assert ranking["input_hashes"]["market_data_sha256"] == _sha256(FIXTURE_DIR / "market_data.json")
    assert ranking["input_hashes"]["sources_sha256"] == _sha256(FIXTURE_DIR / "sources.json")
    assert ranking["input_hashes"]["strategy_snapshot_sha256"] == _sha256(
        FIXTURE_DIR / "strategy_snapshot.yaml"
    )


def test_ranking_v1_regression_fixture_build_ranking_reproduces_complete():
    """Loading the full Event Gate -> Ranking regression fixture chain and
    calling build_ranking() with the same inputs must reproduce the exact
    recorded ranking.json (COMPLETE, PASS candidate ranked)."""
    event_gate = load_json_document(FIXTURE_DIR / "event_gate.json", "event_gate.schema.json")
    candidates = load_json_document(FIXTURE_DIR / "candidates.json", "candidates.schema.json")
    market_data = load_json_document(FIXTURE_DIR / "market_data.json", "market_data.schema.json")
    source_payload = load_json_document(FIXTURE_DIR / "sources.json", "sources.schema.json")
    recorded_ranking = load_json_document(FIXTURE_DIR / "ranking.json", "ranking.schema.json")
    source_matrix = load_source_matrix()
    config = load_strategy_config(FIXTURE_DIR / "strategy_snapshot.yaml")

    input_hashes = dict(recorded_ranking["input_hashes"])

    validate_ranking_preconditions(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        source_matrix=source_matrix,
        config=config,
        input_hashes=input_hashes,
        source_base_dir=FIXTURE_DIR,
    )

    ranking = build_ranking(
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=config,
        input_hashes=input_hashes,
    )

    assert ranking["ranking_status"] == "COMPLETE"
    assert ranking["ranking_complete"] is True
    assert ranking["summary"]["top_ranked_ticker"] == "7203"
    assert ranking["candidates"][0]["final_rank"] == 1

    # Byte-level reproducibility: every field except generated_at (wall
    # clock at write time) must match the recorded artifact exactly.
    recomputed = dict(ranking)
    recorded = dict(recorded_ranking)
    recomputed.pop("generated_at", None)
    recorded.pop("generated_at", None)
    assert recomputed == recorded

    validate_ranking_output_contract(
        ranking=recorded_ranking,
        event_gate=event_gate,
        candidates=candidates,
        market_data=market_data,
        source_payload=source_payload,
        config=config,
    )


def test_ranking_v1_regression_fixture_has_no_placeholder_hashes():
    """metadata.yaml claims every recorded hash is genuine. Guard that claim
    directly: no recorded sha256 in the fixture may be a repeated-character
    placeholder such as '1111...' or 'aaaa...'."""
    for name in ("event_gate.json", "ranking.json"):
        payload = json.loads((FIXTURE_DIR / name).read_text(encoding="utf-8"))
        for key, value in (payload.get("input_hashes") or {}).items():
            assert len(set(value)) > 1, f"{name}.input_hashes.{key} is a placeholder hash"


def test_ranking_v1_regression_fixture_source_matrix_hash_is_real():
    ranking = json.loads((FIXTURE_DIR / "ranking.json").read_text(encoding="utf-8"))
    assert ranking["input_hashes"]["source_matrix_sha256"] == _sha256(
        Path("config/source_matrix.yaml")
    )


def test_ranking_v1_regression_fixture_candidate_is_affordable():
    """HIGH-1: the fixture must be a candidate production Hard Screening
    could really have passed -- the recorded purchase amount has to fit
    inside capital.total_yen."""
    candidates = json.loads((FIXTURE_DIR / "candidates.json").read_text(encoding="utf-8"))
    config = load_strategy_config(FIXTURE_DIR / "strategy_snapshot.yaml")
    capital = Decimal(str(config["capital"]["total_yen"]))

    for candidate in candidates["candidates"]:
        order_plan = candidate["order_plan"]
        assert candidate["status"] == "ELIGIBLE"
        assert candidate["screening_status"] == "PASS"
        assert order_plan["affordable"] is True
        assert Decimal(order_plan["estimated_purchase_amount"]) <= capital


def test_ranking_v1_regression_fixture_passes_production_screening():
    """HIGH-1: feeding the fixture's market_data record back through the
    production screener must reproduce exactly the recorded candidate --
    ELIGIBLE / PASS / affordable -- so the fixture cannot drift into a state
    screen_market_record() would never produce."""
    market_data = json.loads((FIXTURE_DIR / "market_data.json").read_text(encoding="utf-8"))
    candidates = json.loads((FIXTURE_DIR / "candidates.json").read_text(encoding="utf-8"))
    config = load_strategy_config(FIXTURE_DIR / "strategy_snapshot.yaml")
    source_matrix = load_source_matrix()

    recorded_by_ticker = {item["ticker"]: item for item in candidates["candidates"]}
    assert recorded_by_ticker

    for raw_record in market_data["records"]:
        record = MarketDataRecord.from_dict(raw_record)
        result = screen_market_record(record, config, source_matrix).as_dict()
        assert result["status"] == "ELIGIBLE"
        assert result["screening_status"] == "PASS"
        assert result["order_plan"]["affordable"] is True
        assert result == recorded_by_ticker[record.ticker]


ALLOWED_CLASSIFICATIONS = {"DIRECT INPUT", "EXPECTED OUTPUT"}


def test_ranking_v1_regression_fixture_contains_only_documented_artifacts():
    """Every file shipped in the fixture must carry an explicit, recognised
    classification in metadata.yaml, so an unused artifact cannot silently
    accumulate. Parsed as YAML rather than substring-matched, so a filename
    that only appears inside a comment does not satisfy the check."""
    metadata = load_yaml(Path("regression/2026-08-12-ranking-v1-complete/metadata.yaml"))
    artifacts = metadata["artifacts"]

    shipped = {f"runs/2026-08-12/{path.name}" for path in sorted(FIXTURE_DIR.iterdir())}
    assert set(artifacts) == shipped, "metadata.yaml artifacts do not match the shipped file set"
    for key, entry in artifacts.items():
        assert entry["classification"] in ALLOWED_CLASSIFICATIONS, (
            f"{key} has an unrecognised classification: {entry['classification']}"
        )
