"""SQLite永続化と、旧単位（株・小数％）からの互換マイグレーション。"""
from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DB_PATH = BASE_DIR / "stock_projects.db"
SCHEMA_VERSION = "13"
FINANCIAL_DATA_TABLES = {
    "company_master_records",
    "source_documents",
    "financial_facts",
    "segment_facts",
    "mapping_rules",
}
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


def _is_blank(value: object) -> bool:
    """Noneや空文字など、既存値を置き換えてはいけない空値を判定する。"""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, dict, set)):
        return not value
    return False


def _fill_blanks(primary: dict, fallback: dict) -> tuple[dict, int]:
    """primaryの空欄だけをfallbackで補い、補完した項目数も返す。"""
    merged = dict(primary)
    changed = 0
    for key, value in fallback.items():
        if _is_blank(merged.get(key)) and not _is_blank(value):
            merged[key] = value
            changed += 1
    return merged, changed


def normalize_result_type(value: object) -> str:
    """旧画面を含む区分表記を、実績・会社予想・自分予想へ統一する。"""
    text = str(value or "").strip()
    aliases = {
        "actual": "実績", "Actual": "実績", "本決算": "実績",
        "forecast": "会社予想", "company_forecast": "会社予想", "会社予測": "会社予想",
        "自社予想": "自分予想", "独自予想": "自分予想", "own_forecast": "自分予想",
    }
    return aliases.get(text, text or "実績")


def _legacy_mvp_fallback(project: dict) -> dict[str, dict]:
    """実在する旧projectsカラムから、新画面の項目へ明示的に対応付ける。"""
    business = project.get("business_description")
    strengths = project.get("strengths")
    thesis = project.get("investment_thesis")
    catalysts = project.get("catalysts")
    risks = project.get("risks")
    ir_url = project.get("ir_url")
    return {
        "overview": {
            "what_company": business,
            "competitive_strength": strengths,
            "attention_points": risks,
        },
        "catalyst": {
            "main": catalysts,
            "reference_url": ir_url,
        },
        "memo": {
            "company_overview": business,
            "strengths": strengths,
            "investment_thesis": thesis,
            "catalyst": catalysts,
            "risks": risks,
            "reference_urls": ir_url,
        },
    }


