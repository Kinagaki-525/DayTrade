---
name: prepare-daytrade-plan
description: Prepare the next trading day's Japanese cash-equity day-trade candidate by orchestrating the repository's deterministic acquisition CLIs (curl GET, raw-byte evidence, deterministic parsers), Python validation, deterministic screening, a TRADE, NO_TRADE, or DATA_UNAVAILABLE recommendation, and the Risk Engine. Use only when the user explicitly invokes $prepare-daytrade-plan for this repository.
---

# Prepare Daytrade Plan

Run from the repository root with `daytrade-sbi/` as the Python working directory. The agent owns **orchestration only**: running the repository CLIs in the Canonical CLI Pipeline Order and reporting the result, plus Event AI Classification over already-fetched local raw pages. The agent never fetches market data from the web and never hand-writes `market_data.json`, `sources.json`, or `recommendation.json`. Codex and Claude Code both use this same repository CLI pipeline.

Canonical CLI Pipeline Order (source of truth: `daytrade-sbi/docs/canonical-pipeline.md`):

`snapshot-config` -> `validate-source-matrix` -> `resolve-research-window` -> `acquire-discovery` -> `init-candidate-research` -> `acquire-stage1-sources` -> market_data Stage1 reflect -> `apply-stage1` -> TSE Listing Batch Gate -> `plan-stage2-batches` -> `acquire-stage2-market-sources` -> market_data Stage2 reflect -> `acquire-actual-turnover` -> market_data turnover reflect -> `validate-market` -> `screen-market` -> `build-candidate-pipeline` -> `acquire-event-sources` -> Event AI Classification (local only) -> `merge-event-source-extraction` -> `init/complete event-research` -> `validate-event-research` -> `build-event-gate` -> `build-ranking` -> Case A/B/C

## Canonical Instructions

Read these files before starting:

- `daytrade-sbi/AGENTS.md` for repository-wide constraints
- `daytrade-sbi/config/strategy.yaml` for active settings
- `daytrade-sbi/TODO.md` for unresolved decisions
- `daytrade-sbi/docs/canonical-pipeline.md` for the Canonical CLI Pipeline Order
- `daytrade-sbi/prompts/nightly_research.md` for the detailed artifact and CLI workflow
- `daytrade-sbi/config/source_matrix.yaml` for the fixed market research sources

Follow the nightly prompt as the procedural source of truth. Do not copy or replace its commands with inferred alternatives.

## Orchestration

