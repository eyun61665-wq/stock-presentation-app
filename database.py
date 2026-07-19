"""SQLite永続化と、旧単位（株・小数％）からの互換マイグレーション。"""
from __future__ import annotations

import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "stock_projects.db"
SCHEMA_VERSION = "9"
API_PROJECT_COLUMNS = {
    "company_name_en": "TEXT DEFAULT ''", "market": "TEXT DEFAULT ''", "sector17": "TEXT DEFAULT ''",
    "sector33": "TEXT DEFAULT ''", "price_date": "TEXT DEFAULT ''", "data_retrieved_at": "TEXT DEFAULT ''",
    "shares_source": "TEXT DEFAULT ''", "price_source": "TEXT DEFAULT '手入力'", "price_input_method": "TEXT DEFAULT '手入力'", "edinet_code": "TEXT DEFAULT ''",
    "bps": "REAL", "annual_dividend": "REAL", "ir_url": "TEXT DEFAULT ''",
}

TABLE_MIGRATION_COLUMNS = {
    "pl_entries": {
        "cost_of_sales": "REAL", "gross_profit": "REAL", "sga_expenses": "REAL",
        "ordinary_profit": "REAL", "non_operating_income": "REAL",
        "non_operating_expenses": "REAL", "extraordinary_income": "REAL",
        "extraordinary_loss": "REAL", "pretax_profit": "REAL", "income_taxes": "REAL",
        "source": "TEXT DEFAULT '手入力'", "basis_date": "TEXT DEFAULT ''",
    },
    "segment_entries": {"result_type": "TEXT DEFAULT '実績'"},
    "catalysts": {
        "calculation_method": "TEXT DEFAULT '数量モデル'",
        "base_sales": "REAL",
        "impact_rate": "REAL",
    },
}