def initialize_database(db_path: str | Path = DEFAULT_DB_PATH) -> None:
    path = Path(db_path)
    legacy = False
    needs_api_columns = False
    needs_table_columns = False
    needs_forecast_schema = False
    needs_segment_metrics_table = False
    needs_mvp_table = False
    needs_financial_data_tables = False
    schema_outdated = False
    if path.exists():
        with get_connection(path) as conn:
            legacy = _table_exists(conn, "projects") and not _table_exists(conn, "app_metadata")
            if _table_exists(conn, "app_metadata"):
                version_row = conn.execute(
                    "SELECT value FROM app_metadata WHERE key='schema_version'"
                ).fetchone()
                schema_outdated = version_row is None or str(version_row["value"]) != SCHEMA_VERSION
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
            needs_financial_data_tables = any(
                not _table_exists(conn, table) for table in FINANCIAL_DATA_TABLES
            )
        if (legacy or schema_outdated or needs_api_columns or needs_table_columns or needs_forecast_schema
                or needs_segment_metrics_table or needs_mvp_table or needs_financial_data_tables):
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
            CREATE TABLE IF NOT EXISTS company_master_records (
                project_id INTEGER PRIMARY KEY, stock_code TEXT NOT NULL DEFAULT '',
                company_name TEXT NOT NULL DEFAULT '', market TEXT DEFAULT '', industry TEXT DEFAULT '',
                accounting_standard TEXT DEFAULT '', provider TEXT DEFAULT '手入力',
                raw_json TEXT NOT NULL DEFAULT '{}', confirmed_at TEXT DEFAULT '',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            );
            CREATE TABLE IF NOT EXISTS source_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                provider TEXT NOT NULL, document_id TEXT NOT NULL, document_type TEXT NOT NULL,
                title TEXT DEFAULT '', source_url TEXT DEFAULT '', filing_date TEXT DEFAULT '',
                fiscal_year TEXT DEFAULT '', retrieved_at TEXT DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                UNIQUE(project_id, provider, document_id)
            );
            CREATE TABLE IF NOT EXISTS financial_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                source_key TEXT NOT NULL, document_row_id INTEGER, common_key TEXT,
                raw_label TEXT NOT NULL DEFAULT '', xbrl_tag TEXT DEFAULT '', value REAL,
                unit TEXT DEFAULT '', fiscal_year TEXT DEFAULT '', accounting_standard TEXT DEFAULT '',
                source TEXT DEFAULT '', manual_value REAL, mapping_scope TEXT DEFAULT 'unresolved',
                status TEXT DEFAULT 'candidate', confirmed_at TEXT DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}', updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(document_row_id) REFERENCES source_documents(id) ON DELETE SET NULL,
                UNIQUE(project_id, source_key)
            );
            CREATE TABLE IF NOT EXISTS segment_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT, project_id INTEGER NOT NULL,
                source_key TEXT NOT NULL, document_row_id INTEGER, segment_name TEXT,
                raw_label TEXT NOT NULL DEFAULT '', xbrl_tag TEXT DEFAULT '', metric TEXT DEFAULT 'revenue',
                value REAL, unit TEXT DEFAULT '', fiscal_year TEXT DEFAULT '', source TEXT DEFAULT '',
                manual_value REAL, display_order INTEGER DEFAULT 0, status TEXT DEFAULT 'candidate',
                confirmed_at TEXT DEFAULT '', metadata_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
                FOREIGN KEY(document_row_id) REFERENCES source_documents(id) ON DELETE SET NULL,
                UNIQUE(project_id, source_key)
            );
            CREATE TABLE IF NOT EXISTS mapping_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT, scope_key TEXT NOT NULL UNIQUE,
                scope_type TEXT NOT NULL, project_id INTEGER,
                accounting_standard TEXT DEFAULT '', raw_identifier TEXT NOT NULL,
                common_key TEXT NOT NULL, note TEXT DEFAULT '', confirmed_at TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
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
        company_master_migrated = _migrate_existing_company_master(conn)
        if company_master_migrated:
            conn.execute(
                "INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('company_master_migration_count', ?)",
                (str(company_master_migrated),),
            )
        migration_report = _migrate_legacy_project_data_in_connection(conn)
        if migration_report["projects_created"] or migration_report["fields_filled"]:
            conn.execute(
                "INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('legacy_mvp_bridge_report', ?)",
                (json.dumps(migration_report, ensure_ascii=False),),
            )
        conn.execute("INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('schema_version', ?)", (SCHEMA_VERSION,))


def _decode_json_object(value: object) -> dict:
    try:
        loaded = json.loads(str(value or "{}"))
    except (json.JSONDecodeError, TypeError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _migrate_existing_company_master(conn: sqlite3.Connection) -> int:
    """既存projectsを汎用企業マスターへ一度だけ補完する。既存レコードは上書きしない。"""
    if not _table_exists(conn, "projects") or not _table_exists(conn, "company_master_records"):
        return 0
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}

    def text_column(name: str, fallback: str = "''") -> str:
        return f"COALESCE({name}, '')" if name in columns else fallback

    company_name = text_column("company_name", text_column("project_name"))
    before = conn.execute("SELECT COUNT(*) FROM company_master_records").fetchone()[0]
    conn.execute(
        f"""INSERT OR IGNORE INTO company_master_records
           (project_id, stock_code, company_name, market, industry, accounting_standard,
            provider, raw_json, confirmed_at, updated_at)
           SELECT id, {text_column('stock_code')}, {company_name}, {text_column('market')},
                  {text_column('sector33')}, '', '既存projects', '{{}}', '', CURRENT_TIMESTAMP
           FROM projects"""
    )
    after = conn.execute("SELECT COUNT(*) FROM company_master_records").fetchone()[0]
    return int(after - before)


