---
name: prepare-daytrade-plan
description: Prepare the next trading day's Japanese cash-equity day-trade candidate by orchestrating sourced web research, repository Python validation, deterministic screening, a TRADE, NO_TRADE, or DATA_UNAVAILABLE recommendation, and the Risk Engine. Use only when the user explicitly invokes $prepare-daytrade-plan for this repository.
---

# Prepare Daytrade Plan

Run from the repository root with `daytrade-sbi/` as the Python working directory. The main agent owns orchestration, decisions, file writes, Python commands, and human checkpoints.

## Canonical Instructions

Read these files before starting:

- `daytrade-sbi/AGENTS.md` for repository-wide constraints
- `daytrade-sbi/config/strategy.yaml` for active settings
- `daytrade-sbi/TODO.md` for unresolved decisions
- `daytrade-sbi/prompts/nightly_research.md` for the detailed artifact and CLI workflow
- `daytrade-sbi/config/source_matrix.yaml` for the fixed market research sources

Follow the nightly prompt as the procedural source of truth. Do not copy or replace its commands with inferred alternatives.

## Orchestration

1. Confirm the target and previous trading dates from authoritative evidence. Stop if either date is uncertain.
2. Validate the fixed Source Matrix before research. Do not substitute undefined sources at runtime.
3. Delegate bounded Market Discovery and Candidate Research to `market_researcher` when available. Require source_ref, source_id, source role, information type, source status, source URL, retrieval time, trading date, ticker, field, exact value, standard source_checks, source_attempt attempt_id, subagent merge state, and correct result_count semantics. Wait for its summary; the subagent must not write files.
4. Have the main agent follow the nightly prompt to snapshot config, resolve and save the Python-generated research window, save confirmed evidence, and run Python validation, official OHLCV audit, screening, candidate pipeline generation, and performance counter generation.
5. Delegate a read-only audit of the saved dates, cutoff, sources, Source Status, Discovery reasons, required values, and contradictions to `source_auditor` when available. Wait for its findings. Correct only confirmed transcription errors and rerun affected Python steps; never fill missing facts.
6. Confirm `candidate_pipeline.summary.pipeline_complete=true` and `research_incomplete=0` before writing a recommendation for the Risk Engine. If the pipeline is incomplete, correct the missing research or stop as incomplete.
7. Have the main agent compare only `ELIGIBLE` candidates and write one `TRADE` recommendation, `NO_TRADE`, or `DATA_UNAVAILABLE`.
8. If the recommendation decision is `TRADE`, ask the user for confirmed current positions and trades already made that day before the Risk Engine. Do not assume zero. Stop if either value is unavailable.
9. If the recommendation decision is `NO_TRADE` or `DATA_UNAVAILABLE`, run the non-TRADE Risk Engine path without asking for current positions or trades today; it must produce `NOT_APPLICABLE`.
10. Complete the Risk Engine, `research.md`, `recommendation.md`, `report.md`, `official_ohlcv_audit.json`, run artifact allowlist validation, and recommendation recording exactly as specified by the nightly prompt.
11. Present a manual-entry candidate only for `TRADE` plus `PASS`. Keep `NO_TRADE`, `DATA_UNAVAILABLE`, or `REJECTED` unchanged and report the reason.

## Boundaries

- Do not change strategy settings or resolve TODO items.
- Do not add Discovery routes, Ranking rules, or Morning Research.
- Do not treat cutoff-after weekend information as confirmed zero results unless it was actually researched inside the research window.
- Do not delegate final recommendation, file writes, Python validation, or human confirmation.
- Do not log in to SBI Securities, operate its UI, or send an order.
- Do not fabricate facts or claim profitability.

Report the target date, decision, evidence gaps, Risk Engine status, created files, and facts the user must verify in SBI.
