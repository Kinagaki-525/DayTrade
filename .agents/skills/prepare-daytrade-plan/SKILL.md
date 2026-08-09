---
name: prepare-daytrade-plan
description: Prepare the next trading day's Japanese cash-equity day-trade candidate by orchestrating sourced web research, repository Python validation, deterministic screening, a TRADE or NO_TRADE recommendation, and the Risk Engine. Use only when the user explicitly invokes $prepare-daytrade-plan for this repository.
---

# Prepare Daytrade Plan

Run from the repository root with `daytrade-sbi/` as the Python working directory. The main agent owns orchestration, decisions, file writes, Python commands, and human checkpoints.

## Canonical Instructions

Read these files before starting:

- `daytrade-sbi/AGENTS.md` for repository-wide constraints
- `daytrade-sbi/config/strategy.yaml` for active settings
- `daytrade-sbi/TODO.md` for unresolved decisions
- `daytrade-sbi/prompts/nightly_research.md` for the detailed artifact and CLI workflow

Follow the nightly prompt as the procedural source of truth. Do not copy or replace its commands with inferred alternatives.

## Orchestration

1. Confirm the target and previous trading dates from authoritative evidence. Stop if either date is uncertain.
2. Delegate bounded market fact collection to `market_researcher` when available. Require source URL, retrieval time, trading date, ticker, field, and exact value. Wait for its summary; the subagent must not write files.
3. Have the main agent follow the nightly prompt to snapshot config, save confirmed evidence, and run Python validation and screening.
4. Delegate a read-only audit of the saved dates, sources, required values, and contradictions to `source_auditor` when available. Wait for its findings. Correct only confirmed transcription errors and rerun affected Python steps; never fill missing facts.
5. Have the main agent compare only `ELIGIBLE` candidates and write one `TRADE` recommendation or `NO_TRADE`.
6. Before the Risk Engine, ask the user for confirmed current positions and trades already made that day. Do not assume zero. Stop if either value is unavailable.
7. Complete the Risk Engine, report generation, and recommendation recording exactly as specified by the nightly prompt.
8. Present a manual-entry candidate only for `TRADE` plus `PASS`. Keep `NO_TRADE` or `REJECTED` unchanged and report the reason.

## Boundaries

- Do not change strategy settings or resolve TODO items.
- Do not delegate final recommendation, file writes, Python validation, or human confirmation.
- Do not log in to SBI Securities, operate its UI, or send an order.
- Do not fabricate facts or claim profitability.

Report the target date, decision, evidence gaps, Risk Engine status, created files, and facts the user must verify in SBI.
