from financial_document_service import (load_official_financials, merge_imported_pl,
                                          merge_imported_segments, select_annual_documents,
                                          select_recent_pl_records)
from financial_parser import parse_detailed_pl, parse_segment_statements


PL_PAGE = """
（連結損益計算書）
（単位：千円）
前連結会計年度（自 2023年6月1日 至 2024年5月31日）
当連結会計年度（自 2024年6月1日 至 2025年5月31日）
売上高 11,213,162 11,580,528
売上原価 6,928,594 7,254,684
売上総利益 4,284,567 4,325,843
販売費及び一般管理費合計 3,106,064 3,194,799
営業利益 1,178,502 1,131,044
営業外収益合計 272,391 260,244
営業外費用合計 101,862 121,963
経常利益 1,349,032 1,269,324
特別利益合計 － 116,996
特別損失合計 369,934 －
税金等調整前当期純利益 979,098 1,386,321
法人税等合計 291,539 392,222
親会社株主に帰属する当期純利益 687,223 986,017
"""

EPS_PAGE = """
前連結会計年度（自 2023年6月1日 至 2024年5月31日）
当連結会計年度（自 2024年6月1日 至 2025年5月31日）
１株当たり当期純利益 97.03円 139.23円
期中平均株式数（株） 7,082,178 7,082,178
"""

SEGMENT_PAGE = """
【セグメント情報】
「要素部品事業」と「システム製品事業」を報告セグメントとしております。
報告セグメント
当連結会計年度（自 2024年6月1日 至 2025年5月31日）
（単位：千円）
外部顧客への売上高 9,734,872 1,845,656 11,580,528 － 11,580,528
セグメント利益 1,642,107 72,885 1,714,992 (583,948) 1,131,044
"""


def test_detailed_pl_extracts_all_statement_rows_and_average_shares():
    records = parse_detailed_pl(
        ["2025年7月11日", PL_PAGE, EPS_PAGE], "2025年5月期決算短信", "https://example.com/a.pdf", "now"
    )
    current = records[-1]
    assert current["fiscal_year"] == "2025-05-31"
    assert current["sales"] == 11580.528
    assert current["cost_of_sales"] == 7254.684
    assert current["gross_profit"] == 4325.843
    assert current["sga_expenses"] == 3194.799
    assert current["ordinary_profit"] == 1269.324
    assert current["extraordinary_loss"] == 0
    assert current["income_taxes"] == 392.222
    assert current["average_shares"] == 7.082178
    assert current["reported_eps"] == 139.23


def test_segment_statement_extracts_external_sales_and_profit():
    records = parse_segment_statements(
        ["2025年7月11日", SEGMENT_PAGE], "2025年5月期決算短信", "https://example.com/a.pdf", "now"
    )
    assert [(row["segment_name"], row["sales"], row["operating_profit"]) for row in records] == [
        ("要素部品事業", 9734.872, 1642.107),
        ("システム製品事業", 1845.656, 72.885),
    ]


def test_select_annual_documents_excludes_quarters_and_forecast_revisions():
    documents = [
        {"title": "2026年5月期 第3四半期決算短信", "url": "q3.pdf"},
        {"title": "2026年5月期 通期決算短信", "url": "fy.pdf"},
        {"title": "通期業績予想の修正", "url": "forecast.pdf"},
        {"title": "2025年5月期 決算短信", "url": "fy2.pdf"},
    ]
    assert [row["url"] for row in select_annual_documents(documents)] == ["fy.pdf", "fy2.pdf"]


def test_select_annual_documents_excludes_quarter_title_without_period_suffix():
    documents = [
        {"title": "2026年3月期第3四半決算補足資料", "url": "q3.pdf"},
        {"title": "2026年3月期 決算補足資料", "url": "fy.pdf"},
        {"title": "2026年3月期決算短信〔日本基準〕（連結）", "url": "tanshin.pdf"},
    ]

    assert [row["url"] for row in select_annual_documents(documents)] == [
        "fy.pdf", "tanshin.pdf"
    ]


def test_select_annual_documents_accepts_year_end_title_without_annual_word():
    documents = [
        {"title": "2026年2月期決算短信〔日本基準〕(連結)", "url": "fy.pdf"},
        {"title": "2026年2月期第3四半期決算短信〔日本基準〕(連結)", "url": "q3.pdf"},
    ]
    assert select_annual_documents(documents) == [documents[0]]


def test_select_annual_documents_includes_annual_presentation_for_segments():
    documents = [
        {"title": "2026年3月期 決算説明資料", "url": "presentation.pdf"},
        {"title": "2026年3月期 第3四半期 決算説明資料", "url": "q3.pdf"},
    ]
    assert select_annual_documents(documents) == [documents[0]]


def test_select_annual_documents_excludes_correction_notice():
    documents = [
        {"title": "（訂正）2026年3月期 決算短信〔日本基準〕（連結）", "url": "correction.pdf"},
        {"title": "2026年3月期 決算短信〔日本基準〕（連結）", "url": "annual.pdf"},
    ]
    assert select_annual_documents(documents) == [documents[1]]


