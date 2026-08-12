from src.reports import render_daily_report, render_research_report, render_sbi_report


def pipeline_summary(**overrides):
    summary = {
        "discovered": 1,
        "research_complete": 1,
        "research_incomplete": 0,
        "data_unavailable": 0,
        "screened": 1,
        "eligible": 1,
        "rejected": 0,
        "pipeline_complete": True,
        "stage2_target_count": 1,
        "stage2_completed_count": 1,
        "stage2_unavailable_count": 0,
        "stage2_incomplete_count": 0,
        "coverage_rate": 1,
        "research_incomplete_reason_counts": {},
        "screening_input_count": 1,
        "screening_pass_count": 1,
        "screening_reject_count": 0,
        "screening_data_unavailable_count": 0,
        "screening_incomplete_count": 0,
        "screening_complete": True,
        "candidate_count_consistent": True,
        "all_enabled_rules_evaluated": True,
        "screening_rule_counts": [],
        "ranking_complete": False,
    }
    summary.update(overrides)
    return summary


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

    assert "判定\nTRADE" in report
    assert "戦略バージョン: v1" in report
    assert f"設定SHA-256: {'a' * 64}" in report
    assert "SBI証券へのログイン、入力、注文内容の訂正、取消は人間が行う。" in report
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

    assert "判定\nNO_TRADE" in report
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
            "pipeline_summary": pipeline_summary(
                discovered=99,
                research_complete=0,
                data_unavailable=99,
                screened=0,
                eligible=0,
                screening_input_count=0,
                screening_pass_count=0,
            ),
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

    assert "判定\nDATA_UNAVAILABLE" in report
    assert "取引判断まで到達していない" in report
    assert "情報カットオフ: 2026-08-07T20:00:00+09:00" in report
    assert "cutoff後情報: 標準調査対象外" in report
    assert "未調査の情報を確認済みとして扱わない" in report
    assert "候補パイプライン概要" in report
    assert "JPX_TDNET: PARSE_FAILED" in report


def test_structured_research_and_daily_reports_do_not_keep_intermediate_notes():
    market_research = {
        "target_date": "2026-08-10",
        "previous_trading_day": "2026-08-07",
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "discovery": [
            {
                "discovery_type": "VOLUME_RANKING",
                "status": "FOUND",
                "result_count": 50,
                "source_url": "https://finance.yahoo.co.jp/stocks/ranking/volume?market=all",
            }
        ],
    }
    candidate_pipeline = {
        "summary": pipeline_summary(),
        "candidates": [],
    }
    source_payload = {"source_attempts": []}
    performance = {
        "counts": {
            "source_request_count": 1,
            "adopted_source_count": 1,
            "cache_hit_count": 0,
        }
    }
    recommendation = {
        "target_date": "2026-08-10",
        "research_cutoff": "2026-08-07T20:00:00+09:00",
        "decision": "NO_TRADE",
        "selection_reasons": ["該当候補なし"],
        "source_urls": [],
    }
    risk_result = {"status": "NOT_APPLICABLE"}

    research_report = render_research_report(
        market_research,
        candidate_pipeline,
        source_payload,
        performance,
    )
    daily_report = render_daily_report(
        market_research,
        candidate_pipeline,
        source_payload,
        performance,
        recommendation,
        risk_result,
    )

    combined = research_report + daily_report
    assert "市場調査レポート" in research_report
    assert "日次デイトレ計画レポート" in daily_report
    assert "may screen" not in combined
    assert "order-plan preview" not in combined
    assert "調査パイプライン完了: はい" in combined
    assert "Screening PASS: 1件" in combined
    assert "Selectionは本番設定で無効のため、TRADE候補は確定しない。" in combined