def get_connection(db_path: str | Path = DEFAULT_DB_PATH) -> sqlite3.Connection:
    # 複数の端末から同時に開いた場合も、短いDBロックで失敗しないよう待機する。
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _backup_database(db_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = db_path.with_name(f"{db_path.stem}_before_unit_migration_{timestamp}{db_path.suffix}")
    shutil.copy2(db_path, backup)
    return backup


def initialize_database(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    path = Path(db_path)
    legacy = False
    needs_api_columns = False
    needs_table_columns = False
    needs_forecast_schema = False
    needs_segment_metrics_table = False
    if path.exists():
        with get_connection(path) as conn:
            legacy = _table_exists(conn, "projects") and not _table_exists(conn, "app_metadata")
            if _table_exists(conn, "pl_entries"):
                table_sql = str(conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name='pl_entries'"
                ).fetchone()["sql"] or "").replace(" ", "").lower()
                needs_forecast_schema = (
                    "unique(project_id,fiscal_year,result_type)" not in table_sql
                    or "salesrealnotnull" in table_sql
                )
            if _table_exists(conn, "projects"):
                existing = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
                needs_api_columns = bool(set(API_PROJECT_COLUMNS) - existing)
            for table, columns in TABLE_MIGRATION_COLUMNS.items():
                if _table_exists(conn, table):
                    existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
                    needs_table_columns = needs_table_columns or bool(set(columns) - existing)
            needs_segment_metrics_table = not _table_exists(conn, "segment_metrics")
        if legacy or needs_api_columns or needs_table_columns or needs_forecast_schema or needs_segment_metrics_table:
            _backup_database(path)

    with get_connection(path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS app_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS projects (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_name TEXT NOT NULL,
                company_name TEXT DEFAULT '', stock_code TEXT DEFAULT '',
                current_price REAL DEFAULT 0, shares_outstanding REAL DEFAULT 0,
                business_description TEXT DEFAULT '', strengths TEXT DEFAULT '',
                investment_thesis TEXT DEFAULT '', catalysts TEXT DEFAULT '', risks TEXT DEFAULT '',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS pl_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                fiscal_year TEXT NOT NULL, result_type TEXT NOT NULL,
                sales REAL, operating_profit REAL, net_income REAL,
                shares_outstanding REAL,
                cost_of_sales REAL, gross_profit REAL, sga_expenses REAL,
                ordinary_profit REAL, non_operating_income REAL, non_operating_expenses REAL,
                extraordinary_income REAL, extraordinary_loss REAL, pretax_profit REAL,
                income_taxes REAL, source TEXT DEFAULT '手入力', basis_date TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, fiscal_year, result_type)
            );
            CREATE TABLE IF NOT EXISTS catalyst_variables (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                position INTEGER NOT NULL, variable_name TEXT NOT NULL, value REAL NOT NULL DEFAULT 0,
                unit TEXT DEFAULT '', evidence_category TEXT DEFAULT '自分の仮定', source TEXT DEFAULT '', note TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE, UNIQUE(project_id, position)
            );
            CREATE TABLE IF NOT EXISTS catalysts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL UNIQUE,
                catalyst_name TEXT DEFAULT '', target_year TEXT DEFAULT '', description TEXT DEFAULT '',
                target_count REAL NOT NULL DEFAULT 0, target_rate REAL NOT NULL DEFAULT 0,
                capture_rate REAL NOT NULL DEFAULT 0, price_per_case REAL NOT NULL DEFAULT 0,
                evidence_category TEXT DEFAULT '自分の仮定', source TEXT DEFAULT '', note TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS scenarios (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                scenario_name TEXT NOT NULL, base_sales REAL NOT NULL DEFAULT 0,
                existing_sales_growth_rate REAL NOT NULL DEFAULT 0, catalyst_sales REAL NOT NULL DEFAULT 0,
                operating_margin_rate REAL NOT NULL DEFAULT 0, effective_tax_rate REAL NOT NULL DEFAULT 0,
                shares_outstanding REAL NOT NULL DEFAULT 0, per REAL NOT NULL DEFAULT 0,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, scenario_name)
            );
            CREATE TABLE IF NOT EXISTS segment_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                fiscal_year TEXT NOT NULL, result_type TEXT DEFAULT '実績', segment_name TEXT NOT NULL,
                sales REAL, operating_profit REAL, source TEXT DEFAULT '手入力', note TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, fiscal_year, result_type, segment_name)
            );
            CREATE TABLE IF NOT EXISTS segment_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                fiscal_year TEXT NOT NULL, result_type TEXT DEFAULT '実績',
                row_label TEXT NOT NULL, value REAL, unit TEXT DEFAULT '',
                display_order INTEGER DEFAULT 0, source TEXT DEFAULT '手入力', note TEXT DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, fiscal_year, result_type, row_label)
            );
        """)
        if legacy:
            _migrate_legacy_units(conn)
        existing = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
        for column, definition in API_PROJECT_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE projects ADD COLUMN {column} {definition}")
        for table, columns in TABLE_MIGRATION_COLUMNS.items():
            existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
            for column, definition in columns.items():
                if column not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        if needs_forecast_schema:
            _migrate_forecast_tables(conn)
        conn.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))


def _migrate_forecast_tables(conn: sqlite3.Connection) -> None:
    """同一年度の会社予想・自分予想と、予想の空欄保存に対応する。"""
    conn.executescript("""
        CREATE TABLE pl_entries_v7 (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
            fiscal_year TEXT NOT NULL, result_type TEXT NOT NULL,
            sales REAL, operating_profit REAL, net_income REAL, shares_outstanding REAL,
            cost_of_sales REAL, gross_profit REAL, sga_expenses REAL,
            ordinary_profit REAL, non_operating_income REAL, non_operating_expenses REAL,
            extraordinary_income REAL, extraordinary_loss REAL, pretax_profit REAL,
            income_taxes REAL, source TEXT DEFAULT '手入力', basis_date TEXT DEFAULT '',
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, fiscal_year, result_type)
        );
        INSERT INTO pl_entries_v7 (
            id, project_id, fiscal_year, result_type, sales, operating_profit, net_income,
            shares_outstanding, cost_of_sales, gross_profit, sga_expenses, ordinary_profit,
            non_operating_income, non_operating_expenses, extraordinary_income,
            extraordinary_loss, pretax_profit, income_taxes, source, basis_date
        )
        SELECT id, project_id, fiscal_year, COALESCE(NULLIF(result_type, ''), '実績'),
            sales, operating_profit, net_income, shares_outstanding, cost_of_sales,
            gross_profit, sga_expenses, ordinary_profit, non_operating_income,
            non_operating_expenses, extraordinary_income, extraordinary_loss,
            pretax_profit, income_taxes, source, basis_date
        FROM pl_entries;
        DROP TABLE pl_entries;
        ALTER TABLE pl_entries_v7 RENAME TO pl_entries;

        CREATE TABLE segment_entries_v7 (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
            fiscal_year TEXT NOT NULL, result_type TEXT DEFAULT '実績', segment_name TEXT NOT NULL,
            sales REAL, operating_profit REAL, source TEXT DEFAULT '手入力', note TEXT DEFAULT '',
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, fiscal_year, result_type, segment_name)
        );
        INSERT INTO segment_entries_v7 (
            id, project_id, fiscal_year, result_type, segment_name, sales,
            operating_profit, source, note
        )
        SELECT id, project_id, fiscal_year, COALESCE(NULLIF(result_type, ''), '実績'),
            segment_name, sales, operating_profit, source, note
        FROM segment_entries;
        DROP TABLE segment_entries;
        ALTER TABLE segment_entries_v7 RENAME TO segment_entries;
    """)


def _migrate_legacy_units(conn: sqlite3.Connection) -> None:
    """旧アプリの株数（株）・率（小数）を新しい単位へ一度だけ変換する。"""
    conn.execute("UPDATE projects SET shares_outstanding = shares_outstanding / 1000000.0 WHERE shares_outstanding <> 0")
    conn.execute("UPDATE pl_entries SET shares_outstanding = shares_outstanding / 1000000.0 WHERE shares_outstanding <> 0")
    conn.execute("UPDATE scenarios SET shares_outstanding = shares_outstanding / 1000000.0 WHERE shares_outstanding <> 0")
    conn.execute("UPDATE scenarios SET existing_sales_growth_rate = existing_sales_growth_rate * 100, operating_margin_rate = operating_margin_rate * 100, effective_tax_rate = effective_tax_rate * 100")
    if _table_exists(conn, "catalyst_variables"):
        rows = conn.execute("SELECT project_id, position, value, evidence_category, source, note FROM catalyst_variables ORDER BY project_id, position").fetchall()
        by_project: dict[int, dict[int, sqlite3.Row]] = {}
        for row in rows:
            by_project.setdefault(row["project_id"], {})[row["position"]] = row
        for project_id, values in by_project.items():
            if not all(position in values for position in range(4)):
                continue
            first = values[0]
            conn.execute("""INSERT OR REPLACE INTO catalysts
                (project_id, target_count, target_rate, capture_rate, price_per_case, evidence_category, source, note)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (project_id, values[0]["value"], values[1]["value"] * 100, values[2]["value"] * 100,
                 values[3]["value"], first["evidence_category"], first["source"], first["note"]))


