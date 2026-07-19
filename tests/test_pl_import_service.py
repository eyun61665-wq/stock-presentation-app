from pl_import_service import (
    acquire_pl_candidates,
    jquants_records,
    merge_candidates,
    merge_with_existing,
)


def test_jquants_records_keeps_partial_pl_and_latest_three_actual_years():
    financials = [
        {"fiscal_year": f"202{year}-03-31", "sales": year * 100, "operating_profit": None,
         "ordinary_profit": None, "net_income": year * 10, "eps": year, "average_shares": None}
        for year in range(2, 6)
    ]
    records = jquants_records({
        "financials": financials,
        "forecast": {"fiscal_year": "2026-03-31", "forecast_sales": 700,
                     "forecast_operating_profit": 70, "forecast_net_income": 40,
                     "forecast_eps": 20, "disclosed_date": "2025-05-10"},
    })
    actual = [row for row in records if row["result_type"] == "実績"]
    forecast = [row for row in records if row["result_type"] == "会社予想"]
    assert [row["fiscal_year"] for row in actual] == ["2023-03-31", "2024-03-31", "2025-03-31"]
    assert forecast[0]["sales"] == 700


def test_official_values_win_and_jquants_fills_missing_values():
    official = [{"fiscal_year": "2025-03-31", "result_type": "実績", "sales": 1_000,
                 "cost_of_sales": 600, "operating_profit": None, "source": "公式"}]
    jquants = [{"fiscal_year": "2025-03-31", "result_type": "実績", "sales": 999,
                "operating_profit": 100, "source": "J-Quants"}]
    merged = merge_candidates(official, jquants)
    assert merged[0]["sales"] == 1_000
    assert merged[0]["cost_of_sales"] == 600
    assert merged[0]["operating_profit"] == 100
    assert merged[0]["source"] == "公式"


def test_import_does_not_overwrite_manual_values_unless_confirmed():
    existing = [{"id": 9, "fiscal_year": "2025-03-31", "result_type": "実績",
                 "sales": 1_100, "operating_profit": None, "source": "手入力"}]
    imported = [{"fiscal_year": "2025-03-31", "result_type": "実績",
                 "sales": 1_000, "operating_profit": 100, "source": "J-Quants"}]
    safe = merge_with_existing(existing, imported)
    assert safe[0]["sales"] == 1_100
    assert safe[0]["operating_profit"] == 100
    assert safe[0]["id"] == 9
    overwritten = merge_with_existing(existing, imported, overwrite=True)
    assert overwritten[0]["sales"] == 1_000


class FakeClient:
    def equities_master(self):
        return [{"Code": "12340", "CoName": "テスト株式会社"}]

    def daily_bars(self, _code):
        return []

    def financial_summary(self, _code):
        return [{
            "DiscDate": "2025-05-10", "DocType": "FinancialStatements",
            "CurPerType": "FY", "CurPerEn": "2025-03-31",
            "Sales": "1000000000", "OP": "100000000", "OdP": "90000000",
            "NP": "60000000", "EPS": "60", "AvgSh": "1000000",
        }]


def test_acquire_candidates_falls_back_to_optional_jquants_without_company_hardcode():
    def failing_discovery(*_args, **_kwargs):
        raise RuntimeError("公式IRなし")

    result = acquire_pl_candidates(
        "1234", "テスト株式会社", jquants_api_key="key",
        discover=failing_discovery, client_factory=lambda _key: FakeClient(),
    )
    assert result.records[0]["fiscal_year"] == "2025-03-31"
    assert result.records[0]["sales"] == 1_000
    assert "J-Quants" in result.sources
    assert any("公式IRなし" in warning for warning in result.warnings)


def test_acquire_candidates_uses_official_records_without_api_key():
    result = acquire_pl_candidates(
        "9876", "汎用企業",
        discover=lambda *_args, **_kwargs: {"url": "https://example.test/ir"},
        official_loader=lambda *_args, **_kwargs: {
            "pl_records": [{"fiscal_year": "2025-12-31", "result_type": "実績", "sales": 321}],
            "warnings": [],
        },
    )
    assert result.records[0]["sales"] == 321
    assert result.sources == ["企業公式決算資料"]
