from __future__ import annotations

from typing import Any, Iterable


def render_sbi_report(
    recommendation: dict[str, Any],
    risk_result: dict[str, Any],
) -> str:
    decision = str(recommendation.get("decision", "")).strip()
    reasons = _as_text_list(recommendation.get("selection_reasons"))
    source_urls = _as_text_list(recommendation.get("source_urls"))

    if decision in {"NO_TRADE", "DATA_UNAVAILABLE"}:
        return _render_non_trade(recommendation, reasons, source_urls, decision)

    report_decision = "TRADE" if risk_result.get("status") == "PASS" else "REJECTED"
    lines = [
        "# 翌営業日 注文計画",
        "",
        f"対象日: {recommendation.get('target_date', '')}",
        f"戦略バージョン: {recommendation.get('strategy_version', '')}",
        f"設定SHA-256: {recommendation.get('config_sha256', '')}",
        *(_cutoff_line(recommendation)),
        *(_post_cutoff_line(recommendation)),
        "",
        "判定",
        report_decision,
        "",
        "銘柄:",
        f"{recommendation.get('ticker', '')} {recommendation.get('company_name', '')}".strip(),
        "",
        "数量:",
        f"{recommendation.get('shares', '')}株",
        "",
        "注文:",
        "現物買",
        "",
        "注文種別:",
        "IFDOCO入力候補",
        "",
        "買い発動価格:",
        f"{recommendation.get('entry_trigger', '')}円",
        "",
        "買い指値上限:",
        f"{recommendation.get('entry_limit', '')}円",
        "",
        "利確価格:",
        f"{recommendation.get('take_profit', '')}円",
        "",
        "損切り発動価格:",
        f"{recommendation.get('stop_loss', '')}円以下",
        "",
        "損切り発動後:",
        "成行候補。SBI証券の実画面で人間が仕様を確認する。",
        "",
        "必要資金:",
        f"{risk_result.get('required_capital_yen', '')}円",
        "",
        "想定損失:",
        f"{risk_result.get('expected_loss_yen', '')}円",
        "",
        "Risk Engine:",
        str(risk_result.get("status", "")),
    ]
    _append_section(lines, "Codex選定理由", reasons)
    _append_pipeline_sections(lines, recommendation)
    _append_section(lines, "参照データ", source_urls)
    violations = _as_text_list(risk_result.get("violations"))
    if violations:
        _append_section(lines, "拒否理由", violations)
    _append_section(
        lines,
        "注意事項",
        (
            "このレポートは手動入力用の注文候補であり、発注や利益を保証しない。",
            "逆指値発動後の成行では、スリッページにより実損失が想定500円を超える可能性がある。",
            "SBI証券へのログイン、入力、注文内容の訂正、取消は人間が行う。",
        ),
    )
    return "\n".join(lines).rstrip() + "\n"


