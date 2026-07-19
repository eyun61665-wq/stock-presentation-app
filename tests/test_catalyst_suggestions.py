import pytest

from catalyst_suggestion_service import next_fiscal_year, suggest_catalysts


def test_next_fiscal_year_keeps_month_and_day():
    assert next_fiscal_year("2025-05-31") == "2026-05-31"
    assert next_fiscal_year("2025.3") == "2026.3"


def test_segment_based_suggestion_has_sales_impact_range():
    project = {"business_description": "光学製品の開発・製造と海外販売"}
    pl = [{"fiscal_year": "2025.3", "result_type": "実績", "sales": 2000}]
    segments = [
        {"fiscal_year": "2025.3", "result_type": "実績", "segment_name": "光学", "sales": 1200},
        {"fiscal_year": "2025.3", "result_type": "実績", "segment_name": "サービス", "sales": 800},
    ]
    suggestions = suggest_catalysts(project, pl, segments)
    first = suggestions[0]
    assert first["name"] == "光学の売上拡大"
    assert first["standard_rate"] == 5
    assert first["standard_impact"] == pytest.approx(60)
    assert first["low_impact"] < first["standard_impact"] < first["high_impact"]
    assert first["target_year"] == "2026.3"


def test_no_actual_sales_returns_no_unfounded_suggestions():
    assert suggest_catalysts({}, [], []) == []


def test_public_law_change_becomes_sourced_catalyst_with_formula():
    pl = [{"fiscal_year": "2025.3", "result_type": "実績", "sales": 10000}]
    context = [{
        "title": "新ガイドラインへの対応を義務化",
        "url": "https://example.go.jp/rule",
        "source": "官公庁",
        "published": "2026-01-01",
        "category": "法令・ルール変更",
    }]
    result = suggest_catalysts({}, pl, [], context)
    assert result[0]["context_category"] == "法令・ルール変更"
    assert result[0]["source_url"] == "https://example.go.jp/rule"
    assert result[0]["standard_impact"] == 200