def test_one_broken_pdf_does_not_stop_other_years(monkeypatch):
    documents = [
        {"title": "2026年3月期 決算短信", "url": "broken.pdf"},
        {"title": "2025年3月期 決算短信", "url": "working.pdf"},
    ]
    monkeypatch.setattr("financial_document_service.fetch_ir_documents", lambda *_args, **_kwargs: documents)

    def fake_download(url, refresh=False):
        if url == "broken.pdf":
            from data_sources import DataSourceError
            raise DataSourceError("download failed")
        return b"%PDF mock", {"retrieved_at": "now"}

    monkeypatch.setattr("financial_document_service.download_pdf", fake_download)
    monkeypatch.setattr(
        "financial_document_service.parse_financial_document",
        lambda *_args, **_kwargs: {
            "pl_records": [{"fiscal_year": "2025-03-31", "sales": 100}],
            "segment_records": [],
            "warnings": [],
        },
    )

    result = load_official_financials("https://example.com/ir")

    assert result["pl_records"][0]["sales"] == 100
    assert result["documents"] == [documents[1]]
    assert "別の年度" in result["warnings"][0]


def test_confirmed_import_replaces_actual_year_only():
    existing_pl = [
        {"fiscal_year": "2025-05-31", "result_type": "実績", "sales": 1},
        {"fiscal_year": "2027-05-31", "result_type": "自分予想", "sales": 3},
    ]
    imported_pl = [{"fiscal_year": "2025-05-31", "result_type": "実績", "sales": 2}]
    merged_pl = merge_imported_pl(existing_pl, imported_pl)
    assert [row["sales"] for row in merged_pl] == [2, 3]

    existing_segments = [
        {"fiscal_year": "2025-05-31", "result_type": "実績", "segment_name": "旧", "sales": 1},
        {"fiscal_year": "2027-05-31", "result_type": "自分予想", "segment_name": "予想", "sales": 3},
    ]
    imported_segments = [
        {"fiscal_year": "2025-05-31", "result_type": "実績", "segment_name": "新", "sales": 2}
    ]
    merged_segments = merge_imported_segments(existing_segments, imported_segments)
    assert {(row["fiscal_year"], row["segment_name"]) for row in merged_segments} == {
        ("2025-05-31", "新"), ("2027-05-31", "予想")
    }


def test_imported_blank_does_not_erase_manual_pl_value():
    existing = [{
        "fiscal_year": "2025-05-31",
        "result_type": "実績",
        "sales": 100,
        "ordinary_profit": 12,
    }]
    imported = [{
        "fiscal_year": "2025-05-31",
        "result_type": "実績",
        "sales": 110,
        "ordinary_profit": None,
    }]

    merged = merge_imported_pl(existing, imported)

    assert merged[0]["sales"] == 110
    assert merged[0]["ordinary_profit"] == 12


def test_recent_pl_keeps_three_actual_years_and_forecasts():
    records = [
        {"fiscal_year": f"{year}.3", "result_type": "実績", "sales": year}
        for year in range(2021, 2026)
    ] + [{"fiscal_year": "2026.3", "result_type": "会社予想", "sales": 300}]
    selected = select_recent_pl_records(records, 3)
    assert [(row["fiscal_year"], row["result_type"]) for row in selected] == [
        ("2023.3", "実績"), ("2024.3", "実績"), ("2025.3", "実績"),
        ("2026.3", "会社予想"),
    ]


def test_company_forecast_is_not_changed_to_actual_when_imported():
    merged = merge_imported_pl([], [{
        "fiscal_year": "2026.3", "result_type": "会社予想", "sales": 300,
    }])
    assert merged[0]["result_type"] == "会社予想"


def test_single_segment_company_uses_annual_pl_as_segment(monkeypatch):
    document = {"title": "2025年11月期 決算短信", "url": "annual.pdf"}
    monkeypatch.setattr("financial_document_service.fetch_ir_documents", lambda *_args, **_kwargs: [document])
    monkeypatch.setattr(
        "financial_document_service.download_pdf",
        lambda *_args, **_kwargs: (b"%PDF mock", {"retrieved_at": "now"}),
    )
    monkeypatch.setattr(
        "financial_document_service.parse_financial_document",
        lambda *_args, **_kwargs: {
            "pl_records": [{
                "fiscal_year": "2025-11-30", "result_type": "実績",
                "sales": 3895.112, "operating_profit": -215.316,
                "source_url": "annual.pdf",
            }],
            "segment_records": [], "segment_metrics": [],
            "single_segment_name": "IoP Cloud事業", "warnings": [],
        },
    )

    result = load_official_financials("https://example.com/ir", max_years=3)

    assert result["pl_records"][0]["fiscal_year"] == "2025.11"
    assert result["segment_records"] == [{
        "fiscal_year": "2025.11", "result_type": "実績",
        "segment_name": "IoP Cloud事業", "sales": 3895.112,
        "operating_profit": -215.316, "source": "決算短信PDF（単一セグメント）",
        "note": "annual.pdf",
    }]