def list_projects(db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM projects ORDER BY updated_at DESC, id DESC")]


def get_project(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
    return dict(row) if row else None


def save_project(data: dict, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    fields = ["project_name", "company_name", "stock_code", "current_price", "shares_outstanding", "business_description", "strengths", "investment_thesis", "catalysts", "risks", "company_name_en", "market", "sector17", "sector33", "price_date", "data_retrieved_at", "shares_source", "price_source", "price_input_method", "edinet_code", "bps", "annual_dividend", "ir_url"]
    with get_connection(db_path) as conn:
        if data.get("id"):
            current = conn.execute("SELECT * FROM projects WHERE id=?", (data["id"],)).fetchone()
            values = [data[field] if field in data else current[field] for field in fields]
            conn.execute(f"UPDATE projects SET {', '.join(f'{f}=?' for f in fields)}, updated_at=CURRENT_TIMESTAMP WHERE id=?", values + [data["id"]])
            return int(data["id"])
        nullable_numeric = {"bps", "annual_dividend"}
        values = [data.get(field, None if field in nullable_numeric else "") for field in fields]
        return int(conn.execute(f"INSERT INTO projects ({', '.join(fields)}) VALUES ({', '.join('?' for _ in fields)})", values).lastrowid)


def delete_project(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))


def get_pl_entries(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute("""
            SELECT * FROM pl_entries WHERE project_id=?
            ORDER BY fiscal_year,
                CASE result_type WHEN '実績' THEN 0 WHEN '会社予想' THEN 1 ELSE 2 END
        """, (project_id,))]


def save_pl_entries(project_id: int, entries: list[dict], db_path: str | Path = DEFAULT_DB_PATH) -> None:
    fields = [
        "fiscal_year", "result_type", "sales", "operating_profit", "net_income",
        "shares_outstanding", "cost_of_sales", "gross_profit", "sga_expenses",
        "ordinary_profit", "non_operating_income", "non_operating_expenses",
        "extraordinary_income", "extraordinary_loss", "pretax_profit", "income_taxes",
        "source", "basis_date",
    ]
    labels = {
        "fiscal_year": "年度", "result_type": "実績／会社予想／自分予想",
        "sales": "売上高（百万円）", "operating_profit": "営業利益（百万円）",
        "net_income": "純利益（百万円）", "shares_outstanding": "平均株式数（百万株）",
        "cost_of_sales": "売上原価（百万円）", "gross_profit": "売上総利益（百万円）",
        "sga_expenses": "販管費（百万円）", "ordinary_profit": "経常利益（百万円）",
        "non_operating_income": "営業外収益（百万円）", "non_operating_expenses": "営業外費用（百万円）",
        "extraordinary_income": "特別利益（百万円）", "extraordinary_loss": "特別損失（百万円）",
        "pretax_profit": "税引前利益（百万円）", "income_taxes": "法人税等（百万円）",
        "source": "出典", "basis_date": "基準日",
    }

    def value(row: dict, field: str):
        default = "手入力" if field == "source" else ("" if field == "basis_date" else None)
        return row.get(field, row.get(labels[field], default))

    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM pl_entries WHERE project_id=?", (project_id,))
        conn.executemany(
            f"INSERT INTO pl_entries (project_id, {', '.join(fields)}) VALUES (?, {', '.join('?' for _ in fields)})",
            [(project_id, *[value(row, field) for field in fields]) for row in entries],
        )


def get_catalyst(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM catalysts WHERE project_id=?", (project_id,)).fetchone()
    return dict(row) if row else None


def save_catalyst(project_id: int, catalyst: dict, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    fields = [
        "catalyst_name", "target_year", "description", "target_count", "target_rate",
        "capture_rate", "price_per_case", "evidence_category", "source", "note",
        "calculation_method", "base_sales", "impact_rate",
    ]
    defaults = {"calculation_method": "数量モデル", "base_sales": None, "impact_rate": None}
    values = [catalyst.get(field, defaults.get(field, "")) for field in fields]
    with get_connection(db_path) as conn:
        conn.execute(f"""INSERT INTO catalysts (project_id, {', '.join(fields)}) VALUES (?, {', '.join('?' for _ in fields)})
            ON CONFLICT(project_id) DO UPDATE SET {', '.join(f'{field}=excluded.{field}' for field in fields)}""", [project_id, *values])


def get_scenarios(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute("SELECT * FROM scenarios WHERE project_id=? ORDER BY id", (project_id,))]


def save_scenarios(project_id: int, scenarios: list[dict], db_path: str | Path = DEFAULT_DB_PATH) -> None:
    fields = ["scenario_name", "base_sales", "existing_sales_growth_rate", "catalyst_sales", "operating_margin_rate", "effective_tax_rate", "shares_outstanding", "per"]
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM scenarios WHERE project_id=?", (project_id,))
        conn.executemany(f"INSERT INTO scenarios (project_id, {', '.join(fields)}) VALUES (?, {', '.join('?' for _ in fields)})", [(project_id, *[row[field] for field in fields]) for row in scenarios])


def get_segment_entries(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute("""
            SELECT * FROM segment_entries WHERE project_id=?
            ORDER BY fiscal_year,
                CASE result_type WHEN '実績' THEN 0 WHEN '会社予想' THEN 1 ELSE 2 END,
                segment_name
        """, (project_id,))]


def save_segment_entries(project_id: int, entries: list[dict], db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM segment_entries WHERE project_id=?", (project_id,))
        conn.executemany("""INSERT INTO segment_entries
            (project_id, fiscal_year, result_type, segment_name, sales, operating_profit, source, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""", [(project_id,
                                                  row.get("fiscal_year", row.get("年度")),
                                                  row.get("result_type", row.get("実績／会社予想／自分予想", "実績")),
                                                  row.get("segment_name", row.get("セグメント")),
                                                  row.get("sales", row.get("売上高（百万円）")),
                                                  row.get("operating_profit", row.get("営業利益（百万円）")),
                                                  row.get("source", row.get("出典")) or "手入力",
                                                  row.get("note", row.get("メモ")) or "")
                                                for row in entries])


def get_segment_metrics(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute("""
            SELECT * FROM segment_metrics WHERE project_id=?
            ORDER BY display_order, row_label, fiscal_year,
                CASE result_type WHEN '実績' THEN 0 WHEN '会社予想' THEN 1 ELSE 2 END
        """, (project_id,))]


def save_segment_metrics(project_id: int, entries: list[dict], db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """契約社数や平均単価など、任意のセグメントKPIを年度別に保存する。"""
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM segment_metrics WHERE project_id=?", (project_id,))
        conn.executemany("""INSERT INTO segment_metrics
            (project_id, fiscal_year, result_type, row_label, value, unit,
             display_order, source, note)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""", [(
                project_id,
                row.get("fiscal_year", row.get("年度")),
                row.get("result_type", row.get("区分", "実績")),
                row.get("row_label", row.get("項目")),
                row.get("value", row.get("値")),
                row.get("unit", row.get("単位", "")),
                int(row.get("display_order", row.get("表示順", 0)) or 0),
                row.get("source", row.get("出典")) or "手入力",
                row.get("note", row.get("メモ")) or "",
            ) for row in entries])