1. Confirm the target and previous trading dates from authoritative evidence. Stop if either date is uncertain.
2. Validate the fixed Source Matrix before research. Do not substitute undefined sources at runtime.
3. Have the main agent follow the nightly prompt to snapshot config, resolve and save the Python-generated research window, and run `acquire-discovery`, which fetches both Yahoo ranking pages by curl GET, stores the raw bytes with their SHA256, parses them deterministically, confirms TOP50, and writes `market_research.json`. Discovery candidates are never typed in by an agent or a subagent.
4. Check the Discovery Fail-Closed Gate before anything downstream. If `acquire-discovery` exits non-zero, stop. Then load `market_research.json` and stop if `overall_status` is `DISCOVERY_INCOMPLETE` (the CLI result reports `status=CLOSED`, with `reason_codes` carrying `market_research.notes` such as `VOLUME_RANKING_UNAVAILABLE`). Discovery incomplete is **not** `NO_TRADE`: the candidate Universe could not be built, so report `Pipeline: 未完了` / `停止Stage: DISCOVERY` rather than a daily decision, and never convert a `candidate_count == 0` into `NO_TRADE` on its own — on a successful Discovery, `candidate_count` is `len(discovery_candidates)`, not Discovery's own input ticker count. Keep the fail-closed evidence (`market_research.json`, `sources.json`, `network_requests/`, `source_attempts[]`) as it is. Do not run the Recommendation or Risk Builders to fill the gap, and never hand-write `recommendation.json` or `risk_result.json`. Only when Discovery completed successfully, run `init-candidate-research` so every Discovery Candidate has a `candidate_research[]` entry before Stage 1 or Stage 2 work continues.
5. Save confirmed Stage 1 evidence and run `apply-stage1`. Immediately after `apply-stage1` and **before running `plan-stage2-batches` or starting Stage 2 Candidate Research** (Stage 2 can query `YAHOO_JP_HISTORY` and `YAHOO_JP_NEWS`, not just `YAHOO_JP_QUOTE`), check ALL Stage 1 `PASS` candidates as a single all-or-nothing batch gate — from already-verified Source Evidence — for whether each is listed on the Tokyo Stock Exchange, because the `YAHOO_JP_HISTORY` / `YAHOO_JP_NEWS` / `YAHOO_JP_QUOTE` URL templates all hardcode the `.T` suffix and Discovery is `ALL_MARKETS`. This is a gate on the whole batch, not a per-candidate filter: do not exclude, skip, or drop individual candidates from the Stage 1 `PASS` set, and do not rewrite `stage1_status` for any candidate. If even one Stage 1 `PASS` candidate's TSE listing cannot be confirmed, do not run `plan-stage2-batches` at all, do not construct or fetch a guessed `.T` URL for any of these three sources, do not record a `FOUND` attempt for it, and do not start Stage 2 Candidate Research, Turnover Research, Event Research, or Ranking for ANY candidate — the nightly run stops here, fail closed. Only when every Stage 1 `PASS` candidate's TSE listing is confirmed, run `plan-stage2-batches` and then `acquire-stage2-market-sources`, whose candidate set is derived from the Stage 1 `PASS` results on disk. There is no `--ticker` option: no agent or subagent can widen or inject the candidate set.
6. After Stage 2 Candidate Research is merged and **before running `screen-market`**, run `acquire-actual-turnover` for the Stage 2 target set (all of them already passed the all-or-nothing TSE-listing gate in step 5): research the previous trading day's actual turnover from `YAHOO_JP_QUOTE`. On `FOUND`, save the Source Attempt and the matching source record into `sources.json` and the same canonical turnover into `market_data.json.turnover`. On any failure status, save the failure Source Attempt as-is without inventing a substitute value, and set `market_data.json.turnover` to `null` so no stale `FOUND` value survives. Only then continue to Python validation and screening.
7. Have the main agent run Python validation, official OHLCV audit, screening, candidate pipeline generation, and performance counter generation.
8. Delegate a read-only audit of the saved dates, cutoff, sources, Source Status, Discovery reasons, required values, and contradictions to `source_auditor` when available. Wait for its findings. Correct only confirmed transcription errors and rerun affected Python steps; never fill missing facts.
9. Confirm `candidate_pipeline.summary.pipeline_complete=true` and `research_incomplete=0` before continuing. If the pipeline is incomplete, correct the missing research or stop as incomplete.
10. Confirm `candidate_pipeline.summary.screening_complete=true` also holds, then follow `nightly_research.md` for Event Research: run `init-event-research`, and restrict candidates to `status=ELIGIBLE` and `screening_status=PASS` only. Run `acquire-event-sources`, which fetches all six event sources (`JPX_TDNET`, `JPX_EARNINGS_SCHEDULE`, `COMPANY_IR`, `COMPANY_IR_DISCLOSURE`, `YAHOO_JP_NEWS`, `KABUTAN_NEWS`) for the ELIGIBLE/PASS candidates, performing exactly one GET per shared page while still creating one candidate-scoped Source Attempt per candidate. Then perform Event AI Classification over the **already-fetched local raw pages only**, writing the temporary working file, and run `merge-event-source-extraction` (which revalidates every field against the ledger and the stored bytes), then `init-event-research` and `complete-event-research`. Event Research records evidence references and classifications; it must not decide `PASS`, `REJECT`, or `DATA_UNAVAILABLE`.
11. Run `validate-event-research`, then `build-event-gate` to produce `event_gate.json` exactly as `nightly_research.md` specifies. Confirm `event_gate.json` reports `event_gate_complete=true`; if it does not, stop — do not proceed toward Ranking or write a recommendation.
12. Fail-closed gating before Ranking: if `event_gate.json` reports `ranking_ready=false`, do not start Ranking. If any Event Gate candidate has `gate_status=DATA_UNAVAILABLE`, do not start Ranking and record the daily result as `DATA_UNAVAILABLE`. If Event Gate completed normally with zero `PASS` and zero `DATA_UNAVAILABLE` candidates, do not start Ranking and record `NO_TRADE`. If Event Gate completed normally with at least one `PASS` candidate and zero `DATA_UNAVAILABLE` candidates, `ranking_ready=true` — run `build-ranking` to produce `ranking.json`. Ranking v1 (`src/ranking.py`) is a deterministic, Python-only, AI-free ranking of Event Gate `PASS` candidates by two features only (actual turnover desc, relative tick size asc) using simple rank-sum aggregation; it never re-uses Discovery ranks and never fabricates an `estimated_turnover`. Then follow the Selection state machine, running only the CLIs — never reading Rank 1's `feature_values` (turnover, relative tick size) yourself to decide PASS/REJECT:
    - **Case A** — `ranking.json` reports `ranking_status=DATA_UNAVAILABLE`: do not run Selection. Run
      ```
      py -B -m src.cli build-ranking-terminal-recommendation \
        --ranking runs/<date>/ranking.json \
        --event-gate runs/<date>/event_gate.json \
        --candidates runs/<date>/candidates.json \
        --candidate-pipeline runs/<date>/candidate_pipeline.json \
        --market-data runs/<date>/market_data.json \
        --research-window runs/<date>/research_window.json \
        --sources runs/<date>/sources.json \
        --source-matrix config/source_matrix.yaml \
        --config runs/<date>/strategy_snapshot.yaml \
        --output runs/<date>/recommendation.json
      ```
      to produce a `decision=DATA_UNAVAILABLE` `recommendation.json` (schema_version 1).
    - **Case B** — `ranking_status=COMPLETE` but the active `strategy_snapshot.yaml`'s `selection.enabled` is `false` (thresholds not yet calibrated): do not run `build-selection`. Run the same `build-ranking-terminal-recommendation` command as Case A to produce a `decision=NO_TRADE` `recommendation.json` with reason `SELECTION_NOT_ACTIVE_PENDING_CALIBRATION`.
    - **Case C** — `ranking_status=COMPLETE` and `selection.enabled` is `true`: run `build-selection --ranking runs/<date>/ranking.json --event-gate runs/<date>/event_gate.json --candidates runs/<date>/candidates.json --market-data runs/<date>/market_data.json --sources runs/<date>/sources.json --source-matrix config/source_matrix.yaml --config runs/<date>/strategy_snapshot.yaml --output runs/<date>/selection.json`, then `build-selection-recommendation --ranking runs/<date>/ranking.json --selection runs/<date>/selection.json --event-gate runs/<date>/event_gate.json --candidates runs/<date>/candidates.json --candidate-pipeline runs/<date>/candidate_pipeline.json --market-data runs/<date>/market_data.json --research-window runs/<date>/research_window.json --sources runs/<date>/sources.json --source-matrix config/source_matrix.yaml --config runs/<date>/strategy_snapshot.yaml --output runs/<date>/recommendation.json`. Both commands independently re-verify the whole Ranking trust chain (raw-byte hashes plus a full recompute-and-compare), so all of `--event-gate`/`--candidates`/`--market-data`/`--sources`/`--source-matrix` are required and each command stops with a Hard Error without them. Report exactly what these CLIs output (`selection_status`, `decision`) — do not second-guess or recompute their result.
    All three cases (A, B, C) produce a real `recommendation.json` via the Terminal Recommendation Builder or the Selection Recommendation Builder CLI — never write, edit, or hand-craft `recommendation.json` yourself. Never let any AI judgment change a rank, convert rank 1 into a `TRADE` outside of these CLIs, or fall back to rank 2. Never pass `REJECT` or `DATA_UNAVAILABLE` Event Gate candidates to Ranking.
