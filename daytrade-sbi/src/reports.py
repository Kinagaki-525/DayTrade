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
        "# 翌営業日注文案",
        "",
        f"対象日: {recommendation.get('target_date', '')}",
        f"戦略バージョン: {recommendation.get('strategy_version', '')}",
        f"設定SHA-256: {recommendation.get('config_sha256', '')}",
        "",
        "判定:",
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
        "成行候補（SBI証券の実画面で人間が仕様を確認する）",
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
    _append_section(lines, "参照データ", source_urls)
    violations = _as_text_list(risk_result.get("violations"))
    if violations:
        _append_section(lines, "拒否理由", violations)
    _append_section(
        lines,
        "注意事項",
        (
            "このレポートは手動入力用の注文候補であり、発注や利益を保証しない。",
            "逆指値発動後の成行等では、スリッページにより実損失が想定500円を超える可能性がある。",
            "SBI証券へのログイン・入力・注文・訂正・取消は人間が行う。",
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
        "# 翌営業日注文案",
        "",
        f"対象日: {recommendation.get('target_date', '')}",
        f"戦略バージョン: {recommendation.get('strategy_version', '')}",
        f"設定SHA-256: {recommendation.get('config_sha256', '')}",
        "",
        "判定:",
        decision,
    ]
    _append_section(lines, "理由", reasons or ["理由が記録されていない"])
    _append_section(lines, "参照データ", source_urls)
    note = (
        "必要な市場データが揃わず、取引判断まで到達していない。"
        if decision == "DATA_UNAVAILABLE"
        else "NO_TRADEは正常な結果であり、注文は作成しない。"
    )
    _append_section(lines, "注意事項", [note])
    return "\n".join(lines).rstrip() + "\n"


def _append_section(lines: list[str], title: str, values: Iterable[str]) -> None:
    items = list(values)
    if not items:
        return
    lines.extend(["", f"{title}:"])
    lines.extend(f"- {item}" for item in items)


def _as_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]
