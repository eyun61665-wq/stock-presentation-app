"""SQLite永続化と、旧単位（株・小数％）からの互換マイグレーション。"""
from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "stock_projects.db"
SCHEMA_VERSION = "11"
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
        "reported_eps": "REAL",
        "source": "TEXT DEFAULT '手入力'", "basis_date": "TEXT DEFAULT ''",
    },
    "segment_entries": {
        "result_type": "TEXT DEFAULT '実績'",
        "display_order": "INTEGER DEFAULT 0",
        "row_type": "TEXT DEFAULT 'セグメント'",
    },
    "catalysts": {
        "calculation_method": "TEXT DEFAULT '数量モデル'",
        "base_sales": "REAL",
        "impact_rate": "REAL",
        "incremental_margin": "REAL",
        "effective_tax_rate": "REAL",
        "valuation_per": "REAL",
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
    backup_dir = db_path.parent / "backup" / timestamp
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup = backup_dir / db_path.name
    shutil.copy2(db_path, backup)
    return backup


def initialize_database(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    path = Path(db_path)
    legacy = False
    needs_api_columns = False
    needs_table_columns = False
    needs_forecast_schema = False
    needs_segment_metrics_table = False
    needs_mvp_table = False
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
            needs_mvp_table = not _table_exists(conn, "project_mvp_data")
        if (legacy or needs_api_columns or needs_table_columns or needs_forecast_schema
                or needs_segment_metrics_table or needs_mvp_table):
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
                calculation_method TEXT DEFAULT '数量モデル', base_sales REAL, impact_rate REAL,
                incremental_margin REAL, effective_tax_rate REAL, valuation_per REAL,
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
            CREATE TABLE IF NOT EXISTS project_mvp_data (
                project_id INTEGER PRIMARY KEY,
                schema_version INTEGER NOT NULL DEFAULT 1,
                overview_json TEXT NOT NULL DEFAULT '{}',
                settings_json TEXT NOT NULL DEFAULT '{}',
                kpi_json TEXT NOT NULL DEFAULT '{}',
                catalyst_json TEXT NOT NULL DEFAULT '{}',
                memo_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
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
    """旧表を削除せず退避し、同一年度に複数区分を保存できる表へ移行する。"""
    suffix = datetime.now().strftime("%Y%m%d%H%M%S")
    old_pl = f"pl_entries_legacy_{suffix}"
    old_segment = f"segment_entries_legacy_{suffix}"

    conn.execute(f"ALTER TABLE pl_entries RENAME TO {old_pl}")
    conn.execute("""
        CREATE TABLE pl_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
            fiscal_year TEXT NOT NULL, result_type TEXT NOT NULL,
            sales REAL, operating_profit REAL, net_income REAL, shares_outstanding REAL,
            cost_of_sales REAL, gross_profit REAL, sga_expenses REAL,
            ordinary_profit REAL, non_operating_income REAL, non_operating_expenses REAL,
            extraordinary_income REAL, extraordinary_loss REAL, pretax_profit REAL,
            income_taxes REAL, reported_eps REAL, source TEXT DEFAULT '手入力', basis_date TEXT DEFAULT '',
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, fiscal_year, result_type)
        )
    """)
    old_pl_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({old_pl})")}
    reported_eps = "reported_eps" if "reported_eps" in old_pl_columns else "NULL"
    conn.execute(f"""
        INSERT INTO pl_entries (
            id, project_id, fiscal_year, result_type, sales, operating_profit, net_income,
            shares_outstanding, cost_of_sales, gross_profit, sga_expenses, ordinary_profit,
            non_operating_income, non_operating_expenses, extraordinary_income,
            extraordinary_loss, pretax_profit, income_taxes, reported_eps, source, basis_date
        )
        SELECT id, project_id, fiscal_year, COALESCE(NULLIF(result_type, ''), '実績'),
            sales, operating_profit, net_income, shares_outstanding, cost_of_sales,
            gross_profit, sga_expenses, ordinary_profit, non_operating_income,
            non_operating_expenses, extraordinary_income, extraordinary_loss,
            pretax_profit, income_taxes, {reported_eps}, source, basis_date
        FROM {old_pl}
    """)

    conn.execute(f"ALTER TABLE segment_entries RENAME TO {old_segment}")
    conn.execute("""
        CREATE TABLE segment_entries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
            fiscal_year TEXT NOT NULL, result_type TEXT DEFAULT '実績', segment_name TEXT NOT NULL,
            sales REAL, operating_profit REAL, source TEXT DEFAULT '手入力', note TEXT DEFAULT '',
            display_order INTEGER DEFAULT 0, row_type TEXT DEFAULT 'セグメント',
            FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
            UNIQUE(project_id, fiscal_year, result_type, segment_name)
        )
    """)
    old_segment_columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({old_segment})")}
    display_order = "display_order" if "display_order" in old_segment_columns else "0"
    row_type = "row_type" if "row_type" in old_segment_columns else "'セグメント'"
    conn.execute(f"""
        INSERT INTO segment_entries (
            id, project_id, fiscal_year, result_type, segment_name, sales,
            operating_profit, source, note, display_order, row_type
        )
        SELECT id, project_id, fiscal_year, COALESCE(NULLIF(result_type, ''), '実績'),
            segment_name, sales, operating_profit, source, note, {display_order}, {row_type}
        FROM {old_segment}
    """)


def _migrate_legacy_units(conn: sqlite3.Connection) -> None:
    """旧アプリの株数（株）・率（小数）を新しい単位へ一度だけ変換する。"""
    def has_column(table: str, column: str) -> bool:
        return _table_exists(conn, table) and column in {
            row["name"] for row in conn.execute(f"PRAGMA table_info({table})")
        }

    if has_column("projects", "shares_outstanding"):
        conn.execute("UPDATE projects SET shares_outstanding = shares_outstanding / 1000000.0 WHERE shares_outstanding <> 0")
    if has_column("pl_entries", "shares_outstanding"):
        conn.execute("UPDATE pl_entries SET shares_outstanding = shares_outstanding / 1000000.0 WHERE shares_outstanding <> 0")
    if has_column("scenarios", "shares_outstanding"):
        conn.execute("UPDATE scenarios SET shares_outstanding = shares_outstanding / 1000000.0 WHERE shares_outstanding <> 0")
    if all(has_column("scenarios", column) for column in (
        "existing_sales_growth_rate", "operating_margin_rate", "effective_tax_rate"
    )):
        conn.execute("UPDATE scenarios SET existing_sales_growth_rate = existing_sales_growth_rate * 100, operating_margin_rate = operating_margin_rate * 100, effective_tax_rate = effective_tax_rate * 100")
    if has_column("catalyst_variables", "position") and has_column("catalyst_variables", "value"):
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


def rename_project(project_id: int, project_name: str, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    name = str(project_name or "").strip()
    if not name:
        raise ValueError("プロジェクト名を入力してください。")
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE projects SET project_name=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (name, project_id),
        )


MVP_DATA_DEFAULTS = {
    "overview": {},
    "settings": {"pl_unit": "百万円", "segment_unit": "百万円", "period_labels": {}},
    "kpi": {"kpis": [], "revenue_items": [], "profit_method": {}},
    "catalyst": {},
    "memo": {},
}


def get_project_mvp_data(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM project_mvp_data WHERE project_id=?", (project_id,)).fetchone()
    result = {key: dict(value) for key, value in MVP_DATA_DEFAULTS.items()}
    result["schema_version"] = 1
    if not row:
        return result
    for key in MVP_DATA_DEFAULTS:
        try:
            loaded = json.loads(row[f"{key}_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            loaded = {}
        result[key] = loaded if isinstance(loaded, dict) else dict(MVP_DATA_DEFAULTS[key])
    result["schema_version"] = int(row["schema_version"] or 1)
    result["updated_at"] = row["updated_at"]
    return result


def save_project_mvp_data(
    project_id: int,
    *,
    overview: dict | None = None,
    settings: dict | None = None,
    kpi: dict | None = None,
    catalyst: dict | None = None,
    memo: dict | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """MVP拡張データを1トランザクションで保存する。"""
    current = get_project_mvp_data(project_id, db_path)
    values = {
        "overview": current["overview"] if overview is None else overview,
        "settings": current["settings"] if settings is None else settings,
        "kpi": current["kpi"] if kpi is None else kpi,
        "catalyst": current["catalyst"] if catalyst is None else catalyst,
        "memo": current["memo"] if memo is None else memo,
    }
    encoded = [json.dumps(values[key], ensure_ascii=False) for key in MVP_DATA_DEFAULTS]
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO project_mvp_data
               (project_id, schema_version, overview_json, settings_json, kpi_json,
                catalyst_json, memo_json, updated_at)
               VALUES (?, 1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(project_id) DO UPDATE SET
                 schema_version=excluded.schema_version,
                 overview_json=excluded.overview_json,
                 settings_json=excluded.settings_json,
                 kpi_json=excluded.kpi_json,
                 catalyst_json=excluded.catalyst_json,
                 memo_json=excluded.memo_json,
                 updated_at=CURRENT_TIMESTAMP""",
            (project_id, *encoded),
        )


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
        "reported_eps", "source", "basis_date",
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
        "reported_eps": "EPS（円）",
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
        "incremental_margin", "effective_tax_rate", "valuation_per",
    ]
    defaults = {
        "calculation_method": "数量モデル", "base_sales": None, "impact_rate": None,
        "incremental_margin": None, "effective_tax_rate": None, "valuation_per": None,
    }
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
                display_order, segment_name
        """, (project_id,))]


def save_segment_entries(project_id: int, entries: list[dict], db_path: str | Path = DEFAULT_DB_PATH) -> None:
    with get_connection(db_path) as conn:
        conn.execute("DELETE FROM segment_entries WHERE project_id=?", (project_id,))
        conn.executemany("""INSERT INTO segment_entries
            (project_id, fiscal_year, result_type, segment_name, sales, operating_profit,
             source, note, display_order, row_type)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", [(project_id,
                                                  row.get("fiscal_year", row.get("年度")),
                                                  row.get("result_type", row.get("実績／会社予想／自分予想", "実績")),
                                                  row.get("segment_name", row.get("セグメント")),
                                                  row.get("sales", row.get("売上高（百万円）")),
                                                  row.get("operating_profit", row.get("営業利益（百万円）")),
                                                  row.get("source", row.get("出典")) or "手入力",
                                                  row.get("note", row.get("メモ")) or "",
                                                  int(row.get("display_order", 0) or 0),
                                                  row.get("row_type", "セグメント") or "セグメント")
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