13. If the recommendation decision is `TRADE`, ask the user for confirmed current positions and trades already made that day before the Risk Engine. Do not assume zero. Stop if either value is unavailable.
14. If the recommendation decision is `NO_TRADE` or `DATA_UNAVAILABLE`, run the non-TRADE Risk Engine path without asking for current positions or trades today; it must produce `NOT_APPLICABLE`. For a Case A/B (`recommendation.schema_version=1`) recommendation, `risk-check` independently re-verifies the whole Ranking trust chain, so also pass `--ranking runs/<date>/ranking.json --event-gate runs/<date>/event_gate.json --research-window runs/<date>/research_window.json` — these are required and `risk-check` stops with a Hard Error without them. For a Case C (`recommendation.schema_version=2`, Selection-driven) recommendation — TRADE or NO_TRADE alike — `risk-check` independently re-verifies the whole Ranking trust chain plus Selection's and the Recommendation's own recompute-and-compare, so also pass `--selection runs/<date>/selection.json --ranking runs/<date>/ranking.json --event-gate runs/<date>/event_gate.json --research-window runs/<date>/research_window.json` — these are required and `risk-check` stops with a Hard Error without them.
15. Complete the Risk Engine, `research.md`, `recommendation.md`, `report.md`, `official_ohlcv_audit.json`, run artifact allowlist validation (including `event_research.json`, `event_gate.json`, and `ranking.json`), and recommendation recording exactly as specified by the nightly prompt.
16. Present a manual-entry candidate only for `TRADE` plus `PASS`. Keep `NO_TRADE`, `DATA_UNAVAILABLE`, or `REJECTED` unchanged and report the reason.

## Boundaries

- Do not change strategy settings or resolve TODO items.
- Do not add Discovery routes, Selection rules, or Morning Research.
- Do not let AI change a Ranking rank, read Rank 1's feature values to decide PASS/REJECT yourself, or convert Ranking rank 1 into a `TRADE` other than by running `build-selection`/`build-selection-recommendation` and reporting their output verbatim.
- Do not modify repository code, tests, schemas, prompts, or strategy/rule files during the daily plan run unless the user explicitly approves that code change in the current conversation. Treat code defects found during the run as findings to report, not as automatic fixes.
- Do not treat cutoff-after weekend information as confirmed zero results unless it was actually researched inside the research window.
- Do not delegate final recommendation, file writes, Python validation, or human confirmation.
- Do not log in to SBI Securities, operate its UI, or send an order.
- Do not fabricate facts or claim profitability.

Report the target date, decision, evidence gaps, Risk Engine status, created files, and facts the user must verify in SBI.
