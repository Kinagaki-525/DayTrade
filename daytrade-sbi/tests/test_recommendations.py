import csv

from src.recommendations import (
    DEFAULT_RECOMMENDATIONS_PATH,
    RECOMMENDATION_COLUMNS,
    recommendation_to_row,
)


def test_recommendation_row_does_not_infer_execution_results():
    row = recommendation_to_row(
        {
            "target_date": "2026-08-10",
            "decision": "NO_TRADE",
            "notes": "required data unavailable",
        },
        {"status": "NOT_APPLICABLE"},
    )

    assert row["decision"] == "NO_TRADE"
    assert row["order_submitted"] == ""
    assert row["entry_triggered"] == ""
    assert row["entry_filled"] == ""


def test_repository_recommendation_log_uses_current_header():
    with DEFAULT_RECOMMENDATIONS_PATH.open(
        "r", encoding="utf-8-sig", newline=""
    ) as csv_file:
        header = next(csv.reader(csv_file))

    assert header == list(RECOMMENDATION_COLUMNS)