def render_research_report(
    market_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    source_payload: dict[str, Any],
    performance: dict[str, Any],
) -> str:
    summary = candidate_pipeline.get("summary", {})
    counts = performance.get("counts", {})
    lines = [
        "# 市場調査レポート",
        "",
        f"対象日: {market_research.get('target_date', '')}",
        f"前営業日: {market_research.get('previous_trading_day', '')}",
        f"情報カットオフ: {market_research.get('research_cutoff', '')}",
        f"調査パイプライン完了: {_yes_no(summary.get('pipeline_complete'))}",
        "",
        "## Discovery結果",
    ]
    for route in market_research.get("discovery", []):
        lines.append(
            "- "
            f"{route.get('discovery_type', '')}: "
            f"{route.get('status', '')}, "
            f"result_count={route.get('result_count')}, "
            f"{route.get('source_url', '')}"
        )
    lines.extend(
        [
            f"- Discovery候補: {summary.get('discovered', 0)}件",
            "",
            "## 出典監査",
            f"- 出典取得試行: {counts.get('source_request_count', 0)}件",
            f"- 採用出典: {counts.get('adopted_source_count', 0)}件",
            f"- キャッシュヒット: {counts.get('cache_hit_count', 0)}件",
        ]
    )
    lines.extend(_source_attempt_status_lines(source_payload))
    lines.extend(
        [
            "",
            "## データ欠落・矛盾",
            f"- Research完了: {summary.get('research_complete', 0)}件",
            f"- Research未完了: {summary.get('research_incomplete', 0)}件",
            f"- DATA_UNAVAILABLE: {summary.get('data_unavailable', 0)}件",
            f"- Stage 2対象: {summary.get('stage2_target_count', 0)}件",
            f"- Stage 2完了: {summary.get('stage2_completed_count', 0)}件",
            f"- Stage 2外部要因による未到達: {summary.get('stage2_unavailable_count', 0)}件",
            f"- Stage 2未完了: {summary.get('stage2_incomplete_count', 0)}件",
        ]
    )
    reason_counts = summary.get("research_incomplete_reason_counts", {})
    if isinstance(reason_counts, dict) and reason_counts:
        lines.append(
            "- 未完了理由: "
            + ", ".join(f"{key}={value}" for key, value in sorted(reason_counts.items()))
        )
    lines.extend(_candidate_gap_lines(candidate_pipeline))
    lines.extend(
        [
            "",
            "## Rankingに使わなかったDiscovery情報",
            "- Discovery順位と表示値は候補発見理由としてのみ保存し、最終Rankingの評価値として使わない。",
            "",
            "## Codexによる比較評価",
            "- 比較対象はPython screening後のELIGIBLE候補だけとする。",
            "- この文書は最終構造化成果物から生成し、途中判断や注文プレビューは残さない。",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


def render_daily_report(
    market_research: dict[str, Any],
    candidate_pipeline: dict[str, Any],
    source_payload: dict[str, Any],
    performance: dict[str, Any],
    recommendation: dict[str, Any],
    risk_result: dict[str, Any],
) -> str:
    summary = candidate_pipeline.get("summary", {})
    lines = [
        "# 日次デイトレ計画レポート",
        "",
        f"対象日: {recommendation.get('target_date', '')}",
        f"前営業日: {market_research.get('previous_trading_day', '')}",
        f"情報カットオフ: {recommendation.get('research_cutoff', '')}",
        f"判定: {recommendation.get('decision', '')}",
        f"Risk Engine: {risk_result.get('status', '')}",
        f"調査パイプライン完了: {_yes_no(summary.get('pipeline_complete'))}",
        "",
        "## 調査パイプライン概要",
        f"- Discovery候補: {summary.get('discovered', 0)}件",
        f"- Research完了: {summary.get('research_complete', 0)}件",
        f"- Research未完了: {summary.get('research_incomplete', 0)}件",
        f"- DATA_UNAVAILABLE: {summary.get('data_unavailable', 0)}件",
        f"- ELIGIBLE: {summary.get('eligible', 0)}件",
        f"- REJECTED: {summary.get('rejected', 0)}件",
        "",
        "## 判断理由",
    ]
    reasons = _as_text_list(recommendation.get("selection_reasons"))
    lines.extend(f"- {reason}" for reason in reasons)
    if not reasons:
        lines.append("- 理由が記録されていない。")
    lines.extend(["", "## 欠落・矛盾"])
    gap_lines = _candidate_gap_lines(candidate_pipeline)
    lines.extend(gap_lines or ["- 欠落・矛盾は記録されていない。"])
    lines.extend(
        [
            "",
            "## 出典",
            f"- 出典取得試行: {performance.get('counts', {}).get('source_request_count', 0)}件",
            f"- 採用出典: {performance.get('counts', {}).get('adopted_source_count', 0)}件",
        ]
    )
    lines.extend(_source_attempt_status_lines(source_payload))
    _append_section(lines, "参照URL", _as_text_list(recommendation.get("source_urls")))
    _append_section(
        lines,
        "SBI確認事項",
        (
            "TRADEかつRisk Engine PASSの場合だけ、SBI証券の実画面で注文内容を人間が確認する。",
            "NO_TRADE、DATA_UNAVAILABLE、REJECTEDの場合は注文を作成しない。",
        ),
    )
    return "\n".join(lines).rstrip() + "\n"


def _render_non_trade(
    recommendation: dict[str, Any],
    reasons: list[str],
    source_urls: list[str],
    decision: str,
) -> str:
    lines = [
        "# 翌営業日 注文計画",
        "",
        f"対象日: {recommendation.get('target_date', '')}",
        f"戦略バージョン: {recommendation.get('strategy_version', '')}",
        f"設定SHA-256: {recommendation.get('config_sha256', '')}",
        *(_cutoff_line(recommendation)),
        *(_post_cutoff_line(recommendation)),
        "",
        "判定",
        decision,
    ]
    _append_section(lines, "理由", reasons or ["理由が記録されていない。"])
    _append_pipeline_sections(lines, recommendation)
    _append_section(lines, "参照データ", source_urls)
    note = (
        "必要な市場データが揃わず、取引判断まで到達していない。"
        if decision == "DATA_UNAVAILABLE"
        else "NO_TRADEは正常な結果であり、注文を作成しない。"
    )
    _append_section(lines, "注意事項", [note])
    return "\n".join(lines).rstrip() + "\n"


def _append_section(lines: list[str], title: str, values: Iterable[str]) -> None:
    items = list(values)
    if not items:
        return
    lines.extend(["", f"{title}:"])
    lines.extend(f"- {item}" for item in items)


def _append_pipeline_sections(lines: list[str], recommendation: dict[str, Any]) -> None:
    summary = recommendation.get("pipeline_summary")
    if isinstance(summary, dict):
        values = [
            f"Discovery候補: {summary.get('discovered', 0)}件",
            f"Research完了: {summary.get('research_complete', 0)}件",
            f"Research未完了: {summary.get('research_incomplete', 0)}件",
            f"DATA_UNAVAILABLE: {summary.get('data_unavailable', 0)}件",
            f"ELIGIBLE: {summary.get('eligible', 0)}件",
            f"REJECTED: {summary.get('rejected', 0)}件",
        ]
        if "pipeline_complete" in summary:
            values.append(f"調査パイプライン完了: {_yes_no(summary.get('pipeline_complete'))}")
        if "stage2_target_count" in summary:
            values.extend(
                [
                    f"Stage 2対象: {summary.get('stage2_target_count', 0)}件",
                    f"Stage 2未完了: {summary.get('stage2_incomplete_count', 0)}件",
                ]
            )
        _append_section(lines, "候補パイプライン概要", values)
    source_statuses = recommendation.get("source_statuses")
    if isinstance(source_statuses, list):
        values: list[str] = []
        for item in source_statuses:
            if not isinstance(item, dict):
                continue
            line = (
                f"{item.get('source_id', '')}: "
                f"{item.get('status', '')} {item.get('url', '')}"
            ).strip()
            if item.get("reason"):
                line = f"{line} ({item.get('reason')})"
            values.append(line)
        _append_section(lines, "主なSource状態", values)


def _cutoff_line(recommendation: dict[str, Any]) -> list[str]:
    cutoff = recommendation.get("research_cutoff")
    return [f"情報カットオフ: {cutoff}"] if cutoff else []


def _post_cutoff_line(recommendation: dict[str, Any]) -> list[str]:
    if recommendation.get("post_cutoff_information_status") != "OUT_OF_SCOPE":
        return []
    return [
        "cutoff後情報: 標準調査対象外。未調査の情報を確認済みとして扱わない。"
    ]


def _as_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _yes_no(value: Any) -> str:
    if value is True:
        return "はい"
    if value is False:
        return "いいえ"
    return "不明"


def _source_attempt_status_lines(source_payload: dict[str, Any]) -> list[str]:
    attempts = source_payload.get("source_attempts", [])
    if not isinstance(attempts, list):
        return []
    non_found = [
        attempt
        for attempt in attempts
        if isinstance(attempt, dict)
        and attempt.get("status") not in {"FOUND", "NOT_REQUIRED"}
    ]
    if not non_found:
        return ["- 主な出典不足: なし"]
    lines = ["- 主な出典不足:"]
    for attempt in non_found[:10]:
        lines.append(
            "  - "
            f"{attempt.get('source_id', '')} "
            f"{attempt.get('candidate_code', '')}: "
            f"{attempt.get('status', '')} "
            f"{attempt.get('url', '')}"
        )
    if len(non_found) > 10:
        lines.append(f"  - 他{len(non_found) - 10}件")
    return lines


def _candidate_gap_lines(candidate_pipeline: dict[str, Any]) -> list[str]:
    candidates = candidate_pipeline.get("candidates", [])
    if not isinstance(candidates, list):
        return []
    gap_lines: list[str] = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue
        status = candidate.get("pipeline_status")
        incomplete_reason = candidate.get("research_incomplete_reason")
        missing = _as_text_list(candidate.get("missing_requirements"))
        if status not in {"DATA_UNAVAILABLE", "DISCOVERED", "RESEARCH_IN_PROGRESS"}:
            continue
        parts = [str(status)]
        if incomplete_reason:
            parts.append(str(incomplete_reason))
        if missing:
            parts.append("missing=" + ",".join(missing))
        gap_lines.append(f"- {candidate.get('ticker', '')}: " + " / ".join(parts))
        if len(gap_lines) >= 10:
            break
    if len(gap_lines) == 10 and len(candidates) > 10:
        gap_lines.append("- 他の欠落候補はcandidate_pipeline.jsonを確認する。")
    return gap_lines
