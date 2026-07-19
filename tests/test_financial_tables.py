import pandas as pd

from financial_tables import (
    calculate_pl_records,
    metadata_from_entries,
    normalize_metadata,
    pl_display_frame,
    pl_input_frame,
    pl_records_from_frame,
    segment_display_frame,
    segment_combined_input_frame,
    segment_records_from_combined_frame,
    segment_totals_frame,
    segment_input_frame,
    segment_records_from_frames,
    segment_analysis_frame,
    segment_metrics_input_frame,
    segment_metrics_from_frame,
)


PL_ENTRIES = [
    {
        "fiscal_year": "2024.5", "result_type": "実績", "sales": 1000,
        "cost_of_sales": 600, "gross_profit": 400, "sga_expenses": 300,
        "operating_profit": 100, "ordinary_profit": 110, "pretax_profit": 105,
        "income_taxes": 30, "net_income": 75, "shares_outstanding": 10,
    },
    {
        "fiscal_year": "2025.5", "result_type": "自分予想", "sales": 1200,
        "cost_of_sales": 700, "gross_profit": 500, "sga_expenses": 350,
        "operating_profit": 150, "ordinary_profit": 155, "pretax_profit": 150,
        "income_taxes": 45, "net_income": 105, "shares_outstanding": 10,
    },
]


def test_pl_horizontal_matrix_roundtrip_and_calculations():
    metadata = normalize_metadata(metadata_from_entries(PL_ENTRIES))
    matrix = pl_input_frame(PL_ENTRIES, metadata)
    assert list(matrix.columns) == ["科目", "2024.5｜実績", "2025.5｜自分予想"]
    records = pl_records_from_frame(matrix, metadata)
    calculated = calculate_pl_records(records)
    assert calculated[0]["operating_margin"] == 10
    assert calculated[1]["sales_growth"] == 20
    assert calculated[1]["eps"] == 10.5
    display = pl_display_frame(records)
    eps_row = display[display["科目"] == "EPS（円）"].iloc[0]
    assert eps_row["2025.5｜自分予想"] == "10.50"


def test_segment_horizontal_matrix_and_total():
    entries = [
        {"fiscal_year": "2025.3", "result_type": "実績", "segment_name": "製造", "sales": 70, "operating_profit": 7},
        {"fiscal_year": "2025.3", "result_type": "実績", "segment_name": "流通", "sales": 30, "operating_profit": 3},
    ]
    metadata = [{"fiscal_year": "2025.3", "result_type": "実績"}]
    sales = segment_input_frame(entries, metadata, ["製造", "流通"], "sales")
    profit = segment_input_frame(entries, metadata, ["製造", "流通"], "operating_profit")
    records = segment_records_from_frames(sales, profit, metadata)
    display = segment_display_frame(records, metadata, "sales")
    assert display.iloc[0]["セグメント"] == "合計"
    assert display.iloc[0]["2025.3｜実績"] == "100"


def test_blank_values_are_not_converted_to_zero():
    metadata = [{"fiscal_year": "2026.3", "result_type": "会社予想"}]
    frame = pd.DataFrame({"科目": ["売上高（百万円）"], "2026.3｜会社予想": [None]})
    records = pl_records_from_frame(frame, metadata)
    assert records[0]["sales"] is None


def test_same_year_company_and_self_forecasts_are_separate_columns():
    entries = [
        {"fiscal_year": "2026.3", "result_type": "会社予想", "sales": 1100},
        {"fiscal_year": "2026.3", "result_type": "自分予想", "sales": 1250},
    ]
    metadata = normalize_metadata(metadata_from_entries(entries))
    assert metadata == [
        {"fiscal_year": "2026.3", "result_type": "会社予想"},
        {"fiscal_year": "2026.3", "result_type": "自分予想"},
    ]
    matrix = pl_input_frame(entries, metadata)
    assert matrix.loc[0, "2026.3｜会社予想"] == 1100
    assert matrix.loc[0, "2026.3｜自分予想"] == 1250


def test_segment_combined_editor_roundtrip_and_totals():
    entries = [
        {"fiscal_year": "2025.3", "result_type": "実績", "segment_name": "製造", "sales": 70, "operating_profit": 7},
        {"fiscal_year": "2025.3", "result_type": "実績", "segment_name": "流通", "sales": 30, "operating_profit": 3},
    ]
    metadata = [{"fiscal_year": "2025.3", "result_type": "実績"}]
    combined = segment_combined_input_frame(entries, metadata, ["製造", "流通"])
    assert list(combined["項目"]) == ["売上高", "利益", "売上高", "利益"]
    records = segment_records_from_combined_frame(combined, metadata)
    totals = segment_totals_frame(records, metadata)
    assert totals.loc[0, "2025.3｜実績"] == "100"
    assert totals.loc[1, "2025.3｜実績"] == "10"


def test_segment_complete_table_has_years_across_and_kpis_down():
    entries = [
        {"fiscal_year": "2024.3", "result_type": "実績", "segment_name": "主力事業", "sales": 5000},
        {"fiscal_year": "2025.3", "result_type": "実績", "segment_name": "主力事業", "sales": 6000},
        {"fiscal_year": "2024.3", "result_type": "実績", "segment_name": "人材事業", "sales": 1000},
        {"fiscal_year": "2025.3", "result_type": "実績", "segment_name": "人材事業", "sales": 1500},
    ]
    metrics = [
        {"fiscal_year": "2024.3", "result_type": "実績", "row_label": "契約会社数", "value": 1346, "unit": "社", "display_order": 1},
        {"fiscal_year": "2025.3", "result_type": "実績", "row_label": "契約会社数", "value": 1443, "unit": "社", "display_order": 1},
    ]
    metadata = [
        {"fiscal_year": "2024.3", "result_type": "実績"},
        {"fiscal_year": "2025.3", "result_type": "実績"},
    ]

    frame = segment_analysis_frame(entries, metadata, metrics)

    assert list(frame.columns) == ["科目", "2024.3｜実績", "2025.3｜実績"]
    assert frame.iloc[0].to_dict() == {
        "科目": "売上高（百万円）", "2024.3｜実績": "6,000", "2025.3｜実績": "7,500"
    }
    assert "契約会社数（社）" in frame["科目"].tolist()


def test_segment_kpi_editor_roundtrip():
    metadata = [{"fiscal_year": "2025.3", "result_type": "実績"}]
    metrics = [{
        "fiscal_year": "2025.3", "result_type": "実績", "row_label": "平均単価",
        "value": 4284, "unit": "千円", "display_order": 0,
    }]
    frame = segment_metrics_input_frame(metrics, metadata)
    records = segment_metrics_from_frame(frame, metadata)
    assert records[0]["row_label"] == "平均単価"
    assert records[0]["value"] == 4284
    assert records[0]["unit"] == "千円"
