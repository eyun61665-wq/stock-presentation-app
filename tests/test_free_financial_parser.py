import io
import zipfile
from datetime import date, timedelta

from edinet_code_master import find_edinet_company, parse_edinet_code_list
from edinet_financial_service import find_annual_report_documents
from free_financial_parser import parse_layout_tables
from xbrl_extractor import extract_xbrl


def test_free_table_parser_extracts_detailed_pl_segments_and_kpis():
    layouts = [{
        "page": 8,
        "text": "決算概要（単位：百万円）",
        "tables": [[
            ["科目", "2024.3 実績", "2025.3 実績"],
            ["売上高", "7,002", "8,801"],
            ["売上原価", "1,880", "2,100"],
            ["売上総利益", "5,122", "6,701"],
            ["営業利益", "1,000", "1,300"],
            ["親会社株主に帰属する当期純利益", "700", "900"],
            ["① サイバーセキュリティ事業", "5,122", "6,182"],
            ["② セキュリティ教育事業", "705", "997"],
            ["契約会社数（社）", "1,346", "1,443"],
            ["平均単価（千円）", "3,805", "4,284"],
        ]],
    }]
    result = parse_layout_tables(layouts, "決算資料", "https://example.test/report.pdf", "2026-01-01")
    assert result["pl_records"][0]["sales"] == 7002
    assert result["pl_records"][1]["gross_profit"] == 6701
    assert [(row["fiscal_year"], row["segment_name"], row["sales"]) for row in result["segment_records"]] == [
        ("2024.3", "サイバーセキュリティ事業", 5122),
        ("2024.3", "セキュリティ教育事業", 705),
        ("2025.3", "サイバーセキュリティ事業", 6182),
        ("2025.3", "セキュリティ教育事業", 997),
    ]
    assert any(row["row_label"] == "契約会社数(社)" and row["value"] == 1443 for row in result["segment_metrics"])


def test_reportable_segment_matrix_extracts_external_sales_and_profit_only():
    layouts = [{
        "page": 20,
        "text": "当連結会計年度（自 2024年6月1日 至 2025年5月31日）（単位：千円）",
        "tables": [[
            ["", "報告セグメント", "", "", "調整額", "連結財務諸表計上額"],
            ["", "要素部品事業", "システム製品事業", "計", "", ""],
            ["売上高 外部顧客への売上高 セグメント間の内部売上高又は振替高",
             "9,734,872 22,867", "1,845,656 74,762", "11,580,528 97,629", "- (97,629)", "11,580,528 -"],
            ["セグメント利益", "1,642,107", "72,885", "1,714,992", "(583,948)", "1,131,044"],
            ["セグメント資産", "13,229,459", "1,722,825", "14,952,285", "5,387,817", "20,340,102"],
        ]],
    }]
    result = parse_layout_tables(layouts, "本決算短信", "https://example.test/tanshin.pdf", "2026-01-01")
    assert result["pl_records"] == []
    assert [(row["fiscal_year"], row["segment_name"], row["sales"], row["operating_profit"])
            for row in result["segment_records"]] == [
        ("2025.5", "システム製品事業", 1845.656, 72.885),
        ("2025.5", "要素部品事業", 9734.872, 1642.107),
    ]


def test_notes_and_balance_sheet_rows_are_not_misclassified_as_segments():
    layouts = [{
        "page": 9,
        "text": "連結貸借対照表（単位：千円）2025年5月31日",
        "tables": [[
            ["", "前連結会計年度 2024年5月31日", "当連結会計年度 2025年5月31日"],
            ["セグメント資産", "13,499,923", "13,229,459"],
            ["法人税、住民税及び事業税", "252,285", "470,640"],
        ]],
    }]
    result = parse_layout_tables(layouts, "本決算短信", "https://example.test/tanshin.pdf", "2026-01-01")
    assert result["pl_records"] == []
    assert result["segment_records"] == []
    assert result["segment_metrics"] == []


def test_edinet_code_list_maps_four_digit_stock_code():
    csv_text = "\n".join([
        "ダウンロード実行日,2026年07月19日現在,件数,1件",
        "ＥＤＩＮＥＴコード,提出者種別,上場区分,連結の有無,資本金,決算日,提出者名,提出者名（英字）,提出者名（ヨミ）,所在地,提出者業種,証券コード,提出者法人番号",
        '"E00001","内国法人・組合","上場","有","100","3月31日","テスト株式会社","TEST","テスト","東京","情報・通信業","77130","1"',
    ])
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("EdinetcodeDlInfo.csv", csv_text.encode("cp932"))
    rows = parse_edinet_code_list(buffer.getvalue())
    assert find_edinet_company(rows, "7713") == {
        "edinet_code": "E00001", "stock_code": "7713", "stock_code5": "77130",
        "company_name": "テスト株式会社", "fiscal_year_end": "3月31日", "industry": "情報・通信業",
    }


def test_edinet_report_search_uses_fiscal_year_window():
    period_end = date(2025, 3, 31)
    filing_date = period_end + timedelta(days=90)
    report = {"edinetCode": "E00001", "docTypeCode": "120", "docID": "S100TEST"}
    found = find_annual_report_documents(
        "E00001", "3月31日", 1,
        lambda target: [report] if target == filing_date else [],
        today=date(2025, 12, 31),
    )
    assert found == [report]


def test_xbrl_extracts_reportable_segment_sales_and_profit():
    xml = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:xbrldi="http://xbrl.org/2006/xbrldi" xmlns:j="urn:test">
      <xbrli:context id="CurrentYearConsolidated"><xbrli:entity><xbrli:identifier scheme="x">a</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2024-04-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context>
      <xbrli:context id="CurrentYearConsolidated_Security"><xbrli:entity><xbrli:identifier scheme="x">a</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="j:OperatingSegmentsAxis">j:SecurityBusinessMember</xbrldi:explicitMember></xbrli:segment></xbrli:entity><xbrli:period><xbrli:startDate>2024-04-01</xbrli:startDate><xbrli:endDate>2025-03-31</xbrli:endDate></xbrli:period></xbrli:context>
      <j:NetSales contextRef="CurrentYearConsolidated">9000000000</j:NetSales>
      <j:RevenueFromExternalCustomers contextRef="CurrentYearConsolidated_Security">6000000000</j:RevenueFromExternalCustomers>
      <j:SegmentProfitLoss contextRef="CurrentYearConsolidated_Security">1200000000</j:SegmentProfitLoss>
    </xbrli:xbrl>'''
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("XBRL/PublicDoc/sample.xbrl", xml)
    result = extract_xbrl(buffer.getvalue())
    assert result["sales"] == 9000
    assert result["segments"] == [{
        "fiscal_year": "2025-03-31", "segment_name": "SecurityBusiness",
        "sales": 6000, "operating_profit": 1200,
    }]
