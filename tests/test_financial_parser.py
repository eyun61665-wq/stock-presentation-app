from financial_parser import (convert_to_million, infer_year, normalize_japanese_number,
                              normalize_label, parse_pl_text, statement_scope)


def test_amount_unit_and_japanese_number_conversion():
    assert convert_to_million("1,500,000", "千円") == 1500
    assert convert_to_million("(2.5)", "億円") == -250
    assert normalize_japanese_number("△１，２００") == -1200


def test_year_label_and_scope_detection():
    assert infer_year("2025年3月期 決算短信") == "2025"
    assert normalize_label("売上収益") == "sales"
    assert normalize_label("Operating profit") == "operating_profit"
    assert statement_scope("連結損益計算書") == "連結"
    assert statement_scope("個別財務諸表") == "単体"


def test_consolidated_pl_is_preferred_and_quarter_only_is_excluded():
    pages = ["2025年3月期 連結損益計算書\n（単位：百万円）\n売上高 1,000\n営業利益 100\n親会社株主に帰属する当期純利益 50",
             "2025年3月期 個別損益計算書\n（単位：百万円）\n売上高 999\n営業利益 99",
             "2025年3月期 第1四半期\n売上高 10"]
    records = parse_pl_text(pages, "test.pdf", "https://example.test/test.pdf", "2026-01-01")
    assert len(records) == 1
    assert records[0].scope == "連結"
    assert records[0].values["sales"] == 1000
