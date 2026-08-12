# Egress Allowlist for `prepare-daytrade-plan`

This session's network egress proxy blocks outbound web requests by default
(`EGRESS_BLOCKED`). Running `$prepare-daytrade-plan` requires live read access
to every domain defined in `config/source_matrix.yaml`. This file lists the
exact domains to allow so the skill can perform real market research instead
of stopping fail-closed.

## Fixed domains (add these to the environment's egress allowlist)

| Domain | Source IDs using it |
|---|---|
| `www.jpx.co.jp` | `JPX_CALENDAR`, `JPX_LISTED_COMPANY`, `JPX_TRADING_UNIT`, `JPX_LISTED_COMPANY_AUDIT`, `JPX_DAILY_REPORT`, `JPX_TICK_SIZE`, `JPX_TOPIX500`, `JPX_EARNINGS_SCHEDULE` |
| `finance.yahoo.co.jp` | `YAHOO_JP_VOLUME_RANKING`, `YAHOO_JP_GAIN_RANKING`, `YAHOO_JP_HISTORY`, `YAHOO_JP_NEWS`, `YAHOO_JP_QUOTE` |
| `kabutan.jp` | `KABUTAN_HISTORY`, `KABUTAN_NEWS` |
| `www.release.tdnet.info` | `JPX_TDNET` |

Minimal set: these 4 domains cover every fixed Source ID in the current
Source Matrix.

## Variable domains (cannot be pre-listed)

`COMPANY_IR` and `COMPANY_IR_DISCLOSURE` resolve to `https://{issuer_domain}/ir/`
— a different domain per candidate company's own investor-relations site.
No fixed allowlist entry can cover this in advance; it needs either:

- a wildcard/broad HTTPS allow for outbound fetches at run time, or
- per-run addition of the specific issuer domain(s) once Stage 2 candidates
  are known.

If the environment can only allow a fixed list, Event Research for
`COMPANY_IR` / `COMPANY_IR_DISCLOSURE` will still fail closed even after the
4 domains above are allowed — that is expected and separate from Discovery/
Stage 1/Stage 2/Turnover Research, which only need the 4 fixed domains.

## How to apply

This session (Claude Code on the web / remote execution environment) enforces
egress at the environment level. Add the 4 fixed domains above to this
environment's network egress allowlist (environment settings, not a repo
file) and re-run `$prepare-daytrade-plan`. This markdown file is
documentation only; it does not itself change proxy behavior.
