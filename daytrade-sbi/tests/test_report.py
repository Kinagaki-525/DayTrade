from src.reports import render_sbi_report


def test_passed_trade_report_contains_manual_order_warning():
    recommendation = {
        "target_date": "2026-08-10",
        "strategy_version": "v1",
        "config_sha256": "a" * 64,
        "decision": "TRADE",
        "ticker": "1234",
        "company_name": "Example Co.",
        "shares": 100,
        "entry_trigger": "401",
        "entry_limit": "402",
        "take_profit": "410",
        "stop_loss": "397",
        "selection_reasons": ["confirmed fact based comparison"],
        "source_urls": ["https://example.test/market"],
    }
    risk = {
        "status": "PASS",
        "required_capital_yen": "40200",
        "expected_loss_yen": "500",
        "violations": [],
    }

    report = render_sbi_report(recommendation, risk)

    assert "判定:\nTRADE" in report
    assert "戦略バージョン: v1" in report
    assert f"設定SHA-256: {'a' * 64}" in report
    assert "SBI証券へのログイン・入力・注文・訂正・取消は人間が行う" in report
    assert "実損失が想定500円を超える可能性" in report


def test_no_trade_report_is_a_normal_result():
    report = render_sbi_report(
        {
            "target_date": "2026-08-10",
            "strategy_version": "v1",
            "config_sha256": "a" * 64,
            "decision": "NO_TRADE",
            "selection_reasons": ["required data unavailable"],
            "source_urls": [],
        },
        {"status": "NOT_APPLICABLE"},
    )

    assert "判定:\nNO_TRADE" in report
    assert "NO_TRADEは正常な結果" in report


def test_data_unavailable_report_is_not_no_trade():
    report = render_sbi_report(
        {
            "target_date": "2026-08-10",
            "strategy_version": "v1",
            "config_sha256": "a" * 64,
            "decision": "DATA_UNAVAILABLE",
            "research_cutoff": "2026-08-07T20:00:00+09:00",
            "post_cutoff_information_status": "OUT_OF_SCOPE",
            "pipeline_summary": {
                "discovered": 99,
                "research_complete": 0,
                "research_incomplete": 0,
                "data_unavailable": 99,
                "screened": 0,
                "eligible": 0,
                "rejected": 0,
            },
            "source_statuses": [
                {
                    "source_id": "JPX_TDNET",
                    "status": "PARSE_FAILED",
                    "url": "https://example.test/tdnet",
                    "reason": "cutoff内TDnetを解析できなかった",
                }
            ],
            "selection_reasons": ["OHLCV secondary source missing"],
            "source_urls": [],
        },
        {"status": "NOT_APPLICABLE"},
    )

    assert "判定:\nDATA_UNAVAILABLE" in report
    assert "取引判断まで到達していない" in report
    assert "情報カットオフ: 2026-08-07T20:00:00+09:00" in report
    assert "cutoff後情報: 標準調査対象外" in report
    assert "未調査の情報を「0件確認済み」とは扱わない" in report
    assert "候補パイプライン概要" in report
    assert "JPX_TDNET: PARSE_FAILED" in report
