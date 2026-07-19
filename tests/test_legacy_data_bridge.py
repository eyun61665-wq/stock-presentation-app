import sqlite3

import pytest

from database import (
    get_pl_entries,
    get_project,
    get_project_mvp_data,
    get_segment_entries,
    initialize_database,
    migrate_legacy_project_data,
    save_pl_entries,
    save_project_mvp_data,
    save_segment_entries,
)
from mvp_ui import _pl_matrix, _segment_matrix


def _create_populated_legacy_db(path) -> None:
    """project_mvp_data導入前の、実データ入りSQLiteを再現する。"""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE projects (
            id INTEGER PRIMARY KEY, project_name TEXT NOT NULL, company_name TEXT DEFAULT '',
            stock_code TEXT DEFAULT '', current_price REAL, shares_outstanding REAL,
            business_description TEXT DEFAULT '', strengths TEXT DEFAULT '',
            investment_thesis TEXT DEFAULT '', catalysts TEXT DEFAULT '', risks TEXT DEFAULT ''
        );
        CREATE TABLE pl_entries (
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, fiscal_year TEXT NOT NULL,
            result_type TEXT NOT NULL, sales REAL, operating_profit REAL, net_income REAL,
            shares_outstanding REAL, UNIQUE(project_id, fiscal_year)
        );
        CREATE TABLE segment_entries (
            id INTEGER PRIMARY KEY, project_id INTEGER NOT NULL, fiscal_year TEXT NOT NULL,
            segment_name TEXT NOT NULL, sales REAL, operating_profit REAL,
            source TEXT, note TEXT, UNIQUE(project_id, fiscal_year, segment_name)
        );
        INSERT INTO projects VALUES
            (9, '日東工業', '日東工業', '6651', NULL, NULL,
             '配電盤などを製造する会社', '販売網', '更新需要を取り込む', '設備投資回復', '資材高'),
            (10, '別会社', '別会社', '9998', NULL, NULL, '', '', '', '', '');
        INSERT INTO pl_entries VALUES
            (1, 9, '2024-03-31', '実績', 160709, 11967, 8715, 37.93),
            (2, 9, '2025-03-31', '実績', 184683, 13432, 12097, 37.934),
            (3, 9, '2026-03-31', '実績', 195783, 15446, 11493, 37.936),
            (4, 10, '2026-03-31', '実績', 1, 1, 1, 1);
        INSERT INTO segment_entries VALUES
            (1, 9, '2026-03-31', '製造', 119877, 11306, '旧PDF', ''),
            (2, 9, '2026-03-31', '流通', 59956, 2628, '旧PDF', ''),
            (3, 9, '2026-03-31', '電子部品', 15949, 1379, '旧PDF', ''),
            (4, 10, '2026-03-31', '他社事業', 1, 1, '旧PDF', '');
    """)
    con.commit()
    con.close()


@pytest.fixture
def legacy_db(tmp_path):
    path = tmp_path / "legacy_populated.db"
    _create_populated_legacy_db(path)
    initialize_database(path)
    return path


def test_legacy_project_code_and_pl_are_available_to_new_screen(legacy_db):
    assert get_project(9, legacy_db)["stock_code"] == "6651"
    matrix = _pl_matrix(get_pl_entries(9, legacy_db))
    assert matrix.loc[matrix["項目"] == "売上高", "2026-03-31 実績"].iloc[0] == "195,783"
    assert matrix.loc[matrix["項目"] == "営業利益", "2026-03-31 実績"].iloc[0] == "15,446"


def test_legacy_segments_are_available_to_new_screen(legacy_db):
    matrix = _segment_matrix(get_segment_entries(9, legacy_db))
    assert set(matrix["項目"]) >= {"1：製造", "2：流通", "3：電子部品"}
    assert matrix.loc[matrix["項目"] == "1：製造", "2026-03-31 実績"].iloc[0] == 119877


def test_new_mvp_values_win_and_empty_new_values_fall_back(legacy_db):
    initial = get_project_mvp_data(9, legacy_db)
    assert initial["overview"]["what_company"] == "配電盤などを製造する会社"
    save_project_mvp_data(9, overview={"what_company": "新画面で確認済み", "competitive_strength": ""}, db_path=legacy_db)
    loaded = get_project_mvp_data(9, legacy_db)
    assert loaded["overview"]["what_company"] == "新画面で確認済み"
    assert loaded["overview"]["competitive_strength"] == "販売網"


def test_none_does_not_overwrite_existing_pl_or_segment(legacy_db):
    save_pl_entries(9, [{
        "fiscal_year": "2026-03-31", "result_type": "実績",
        "sales": None, "operating_profit": 16000,
    }], legacy_db)
    latest = get_pl_entries(9, legacy_db)[-1]
    assert latest["sales"] == 195783
    assert latest["operating_profit"] == 16000

    save_segment_entries(9, [{
        "fiscal_year": "2026-03-31", "result_type": "実績", "segment_name": "製造",
        "sales": None, "operating_profit": 12000,
    }], legacy_db)
    manufacturing = next(row for row in get_segment_entries(9, legacy_db) if row["segment_name"] == "製造")
    assert manufacturing["sales"] == 119877
    assert manufacturing["operating_profit"] == 12000


def test_migration_is_idempotent_and_projects_do_not_mix(legacy_db):
    before = (len(get_pl_entries(9, legacy_db)), len(get_segment_entries(9, legacy_db)))
    first = migrate_legacy_project_data(legacy_db)
    second = migrate_legacy_project_data(legacy_db)
    after = (len(get_pl_entries(9, legacy_db)), len(get_segment_entries(9, legacy_db)))
    assert before == after == (3, 3)
    assert first["fields_filled"] == 0
    assert second["fields_filled"] == 0
    assert get_pl_entries(10, legacy_db)[0]["sales"] == 1
    assert all(row["sales"] != 1 for row in get_pl_entries(9, legacy_db))


def test_company_and_own_forecasts_are_saved_separately(legacy_db):
    save_pl_entries(9, [
        {"fiscal_year": "2027-03-31", "result_type": "会社予想", "sales": 210000},
        {"fiscal_year": "2027-03-31", "result_type": "独自予想", "sales": 220000},
    ], legacy_db)
    forecasts = [row for row in get_pl_entries(9, legacy_db) if row["fiscal_year"] == "2027-03-31"]
    assert [(row["result_type"], row["sales"]) for row in forecasts] == [
        ("会社予想", 210000), ("自分予想", 220000),
    ]


def test_edited_rows_survive_reload(legacy_db):
    pl = next(row for row in get_pl_entries(9, legacy_db) if row["fiscal_year"] == "2026-03-31")
    save_pl_entries(9, [{**pl, "sales": 196000}], legacy_db)
    assert next(row for row in get_pl_entries(9, legacy_db) if row["fiscal_year"] == "2026-03-31")["sales"] == 196000

    segment = next(row for row in get_segment_entries(9, legacy_db) if row["segment_name"] == "流通")
    save_segment_entries(9, [{**segment, "sales": 60000}], legacy_db)
    assert next(row for row in get_segment_entries(9, legacy_db) if row["segment_name"] == "流通")["sales"] == 60000
