---
name: record-daytrade-result
description: Validate and record a completed Japanese stock day-trade using only execution facts confirmed by the user, then calculate current metrics. Use only when the user explicitly invokes $record-daytrade-result after a planned trade has been fully entered and closed on the target date.
---

# Record Daytrade Result

Run from the repository root with `daytrade-sbi/` as the Python working directory. The main agent performs this sequential workflow directly; do not delegate it to a subagent.

## Canonical Instructions

Read `daytrade-sbi/AGENTS.md`, `daytrade-sbi/TODO.md`, and `daytrade-sbi/docs/trade-data-dictionary.md`. Treat the data dictionary as the source of truth for the execution schema, CLI commands, CSV columns, and duplicate behavior.

## Workflow

1. Identify the target date and load its `recommendation.json`, `risk_result.json`, and `market_data.json`.
2. Confirm that the recommendation is `TRADE`, Risk Engine status is `PASS`, and the position was fully entered and closed on that date. Otherwise do not create a trade row.
3. Ask the user to confirm ticker, actual entry, actual exit, shares, realized profit or loss if available, exit reason, entry time, exit time, and required notes.
4. Copy trade date, strategy version, and config SHA-256 from the linked artifacts. Do not generate or guess them.
5. Write `runs/YYYY-MM-DD/execution_result.json` using `schemas/execution_result.schema.json`.
6. Run the documented validation command and show the complete CSV preview to the user.
7. Obtain explicit confirmation before running the documented record command. Then calculate metrics.
8. Report whether the row was recorded or already present, the metrics, and every uncalculated field.

## Boundaries

- Record only facts explicitly supplied or confirmed by the user.
- Leave `profit_loss_yen` as `null` when unconfirmed; do not derive it from prices.
- Do not update `recommendations.csv` execution-state columns while their procedure remains unresolved.
- Do not record `NO_TRADE`, an unfilled order, an open position, a partial fill, or a backtest in `trades.csv`.
- Stop when artifacts conflict or the situation requires an unresolved TODO decision.
