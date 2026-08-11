from src.candidate_pipeline import _screening_summary


def test_screening_counts_when_candidates_empty():
    screening_by_ticker = {}
    research_complete_tickers = {"1000", "2000"}

    summary = _screening_summary(screening_by_ticker, research_complete_tickers)

    assert summary["screening_input_count"] == 2
    assert summary["screening_incomplete_count"] == 2
    assert summary["screening_complete"] is False