def _migrate_legacy_project_data_in_connection(conn: sqlite3.Connection) -> dict[str, object]:
    """旧projectsの説明項目を新JSONの空欄だけへ移す。PL・セグメントは既存表を継続利用する。"""
    report: dict[str, object] = {
        "migration": "legacy_project_fields_to_mvp_v1",
        "executed_at": datetime.now().isoformat(timespec="seconds"),
        "projects_examined": 0,
        "projects_created": 0,
        "projects_updated": 0,
        "fields_filled": 0,
        "pl_rows_reused": 0,
        "segment_rows_reused": 0,
    }
    if not _table_exists(conn, "projects") or not _table_exists(conn, "project_mvp_data"):
        return report
    project_columns = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    selectable = [name for name in (
        "id", "business_description", "strengths", "investment_thesis", "catalysts", "risks", "ir_url"
    ) if name in project_columns]
    if "id" not in selectable:
        return report
    for row in conn.execute(f"SELECT {', '.join(selectable)} FROM projects ORDER BY id"):
        project = dict(row)
        report["projects_examined"] = int(report["projects_examined"]) + 1
        fallback = _legacy_mvp_fallback(project)
        existing = conn.execute(
            "SELECT * FROM project_mvp_data WHERE project_id=?", (project["id"],)
        ).fetchone()
        current = {
            "overview": _decode_json_object(existing["overview_json"]) if existing else {},
            "settings": _decode_json_object(existing["settings_json"]) if existing else {},
            "kpi": _decode_json_object(existing["kpi_json"]) if existing else {},
            "catalyst": _decode_json_object(existing["catalyst_json"]) if existing else {},
            "memo": _decode_json_object(existing["memo_json"]) if existing else {},
        }
        changed = 0
        for section in ("overview", "catalyst", "memo"):
            current[section], filled = _fill_blanks(current[section], fallback[section])
            changed += filled
        if not changed:
            continue
        encoded = [json.dumps(current[key], ensure_ascii=False) for key in MVP_DATA_DEFAULTS]
        conn.execute(
            """INSERT INTO project_mvp_data
               (project_id, schema_version, overview_json, settings_json, kpi_json,
                catalyst_json, memo_json, updated_at)
               VALUES (?, 1, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(project_id) DO UPDATE SET
                 overview_json=excluded.overview_json,
                 catalyst_json=excluded.catalyst_json,
                 memo_json=excluded.memo_json,
                 updated_at=CURRENT_TIMESTAMP""",
            (project["id"], *encoded),
        )
        if existing:
            report["projects_updated"] = int(report["projects_updated"]) + 1
        else:
            report["projects_created"] = int(report["projects_created"]) + 1
        report["fields_filled"] = int(report["fields_filled"]) + changed
    if _table_exists(conn, "pl_entries"):
        report["pl_rows_reused"] = int(conn.execute("SELECT COUNT(*) FROM pl_entries").fetchone()[0])
    if _table_exists(conn, "segment_entries"):
        report["segment_rows_reused"] = int(conn.execute("SELECT COUNT(*) FROM segment_entries").fetchone()[0])
    return report


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


def migrate_legacy_project_data(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, object]:
    """旧会社情報を新JSONの空欄へ安全に補完する。何度実行しても重複しない。"""
    path = Path(db_path)
    if path.exists():
        _backup_database(path)
    with get_connection(path) as conn:
        report = _migrate_legacy_project_data_in_connection(conn)
        if report["projects_created"] or report["fields_filled"]:
            conn.execute(
                "INSERT OR REPLACE INTO app_metadata (key, value) VALUES ('legacy_mvp_bridge_report', ?)",
                (json.dumps(report, ensure_ascii=False),),
            )
    return report


def get_migration_report(db_path: str | Path = DEFAULT_DB_PATH) -> dict[str, object]:
    """最後に実データを補完した移行結果を返す。"""
    with get_connection(db_path) as conn:
        if not _table_exists(conn, "app_metadata"):
            return {}
        row = conn.execute(
            "SELECT value FROM app_metadata WHERE key='legacy_mvp_bridge_report'"
        ).fetchone()
    return _decode_json_object(row["value"]) if row else {}


