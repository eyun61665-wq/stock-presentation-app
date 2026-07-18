from database import (delete_project, get_catalyst, get_pl_entries, get_project, get_scenarios, get_segment_entries,
                      initialize_database, save_catalyst, save_pl_entries, save_project, save_scenarios, save_segment_entries)
import sqlite3


def test_save_reload_and_delete_all_project_data(tmp_path):
    db_path = tmp_path / "test.db"
    initialize_database(db_path)
    project_id = save_project({"project_name": "テスト", "current_price": 1000, "shares_outstanding": 10}, db_path)
    save_pl_entries(project_id, [{"年度": "2025/3", "実績／会社予想／自分予想": "実績", "売上高（百万円）": 2000,
                                  "営業利益（百万円）": 200, "純利益（百万円）": 500, "平均株式数（百万株）": 10}], db_path)
    save_catalyst(project_id, {"catalyst_name": "新製品", "target_year": "2026/3", "description": "テスト",
                               "target_count": 1000, "target_rate": 50, "capture_rate": 10, "price_per_case": 2,
                               "evidence_category": "自分の仮定", "source": "試算", "note": "確認中"}, db_path)
    save_scenarios(project_id, [{"scenario_name": name, "base_sales": 2000, "existing_sales_growth_rate": 5,
                                 "catalyst_sales": 100, "operating_margin_rate": 10, "effective_tax_rate": 30,
                                 "shares_outstanding": 10, "per": 20} for name in ["弱気", "標準", "強気"]], db_path)
    assert get_project(project_id, db_path)["shares_outstanding"] == 10
    assert get_pl_entries(project_id, db_path)[0]["shares_outstanding"] == 10
    assert get_catalyst(project_id, db_path)["target_rate"] == 50
    assert [row["scenario_name"] for row in get_scenarios(project_id, db_path)] == ["弱気", "標準", "強気"]
    delete_project(project_id, db_path)
    assert get_project(project_id, db_path) is None
    assert get_pl_entries(project_id, db_path) == []
    assert get_catalyst(project_id, db_path) is None


def test_rate_model_catalyst_is_saved_and_reloaded(tmp_path):
    db_path = tmp_path / "rate_catalyst.db"
    initialize_database(db_path)
    project_id = save_project({"project_name": "候補提案"}, db_path)
    save_catalyst(project_id, {
        "catalyst_name": "新製品", "target_year": "2027.3",
        "calculation_method": "売上比率モデル", "base_sales": 2000,
        "impact_rate": 5, "evidence_category": "自分の仮定",
    }, db_path)
    loaded = get_catalyst(project_id, db_path)
    assert loaded["calculation_method"] == "売上比率モデル"
    assert loaded["base_sales"] == 2000
    assert loaded["impact_rate"] == 5


def test_segment_entries_save_and_reload(tmp_path):
    db_path = tmp_path / "test.db"
    initialize_database(db_path)
    project_id = save_project({"project_name": "セグメント"}, db_path)
    save_segment_entries(project_id, [{"年度": "2025/3", "セグメント": "光学ユニット", "売上高（百万円）": 1200,
                                       "営業利益（百万円）": 180, "出典": "手入力", "メモ": "確認済"}], db_path)
    loaded = get_segment_entries(project_id, db_path)
    assert loaded[0]["segment_name"] == "光学ユニット"
    assert loaded[0]["sales"] == 1200


def test_expanded_pl_and_segment_result_type_are_saved(tmp_path):
    db_path = tmp_path / "expanded.db"
    initialize_database(db_path)
    project_id = save_project({"project_name": "横持ち表"}, db_path)
    save_pl_entries(project_id, [{
        "fiscal_year": "2026.3", "result_type": "自分予想", "sales": 3000,
        "cost_of_sales": 1800, "gross_profit": 1200, "sga_expenses": 800,
        "operating_profit": 400, "ordinary_profit": 420, "pretax_profit": 410,
        "income_taxes": 120, "net_income": 290, "shares_outstanding": 10,
        "source": "自分の仮定", "basis_date": "2025-05-01",
    }], db_path)
    pl = get_pl_entries(project_id, db_path)[0]
    assert pl["cost_of_sales"] == 1800
    assert pl["ordinary_profit"] == 420
    assert pl["result_type"] == "自分予想"
    save_segment_entries(project_id, [{
        "fiscal_year": "2026.3", "result_type": "会社予想", "segment_name": "製造",
        "sales": 2000, "operating_profit": 300, "source": "会社開示", "note": "予想",
    }], db_path)
    segment = get_segment_entries(project_id, db_path)[0]
    assert segment["result_type"] == "会社予想"