def get_project_mvp_data(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> dict:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM project_mvp_data WHERE project_id=?", (project_id,)).fetchone()
    result = {key: dict(value) for key, value in MVP_DATA_DEFAULTS.items()}
    result["schema_version"] = 1
    if row:
        for key in MVP_DATA_DEFAULTS:
            loaded = _decode_json_object(row[f"{key}_json"])
            result[key] = {**dict(MVP_DATA_DEFAULTS[key]), **loaded}
        result["schema_version"] = int(row["schema_version"] or 1)
        result["updated_at"] = row["updated_at"]
    project = get_project(project_id, db_path)
    if project:
        fallback = _legacy_mvp_fallback(project)
        for section in ("overview", "catalyst", "memo"):
            result[section], _ = _fill_blanks(result[section], fallback[section])
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
        rows = [dict(row) for row in conn.execute("""
            SELECT * FROM pl_entries WHERE project_id=?
            ORDER BY fiscal_year,
                CASE result_type WHEN '実績' THEN 0 WHEN '会社予想' THEN 1 ELSE 2 END
        """, (project_id,))]
    for row in rows:
        row["result_type"] = normalize_result_type(row.get("result_type"))
    return rows


def save_pl_entries(project_id: int, entries: list[dict], db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """PLを行単位で更新し、渡されなかった旧行やNoneの既存値を保持する。"""
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
        return row.get(field, row.get(labels[field]))

    normalized: list[dict] = []
    for raw in entries:
        fiscal_year = str(value(raw, "fiscal_year") or "").strip()
        if not fiscal_year:
            continue
        row = {field: value(raw, field) for field in fields}
        row["fiscal_year"] = fiscal_year
        row["result_type"] = normalize_result_type(row.get("result_type"))
        row["id"] = raw.get("id")
        normalized.append(row)
    if not normalized:
        return
    with get_connection(db_path) as conn:
        for row in normalized:
            existing = None
            if row.get("id") is not None:
                existing = conn.execute(
                    "SELECT * FROM pl_entries WHERE id=? AND project_id=?", (row["id"], project_id)
                ).fetchone()
            if existing is None:
                existing = conn.execute(
                    "SELECT * FROM pl_entries WHERE project_id=? AND fiscal_year=? AND result_type=?",
                    (project_id, row["fiscal_year"], row["result_type"]),
                ).fetchone()
            merged = dict(existing) if existing else {}
            for field in fields:
                incoming = row.get(field)
                if not _is_blank(incoming):
                    merged[field] = incoming
                elif field not in merged:
                    merged[field] = "手入力" if field == "source" else ("" if field == "basis_date" else None)
            if existing:
                conn.execute(
                    f"UPDATE pl_entries SET {', '.join(f'{field}=?' for field in fields)} WHERE id=? AND project_id=?",
                    [*[merged.get(field) for field in fields], existing["id"], project_id],
                )
            else:
                conn.execute(
                    f"INSERT INTO pl_entries (project_id, {', '.join(fields)}) VALUES (?, {', '.join('?' for _ in fields)})",
                    [project_id, *[merged.get(field) for field in fields]],
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
        rows = [dict(row) for row in conn.execute("""
            SELECT * FROM segment_entries WHERE project_id=?
            ORDER BY fiscal_year,
                CASE result_type WHEN '実績' THEN 0 WHEN '会社予想' THEN 1 ELSE 2 END,
                display_order, segment_name
        """, (project_id,))]
    for row in rows:
        row["result_type"] = normalize_result_type(row.get("result_type"))
    return rows


def save_segment_entries(project_id: int, entries: list[dict], db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """セグメントを行単位で更新し、空表やNoneで既存データを消さない。"""
    fields = [
        "fiscal_year", "result_type", "segment_name", "sales", "operating_profit",
        "source", "note", "display_order", "row_type",
    ]
    normalized = []
    for raw in entries:
        fiscal_year = str(raw.get("fiscal_year", raw.get("年度")) or "").strip()
        segment_name = str(raw.get("segment_name", raw.get("セグメント")) or "").strip()
        if not fiscal_year or not segment_name:
            continue
        normalized.append({
            "id": raw.get("id"),
            "fiscal_year": fiscal_year,
            "result_type": normalize_result_type(raw.get("result_type", raw.get("実績／会社予想／自分予想", "実績"))),
            "segment_name": segment_name,
            "sales": raw.get("sales", raw.get("売上高（百万円）")),
            "operating_profit": raw.get("operating_profit", raw.get("営業利益（百万円）")),
            "source": raw.get("source", raw.get("出典")),
            "note": raw.get("note", raw.get("メモ")),
            "display_order": raw.get("display_order", raw.get("表示順")),
            "row_type": raw.get("row_type", "セグメント"),
        })
    if not normalized:
        return
    with get_connection(db_path) as conn:
        for row in normalized:
            existing = None
            if row.get("id") is not None:
                existing = conn.execute(
                    "SELECT * FROM segment_entries WHERE id=? AND project_id=?", (row["id"], project_id)
                ).fetchone()
            if existing is None:
                existing = conn.execute(
                    """SELECT * FROM segment_entries
                       WHERE project_id=? AND fiscal_year=? AND result_type=? AND segment_name=?""",
                    (project_id, row["fiscal_year"], row["result_type"], row["segment_name"]),
                ).fetchone()
            merged = dict(existing) if existing else {}
            for field in fields:
                incoming = row.get(field)
                if not _is_blank(incoming):
                    merged[field] = incoming
                elif field not in merged:
                    defaults = {"source": "手入力", "note": "", "display_order": 0, "row_type": "セグメント"}
                    merged[field] = defaults.get(field)
            if existing:
                conn.execute(
                    f"UPDATE segment_entries SET {', '.join(f'{field}=?' for field in fields)} WHERE id=? AND project_id=?",
                    [*[merged.get(field) for field in fields], existing["id"], project_id],
                )
            else:
                conn.execute(
                    f"INSERT INTO segment_entries (project_id, {', '.join(fields)}) VALUES (?, {', '.join('?' for _ in fields)})",
                    [project_id, *[merged.get(field) for field in fields]],
                )


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


def _plain_dict(value: object) -> dict:
    if isinstance(value, dict):
        return dict(value)
    converter = getattr(value, "to_dict", None)
    if callable(converter):
        converted = converter()
        return dict(converted) if isinstance(converted, dict) else {}
    return {}


def _source_key(kind: str, row: dict) -> str:
    """同じ取得候補を何度保存しても重複させない安定キー。"""
    identity = {
        "kind": kind,
        "document_id": row.get("document_id") or "",
        "raw_label": row.get("raw_label") or "",
        "xbrl_tag": row.get("xbrl_tag") or "",
        "fiscal_year": row.get("fiscal_year") or "",
        "metric": row.get("metric") or "",
        "unit": row.get("unit") or "",
    }
    encoded = json.dumps(identity, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def save_company_master_record(record: object, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """取得した企業マスターをプロジェクト単位で保存し、空値では既存値を消さない。"""
    row = _plain_dict(record)
    project_id = int(row["project_id"])
    with get_connection(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM company_master_records WHERE project_id=?", (project_id,)
        ).fetchone()
        current = dict(existing) if existing else {}
        fields = (
            "stock_code", "company_name", "market", "industry", "accounting_standard",
            "provider", "confirmed_at",
        )
        merged = {field: current.get(field, "") for field in fields}
        for field in fields:
            if not _is_blank(row.get(field)):
                merged[field] = row[field]
        raw_data = row.get("raw_data") if isinstance(row.get("raw_data"), dict) else {}
        if not raw_data and existing:
            raw_data = _decode_json_object(existing["raw_json"])
        conn.execute(
            """INSERT INTO company_master_records
               (project_id, stock_code, company_name, market, industry, accounting_standard,
                provider, raw_json, confirmed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(project_id) DO UPDATE SET
                 stock_code=excluded.stock_code, company_name=excluded.company_name,
                 market=excluded.market, industry=excluded.industry,
                 accounting_standard=excluded.accounting_standard, provider=excluded.provider,
                 raw_json=excluded.raw_json, confirmed_at=excluded.confirmed_at,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                project_id, merged["stock_code"], merged["company_name"], merged["market"],
                merged["industry"], merged["accounting_standard"], merged["provider"] or "手入力",
                json.dumps(raw_data, ensure_ascii=False), merged["confirmed_at"],
            ),
        )
        project = conn.execute("SELECT * FROM projects WHERE id=?", (project_id,)).fetchone()
        if project:
            updates = {}
            if _is_blank(project["stock_code"]) and merged["stock_code"]:
                updates["stock_code"] = merged["stock_code"]
            if _is_blank(project["company_name"]) and merged["company_name"]:
                updates["company_name"] = merged["company_name"]
            if "market" in project.keys() and _is_blank(project["market"]) and merged["market"]:
                updates["market"] = merged["market"]
            if "sector33" in project.keys() and _is_blank(project["sector33"]) and merged["industry"]:
                updates["sector33"] = merged["industry"]
            if updates:
                conn.execute(
                    f"UPDATE projects SET {', '.join(f'{key}=?' for key in updates)}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    [*updates.values(), project_id],
                )


def get_company_master_record(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> dict | None:
    with get_connection(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM company_master_records WHERE project_id=?", (project_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["raw_data"] = _decode_json_object(result.pop("raw_json", "{}"))
    return result


def save_source_document(document: object, db_path: str | Path = DEFAULT_DB_PATH) -> int:
    row = _plain_dict(document)
    if not str(row.get("document_id") or "").strip():
        raise ValueError("取得書類のdocument_idが必要です。")
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO source_documents
               (project_id, provider, document_id, document_type, title, source_url,
                filing_date, fiscal_year, retrieved_at, metadata_json, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(project_id, provider, document_id) DO UPDATE SET
                 document_type=excluded.document_type, title=excluded.title,
                 source_url=excluded.source_url, filing_date=excluded.filing_date,
                 fiscal_year=excluded.fiscal_year, retrieved_at=excluded.retrieved_at,
                 metadata_json=excluded.metadata_json, updated_at=CURRENT_TIMESTAMP""",
            (
                int(row["project_id"]), str(row.get("provider") or ""), str(row["document_id"]),
                str(row.get("document_type") or ""), str(row.get("title") or ""),
                str(row.get("source_url") or ""), str(row.get("filing_date") or ""),
                str(row.get("fiscal_year") or ""), str(row.get("retrieved_at") or ""),
                json.dumps(metadata, ensure_ascii=False),
            ),
        )
        saved = conn.execute(
            "SELECT id FROM source_documents WHERE project_id=? AND provider=? AND document_id=?",
            (int(row["project_id"]), str(row.get("provider") or ""), str(row["document_id"])),
        ).fetchone()
        return int(saved["id"])


def list_source_documents(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM source_documents WHERE project_id=? ORDER BY filing_date DESC, id DESC",
            (project_id,),
        )]
    for row in rows:
        row["metadata"] = _decode_json_object(row.pop("metadata_json", "{}"))
    return rows


def _document_row_id(conn: sqlite3.Connection, project_id: int, document_id: str) -> int | None:
    if not document_id:
        return None
    row = conn.execute(
        "SELECT id FROM source_documents WHERE project_id=? AND document_id=? ORDER BY id DESC LIMIT 1",
        (project_id, document_id),
    ).fetchone()
    return int(row["id"]) if row else None


def save_financial_facts(facts: list[object], db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """根拠付き財務候補を保存する。手動修正値はNoneで上書きしない。"""
    with get_connection(db_path) as conn:
        for fact in facts:
            row = _plain_dict(fact)
            project_id = int(row["project_id"])
            source_key = str(row.get("source_key") or _source_key("financial", row))
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            conn.execute(
                """INSERT INTO financial_facts
                   (project_id, source_key, document_row_id, common_key, raw_label, xbrl_tag,
                    value, unit, fiscal_year, accounting_standard, source, manual_value,
                    mapping_scope, status, confirmed_at, metadata_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(project_id, source_key) DO UPDATE SET
                     document_row_id=COALESCE(excluded.document_row_id, financial_facts.document_row_id),
                     common_key=COALESCE(NULLIF(excluded.common_key, ''), financial_facts.common_key),
                     raw_label=COALESCE(NULLIF(excluded.raw_label, ''), financial_facts.raw_label),
                     xbrl_tag=COALESCE(NULLIF(excluded.xbrl_tag, ''), financial_facts.xbrl_tag),
                     value=COALESCE(excluded.value, financial_facts.value),
                     unit=COALESCE(NULLIF(excluded.unit, ''), financial_facts.unit),
                     fiscal_year=COALESCE(NULLIF(excluded.fiscal_year, ''), financial_facts.fiscal_year),
                     accounting_standard=COALESCE(NULLIF(excluded.accounting_standard, ''), financial_facts.accounting_standard),
                     source=COALESCE(NULLIF(excluded.source, ''), financial_facts.source),
                     manual_value=COALESCE(excluded.manual_value, financial_facts.manual_value),
                     mapping_scope=COALESCE(NULLIF(excluded.mapping_scope, ''), financial_facts.mapping_scope),
                     status=COALESCE(NULLIF(excluded.status, ''), financial_facts.status),
                     confirmed_at=COALESCE(NULLIF(excluded.confirmed_at, ''), financial_facts.confirmed_at),
                     metadata_json=CASE WHEN excluded.metadata_json='{}' THEN financial_facts.metadata_json ELSE excluded.metadata_json END,
                     updated_at=CURRENT_TIMESTAMP""",
                (
                    project_id, source_key, _document_row_id(conn, project_id, str(row.get("document_id") or "")),
                    row.get("common_key"), str(row.get("raw_label") or ""), str(row.get("xbrl_tag") or ""),
                    row.get("value"), str(row.get("unit") or ""), str(row.get("fiscal_year") or ""),
                    str(row.get("accounting_standard") or ""), str(row.get("source") or ""),
                    row.get("manual_value"), str(row.get("mapping_scope") or "unresolved"),
                    str(row.get("status") or "candidate"), str(row.get("confirmed_at") or ""),
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )


def list_financial_facts(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM financial_facts WHERE project_id=? ORDER BY fiscal_year, common_key, id",
            (project_id,),
        )]
    for row in rows:
        row["metadata"] = _decode_json_object(row.pop("metadata_json", "{}"))
    return rows


def save_segment_facts(facts: list[object], db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """根拠付きセグメント候補を保存し、未確認名は候補のまま保持する。"""
    with get_connection(db_path) as conn:
        for fact in facts:
            row = _plain_dict(fact)
            project_id = int(row["project_id"])
            source_key = str(row.get("source_key") or _source_key("segment", row))
            metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            conn.execute(
                """INSERT INTO segment_facts
                   (project_id, source_key, document_row_id, segment_name, raw_label, xbrl_tag,
                    metric, value, unit, fiscal_year, source, manual_value, display_order,
                    status, confirmed_at, metadata_json, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                   ON CONFLICT(project_id, source_key) DO UPDATE SET
                     document_row_id=COALESCE(excluded.document_row_id, segment_facts.document_row_id),
                     segment_name=COALESCE(NULLIF(excluded.segment_name, ''), segment_facts.segment_name),
                     raw_label=COALESCE(NULLIF(excluded.raw_label, ''), segment_facts.raw_label),
                     xbrl_tag=COALESCE(NULLIF(excluded.xbrl_tag, ''), segment_facts.xbrl_tag),
                     metric=COALESCE(NULLIF(excluded.metric, ''), segment_facts.metric),
                     value=COALESCE(excluded.value, segment_facts.value),
                     unit=COALESCE(NULLIF(excluded.unit, ''), segment_facts.unit),
                     fiscal_year=COALESCE(NULLIF(excluded.fiscal_year, ''), segment_facts.fiscal_year),
                     source=COALESCE(NULLIF(excluded.source, ''), segment_facts.source),
                     manual_value=COALESCE(excluded.manual_value, segment_facts.manual_value),
                     display_order=COALESCE(excluded.display_order, segment_facts.display_order),
                     status=COALESCE(NULLIF(excluded.status, ''), segment_facts.status),
                     confirmed_at=COALESCE(NULLIF(excluded.confirmed_at, ''), segment_facts.confirmed_at),
                     metadata_json=CASE WHEN excluded.metadata_json='{}' THEN segment_facts.metadata_json ELSE excluded.metadata_json END,
                     updated_at=CURRENT_TIMESTAMP""",
                (
                    project_id, source_key, _document_row_id(conn, project_id, str(row.get("document_id") or "")),
                    row.get("segment_name"), str(row.get("raw_label") or ""), str(row.get("xbrl_tag") or ""),
                    str(row.get("metric") or "revenue"), row.get("value"), str(row.get("unit") or ""),
                    str(row.get("fiscal_year") or ""), str(row.get("source") or ""), row.get("manual_value"),
                    int(row.get("display_order") or 0), str(row.get("status") or "candidate"),
                    str(row.get("confirmed_at") or ""), json.dumps(metadata, ensure_ascii=False),
                ),
            )


def list_segment_facts(project_id: int, db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM segment_facts WHERE project_id=? ORDER BY fiscal_year, display_order, id",
            (project_id,),
        )]
    for row in rows:
        row["metadata"] = _decode_json_object(row.pop("metadata_json", "{}"))
    return rows


def _mapping_scope_key(row: dict) -> str:
    scope_type = str(row.get("scope_type") or "").strip()
    return "|".join((
        scope_type,
        str(row.get("project_id") or "") if scope_type == "company" else "",
        str(row.get("accounting_standard") or "").strip().upper() if scope_type == "accounting" else "",
        str(row.get("raw_identifier") or "").strip().casefold(),
    ))


def save_mapping_rule(rule: object, db_path: str | Path = DEFAULT_DB_PATH) -> None:
    """ユーザーが確認した元タグ・元表記と共通項目の対応を保存する。"""
    row = _plain_dict(rule)
    if row.get("scope_type") not in {"company", "accounting", "global"}:
        raise ValueError("マッピング範囲はcompany、accounting、globalから選択してください。")
    if row.get("common_key") not in {
        "revenue", "cost_of_sales", "gross_profit", "sga",
        "operating_income", "ordinary_income", "net_income", "eps",
    }:
        raise ValueError("未対応の共通財務キーです。")
    if not str(row.get("raw_identifier") or "").strip():
        raise ValueError("元のXBRLタグまたは元表記が必要です。")
    if row["scope_type"] == "company" and row.get("project_id") is None:
        raise ValueError("企業固有マッピングにはproject_idが必要です。")
    if row["scope_type"] == "accounting" and not str(row.get("accounting_standard") or "").strip():
        raise ValueError("会計基準別マッピングには会計基準が必要です。")
    confirmed_at = str(row.get("confirmed_at") or datetime.now().astimezone().isoformat(timespec="seconds"))
    with get_connection(db_path) as conn:
        conn.execute(
            """INSERT INTO mapping_rules
               (scope_key, scope_type, project_id, accounting_standard, raw_identifier,
                common_key, note, confirmed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(scope_key) DO UPDATE SET common_key=excluded.common_key,
                 note=excluded.note, confirmed_at=excluded.confirmed_at,
                 updated_at=CURRENT_TIMESTAMP""",
            (
                _mapping_scope_key(row), row["scope_type"], row.get("project_id"),
                str(row.get("accounting_standard") or ""), str(row["raw_identifier"]),
                str(row["common_key"]), str(row.get("note") or ""), confirmed_at,
            ),
        )


def list_mapping_rules(db_path: str | Path = DEFAULT_DB_PATH) -> list[dict]:
    with get_connection(db_path) as conn:
        return [dict(row) for row in conn.execute(
            """SELECT scope_type, project_id, accounting_standard, raw_identifier,
                      common_key, note, confirmed_at
               FROM mapping_rules
               ORDER BY CASE scope_type WHEN 'company' THEN 0 WHEN 'accounting' THEN 1 ELSE 2 END, id"""
        )]


def confirm_financial_fact_mapping(
    fact_id: int,
    common_key: str,
    scope_type: str,
    *,
    manual_value: float | None = None,
    db_path: str | Path = DEFAULT_DB_PATH,
) -> None:
    """候補確認と学習ルール保存を同じトランザクションで行う。"""
    if common_key not in {
        "revenue", "cost_of_sales", "gross_profit", "sga",
        "operating_income", "ordinary_income", "net_income", "eps",
    }:
        raise ValueError("未対応の共通財務キーです。")
    with get_connection(db_path) as conn:
        fact = conn.execute("SELECT * FROM financial_facts WHERE id=?", (fact_id,)).fetchone()
        if not fact:
            raise ValueError("確認対象の財務候補が見つかりません。")
        rule = {
            "scope_type": scope_type,
            "project_id": fact["project_id"] if scope_type == "company" else None,
            "accounting_standard": fact["accounting_standard"] if scope_type == "accounting" else "",
            "raw_identifier": fact["xbrl_tag"] or fact["raw_label"],
            "common_key": common_key,
        }
        if scope_type not in {"company", "accounting", "global"}:
            raise ValueError("マッピング範囲が不正です。")
        confirmed_at = datetime.now().astimezone().isoformat(timespec="seconds")
        conn.execute(
            """INSERT INTO mapping_rules
               (scope_key, scope_type, project_id, accounting_standard, raw_identifier,
                common_key, note, confirmed_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, '', ?, CURRENT_TIMESTAMP)
               ON CONFLICT(scope_key) DO UPDATE SET common_key=excluded.common_key,
                 confirmed_at=excluded.confirmed_at, updated_at=CURRENT_TIMESTAMP""",
            (
                _mapping_scope_key(rule), scope_type, rule["project_id"], rule["accounting_standard"],
                rule["raw_identifier"], common_key, confirmed_at,
            ),
        )
        conn.execute(
            """UPDATE financial_facts SET common_key=?, mapping_scope=?, status='confirmed',
               manual_value=COALESCE(?, manual_value), confirmed_at=?, updated_at=CURRENT_TIMESTAMP
               WHERE id=?""",
            (common_key, scope_type, manual_value, confirmed_at, fact_id),
        )