def test_same_year_forecasts_and_blank_company_fields_are_saved(tmp_path):
    db_path = tmp_path / "forecasts.db"
    initialize_database(db_path)
    project_id = save_project({"project_name": "予想比較"}, db_path)
    save_pl_entries(project_id, [
        {"fiscal_year": "2026.3", "result_type": "会社予想", "sales": 1000,
         "operating_profit": None, "net_income": None, "shares_outstanding": None},
        {"fiscal_year": "2026.3", "result_type": "自分予想", "sales": 1150,
         "operating_profit": 120, "net_income": 80, "shares_outstanding": 10},
    ], db_path)
    loaded = get_pl_entries(project_id, db_path)
    assert [(row["result_type"], row["sales"]) for row in loaded] == [
        ("会社予想", 1000), ("自分予想", 1150)
    ]
    assert loaded[0]["operating_profit"] is None

    save_segment_entries(project_id, [
        {"fiscal_year": "2026.3", "result_type": "会社予想", "segment_name": "製造", "sales": 600},
        {"fiscal_year": "2026.3", "result_type": "自分予想", "segment_name": "製造", "sales": 700},
    ], db_path)
    assert [row["result_type"] for row in get_segment_entries(project_id, db_path)] == ["会社予想", "自分予想"]


def test_jquants_valuation_fields_are_saved_and_reloaded(tmp_path):
    db_path = tmp_path / "valuation.db"
    initialize_database(db_path)
    project_id = save_project({
        "project_name": "API valuation",
        "bps": 1250.0,
        "annual_dividend": 50.0,
        "ir_url": "https://example.com/ir/",
    }, db_path)
    project = get_project(project_id, db_path)
    assert project["bps"] == 1250.0
    assert project["annual_dividend"] == 50.0
    assert project["ir_url"] == "https://example.com/ir/"


def test_legacy_database_is_backed_up_and_units_are_migrated(tmp_path):
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.executescript("""
        CREATE TABLE projects (id INTEGER PRIMARY KEY, project_name TEXT, shares_outstanding REAL);
        CREATE TABLE pl_entries (id INTEGER PRIMARY KEY, project_id INTEGER, fiscal_year TEXT, result_type TEXT,
                                 sales REAL, operating_profit REAL, net_income REAL, shares_outstanding REAL);
        CREATE TABLE scenarios (id INTEGER PRIMARY KEY, project_id INTEGER, scenario_name TEXT, base_sales REAL,
                                existing_sales_growth_rate REAL, catalyst_sales REAL, operating_margin_rate REAL,
                                effective_tax_rate REAL, shares_outstanding REAL, per REAL);
        CREATE TABLE catalyst_variables (id INTEGER PRIMARY KEY, project_id INTEGER, position INTEGER, variable_name TEXT,
                                         value REAL, unit TEXT, evidence_category TEXT, source TEXT, note TEXT);
        INSERT INTO projects VALUES (1, '旧データ', 10000000);
        INSERT INTO pl_entries VALUES (1, 1, '2025/3', '実績', 1000, 100, 50, 10000000);
        INSERT INTO scenarios VALUES (1, 1, '標準', 1000, 0.1, 10, 0.1, 0.3, 10000000, 15);
    """)
    conn.commit(); conn.close()
    initialize_database(db_path)
    assert get_project(1, db_path)["shares_outstanding"] == 10
    assert get_pl_entries(1, db_path)[0]["shares_outstanding"] == 10
    assert get_scenarios(1, db_path)[0]["operating_margin_rate"] == 10
    assert list(tmp_path.glob("legacy_before_unit_migration_*.db"))
