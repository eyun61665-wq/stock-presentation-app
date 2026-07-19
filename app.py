"""日本株の銘柄発表MVP。"""
from __future__ import annotations

import os
import time

import pandas as pd
import requests
import streamlit as st

from calculations import (catalyst_additional_sales, catalyst_price_impact, catalyst_sales_from_rate, eps, market_capitalization,
                          operating_margin, sales_growth_rate, scenario_calculation)
from catalyst_suggestion_service import suggest_catalysts
from catalyst_research_service import research_catalyst_context
from company_profile_service import fetch_company_profile, fill_empty_profile
from database import (delete_project, get_catalyst, get_pl_entries, get_project, get_scenarios,
                      get_segment_entries, get_segment_metrics, initialize_database, list_projects,
                      save_catalyst, save_pl_entries, save_segment_entries, save_segment_metrics,
                      save_project, save_scenarios)
from validators import (EVIDENCE_CATEGORIES, catalyst_warnings, complete_pl_records,
                        frame_to_records, pl_warnings, validate_pl_entries, validate_project)
from company_data_service import (build_company_forecast_record, build_pl_import_records, fetch_company_data,
                                  find_companies, find_project_by_code,
                                  should_show_no_results)
from data_normalizer import display_code
from jquants_client import JQuantsApiError, JQuantsClient
from jpx_master import JPXMasterError, get_master, search_master
from edinet_client import EDINETClient, EDINETError
from edinet_code_master import (EDINETMasterError, find_edinet_company,
                                load_edinet_code_master)
from edinet_financial_service import load_edinet_financials
from pdf_financial_extractor import extract_pdf_preview
from pdf_financial_extractor import extract_pdf_pages
from data_sources import DataSourceError, download_pdf, fetch_ir_pdf_links
from financial_parser import PL_ITEMS, parse_pl_text
from financial_document_service import (load_official_financials, load_official_financials_with_ai,
                                          load_official_financials_with_gemini,
                                          merge_imported_pl, merge_imported_segments,
                                          merge_imported_segment_metrics,
                                          parse_financial_document, select_recent_pl_records)
from ai_financial_parser import AIFinancialParserError, OpenAIFinancialParser
from gemini_financial_parser import GeminiFinancialParser, GeminiFinancialParserError
from ir_source_discovery import (IRSourceDiscoveryError, VERIFIED_IR_SOURCES,
                                 discover_ir_source)
from exporter import pl_to_csv, pl_to_excel
from stock_data import valuation_snapshot
from financial_tables import (RESULT_TYPES, color_columns, metadata_from_entries,
                              calculate_pl_records, normalize_metadata,
                              column_title,
                              pl_display_frame, pl_input_frame,
                              pl_records_from_frame, segment_combined_input_frame,
                              segment_records_from_combined_frame, segment_totals_frame,
                              segment_display_frame,
                              segment_input_frame, segment_names_from_entries,
                              segment_records_from_frames, segment_analysis_frame,
                              segment_metrics_input_frame, segment_metrics_from_frame)
from forecast_service import apply_catalyst_to_self_forecast, catalyst_base
from ui_layout import (DISPLAY_AUTO, DISPLAY_DESKTOP, DISPLAY_MOBILE,
                       DISPLAY_OPTIONS, resolve_display_mode,
                       user_agent_from_headers)

st.set_page_config(
    page_title="銘柄発表",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="auto",
)


@st.cache_resource(show_spinner=False)
def initialize_app_storage() -> bool:
    """SQLiteのスキーマ確認はプロセスごとに一度だけ行う。"""
    initialize_database()
    return True


initialize_app_storage()

SCENARIOS = ["弱気", "標準", "強気"]
PL_INPUT_COLUMNS = ["年度", "実績／会社予想／自分予想", "売上高（百万円）", "営業利益（百万円）", "純利益（百万円）", "平均株式数（百万株）"]
PL_CALCULATED_COLUMNS = ["売上成長率", "営業利益率", "EPS（円）"]


def current_display_mode() -> str:
    """自動判定または利用者の指定から、現在の画面モードを返す。"""
    preference = st.session_state.get("display_mode", DISPLAY_AUTO)
    try:
        user_agent = user_agent_from_headers(st.context.headers)
    except Exception:
        user_agent = ""
    return resolve_display_mode(preference, user_agent)


def is_mobile_view() -> bool:
    return current_display_mode() == DISPLAY_MOBILE


def responsive_columns(spec, **kwargs):
    """PCでは横並び、スマホでは上から順に並ぶ入れ物を返す。"""
    count = spec if isinstance(spec, int) else len(spec)
    if is_mobile_view():
        return [st.container() for _ in range(count)]
    return st.columns(spec, **kwargs)


def metric_slots(count: int):
    """PCは1行、スマホは最大2枚ずつのカード配置にする。"""
    if not is_mobile_view():
        return st.columns(count)
    slots = []
    for start in range(0, count, 2):
        slots.extend(st.columns(min(2, count - start)))
    return slots


def apply_responsive_styles() -> None:
    """スマホで余白と見出しが大きくなりすぎないようにする。"""
    st.markdown(
        """
        <style>
        @media (max-width: 700px) {
          .block-container {padding: 1rem 0.85rem 5rem;}
          h1 {font-size: 2rem !important; line-height: 1.2 !important;}
          h2 {font-size: 1.55rem !important;}
          h3 {font-size: 1.25rem !important;}
          [data-testid="stMetricValue"] {font-size: 1.35rem;}
          [data-testid="stMetricLabel"] {font-size: 0.82rem;}
          div[data-testid="stForm"] {padding: 0.85rem;}
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def reset_navigation_widgets() -> None:
    """PC・スマホ切替時に、もう一方の古い選択状態を持ち越さない。"""
    for key in list(st.session_state):
        if str(key).startswith((
            "mobile_project_selector_",
            "desktop_project_selector_",
            "mobile_page_selector_",
            "desktop_page_selector_",
        )):
            st.session_state.pop(key, None)
    st.session_state["navigation_nonce"] = int(st.session_state.get("navigation_nonce", 0)) + 1


def start_new_project() -> None:
    set_active_project(None)


def set_active_project(project_id: int | None) -> None:
    """プログラム側で銘柄を切り替えたとき、PC・スマホの選択欄も同期する。"""
    st.session_state.project_id = project_id
    # 銘柄切替・自動補完後に、直前のフォーム値が取得値を隠さないようにする。
    for key in list(st.session_state):
        if str(key).startswith("project_form_"):
            st.session_state.pop(key, None)
    reset_navigation_widgets()


def number(value: object, default: float = 0.0) -> float:
    if value is None or value == "":
        return default
    try:
        if pd.isna(value):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def require_project() -> int | None:
    project_id = st.session_state.get("project_id")
    if project_id is None:
        st.info("サイドバーからプロジェクトを選択、または新規作成してください。")
        return None
    return int(project_id)


def show_warnings(messages: list[str]) -> None:
    for message in messages:
        st.warning(message)


def clear_state_prefix(prefix: str) -> None:
    """列構成変更時に古いdata_editor状態を残さない。"""
    for key in [key for key in st.session_state if str(key).startswith(prefix)]:
        del st.session_state[key]


def get_jquants_api_key() -> str | None:
    key = os.getenv("JQUANTS_API_KEY")
    if key:
        return key
    try:
        return st.secrets.get("JQUANTS_API_KEY")
    except Exception:
        return None


def get_openai_api_key() -> str | None:
    """AIキーは環境変数またはStreamlit Secretsからだけ読み込む。"""
    key = os.getenv("OPENAI_API_KEY")
    if key:
        return key
    try:
        return st.secrets.get("OPENAI_API_KEY")
    except Exception:
        return None


def get_openai_financial_model() -> str:
    model = os.getenv("OPENAI_FINANCIAL_MODEL")
    if model:
        return model
    try:
        return str(st.secrets.get("OPENAI_FINANCIAL_MODEL", "gpt-5.6-luna"))
    except Exception:
        return "gpt-5.6-luna"


def get_gemini_api_key() -> str | None:
    """Geminiキーは環境変数またはStreamlit Secretsからだけ読み込む。"""
    key = os.getenv("GEMINI_API_KEY")
    if key:
        return key
    try:
        return st.secrets.get("GEMINI_API_KEY")
    except Exception:
        return None


def get_gemini_financial_model() -> str:
    model = os.getenv("GEMINI_FINANCIAL_MODEL")
    if model:
        return model
    try:
        return str(st.secrets.get("GEMINI_FINANCIAL_MODEL", "gemini-3.5-flash"))
    except Exception:
        return "gemini-3.5-flash"


def get_edinet_api_key() -> str | None:
    """無料EDINETキーもソースやSQLiteへ保存しない。"""
    key = os.getenv("EDINET_API_KEY")
    if key:
        return key
    try:
        return st.secrets.get("EDINET_API_KEY")
    except Exception:
        return None


@st.cache_data(ttl=300, show_spinner=False)
def cached_company_search(api_key: str, query: str, refresh_nonce: int) -> list[dict]:
    return find_companies(JQuantsClient(api_key), query)


@st.cache_data(ttl=300, show_spinner=False)
def cached_company_data(api_key: str, master: dict, refresh_nonce: int) -> dict:
    return fetch_company_data(JQuantsClient(api_key), master)


@st.cache_data(ttl="6h", max_entries=20, show_spinner=False)
def cached_official_financials(ir_url: str, max_years: int, refresh_nonce: int) -> dict:
    return load_official_financials(ir_url, max_years=max_years, refresh=bool(refresh_nonce))


@st.cache_data(ttl="7d", max_entries=20, show_spinner=False)
def cached_edinet_financials(stock_code: str, max_years: int, refresh_nonce: int) -> dict:
    """証券コードからEDINETコードを自動特定し、無料XBRLを取得する。"""
    api_key = get_edinet_api_key()
    if not api_key:
        return {
            "pl_records": [], "segment_records": [], "segment_metrics": [], "documents": [],
            "warnings": ["EDINET無料キー未設定のため、決算短信PDFだけで解析しました。"],
        }
    rows, stale = load_edinet_code_master(refresh=bool(refresh_nonce))
    company = find_edinet_company(rows, stock_code)
    if company is None:
        raise EDINETMasterError("証券コードに対応するEDINETコードを確認できませんでした。")
    result = load_edinet_financials(
        api_key,
        company["edinet_code"],
        company["fiscal_year_end"],
        max_years=max_years,
    )
    if stale:
        result.setdefault("warnings", []).append("通信失敗のため前回のEDINETコード一覧を使用しました。")
    return result


@st.cache_data(ttl="24h", max_entries=20, show_spinner=False)
def cached_ai_official_financials(
    ir_url: str,
    model: str,
    max_years: int,
    refresh_nonce: int,
) -> dict:
    api_key = get_openai_api_key()
    if not api_key:
        raise AIFinancialParserError("OPENAI_API_KEYが設定されていません。")
    return load_official_financials_with_ai(
        ir_url,
        api_key,
        model,
        max_years=max_years,
        refresh=bool(refresh_nonce),
    )


@st.cache_data(ttl="24h", max_entries=20, show_spinner=False)
def cached_gemini_official_financials(
    ir_url: str,
    model: str,
    max_years: int,
    refresh_nonce: int,
) -> dict:
    api_key = get_gemini_api_key()
    if not api_key:
        raise GeminiFinancialParserError("GEMINI_API_KEYが設定されていません。")
    return load_official_financials_with_gemini(
        ir_url,
        api_key,
        model,
        max_years=max_years,
        refresh=bool(refresh_nonce),
    )


@st.cache_data(ttl="24h", max_entries=100, show_spinner=False)
def cached_ir_source(
    stock_code: str,
    company_name: str,
    saved_url: str,
    refresh_nonce: int,
) -> dict[str, str]:
    """保存済みURLを優先し、必要なときだけ公式IRをネット上で探索する。"""
    if saved_url and not refresh_nonce:
        return {"url": saved_url, "method": "保存済み公式IR"}
    return discover_ir_source(stock_code, company_name, refresh=bool(refresh_nonce))


@st.cache_data(ttl="24h", max_entries=100, show_spinner=False)
def cached_company_profile(ir_url: str, company_name: str, sector: str) -> dict[str, str]:
    return fetch_company_profile(ir_url, company_name, sector)


@st.cache_data(ttl="6h", max_entries=100, show_spinner=False)
def cached_catalyst_context(company_name: str, sector: str) -> list[dict[str, str]]:
    return research_catalyst_context(company_name, sector)


def enrich_project_profile(project_id: int, project: dict) -> dict:
    """企業入力時に公式説明と検討候補を空欄だけへ補う。"""
    current = get_project(project_id) or dict(project)
    company_name = str(current.get("company_name") or current.get("project_name") or "").strip()
    stock_code = str(current.get("stock_code") or "").strip()
    ir_url = str(current.get("ir_url") or "").strip()
    verified_ir_url = VERIFIED_IR_SOURCES.get(stock_code, "")
    source_changed = bool(verified_ir_url and ir_url and ir_url != verified_ir_url)
    # 旧版が業種コードなどから作った根拠のない候補文は引き継がない。
    legacy_auto_prefixes = (
        "確認候補：", "検討候補：", "検討仮説：", "候補：", "主な確認リスク：",
    )
    for field in ("business_description", "strengths", "investment_thesis", "catalysts", "risks"):
        if str(current.get(field) or "").startswith(legacy_auto_prefixes):
            current[field] = ""
    if verified_ir_url:
        ir_url = verified_ir_url
    elif stock_code and company_name and not ir_url:
        try:
            source = cached_ir_source(stock_code, company_name, "", 0)
            ir_url = source["url"]
        except (IRSourceDiscoveryError, DataSourceError, ValueError, OSError):
            pass
    if source_changed:
        # 別銘柄の自動生成候補だけを破棄する。通常の手入力文章は残す。
        auto_prefixes = {
            "business_description": ("確認候補：",),
            "strengths": ("検討候補：",),
            "investment_thesis": ("検討仮説：",),
            "catalysts": ("候補：",),
            "risks": ("主な確認点：",),
        }
        for field, prefixes in auto_prefixes.items():
            if str(current.get(field) or "").startswith(prefixes):
                current[field] = ""
    try:
        candidates = cached_company_profile(
            ir_url,
            company_name,
            str(current.get("sector33") or current.get("sector17") or ""),
        )
        # 一時的な通信失敗で空結果がキャッシュされていても、事業内容だけは再確認する。
        if ir_url and not candidates.get("business_description"):
            candidates = fetch_company_profile(
                ir_url,
                company_name,
                str(current.get("sector33") or current.get("sector17") or ""),
            )
    except (requests.RequestException, ValueError, OSError):
        candidates = fetch_company_profile(
            "", company_name, str(current.get("sector33") or current.get("sector17") or "")
        )
    updated = fill_empty_profile(current, candidates)
    updated.update({"id": project_id, "ir_url": ir_url or current.get("ir_url", "")})
    save_project(updated)
    return updated


def jquants_financial_fallback(stock_code: str, refresh_nonce: int = 0) -> dict:
    """公式PDFが取れないとき、任意設定のJ-QuantsからPLだけを補完する。"""
    api_key = get_jquants_api_key()
    if not api_key:
        raise DataSourceError("公式資料を取得できず、J-Quantsも未設定です。空欄の表へ手入力できます。")
    candidates = cached_company_search(api_key, stock_code, refresh_nonce)
    exact = next(
        (row for row in candidates if row.get("stock_code") == display_code(stock_code)),
        candidates[0] if candidates else None,
    )
    if exact is None:
        raise DataSourceError("銘柄コードに一致する企業データを取得できませんでした。")
    company_data = cached_company_data(api_key, exact, refresh_nonce)
    pl_records = build_pl_import_records(company_data.get("financials", []))
    forecast = build_company_forecast_record(company_data.get("forecast"))
    if forecast:
        pl_records.append(forecast)
    for row in pl_records:
        row["shares_outstanding"] = row.get("average_shares", row.get("shares_outstanding"))
    return {
        "pl_records": pl_records,
        "segment_records": [],
        "documents": [{"title": "J-Quants 財務サマリー", "url": "J-Quants"}],
        "warnings": ["PLの不足項目はJ-Quants財務サマリーで補完しました。"],
    }


def merge_financial_previews(primary: dict, supplement: dict) -> dict:
    """公式資料の値を優先し、欠損項目だけを構造化データで補う。"""
    merged = {**primary}
    by_key = {
        (str(row.get("fiscal_year")), str(row.get("result_type", "実績"))): dict(row)
        for row in primary.get("pl_records", [])
    }
    for row in supplement.get("pl_records", []):
        key = (str(row.get("fiscal_year")), str(row.get("result_type", "実績")))
        current = by_key.get(key, {})
        for field, value in row.items():
            if current.get(field) in (None, "") and value not in (None, ""):
                current[field] = value
        by_key[key] = current
    merged["pl_records"] = [by_key[key] for key in sorted(by_key)]
    segment_by_key = {
        (
            str(row.get("fiscal_year")), str(row.get("result_type", "実績")),
            str(row.get("segment_name")),
        ): dict(row)
        for row in primary.get("segment_records", [])
    }
    for row in supplement.get("segment_records", []):
        key = (
            str(row.get("fiscal_year")), str(row.get("result_type", "実績")),
            str(row.get("segment_name")),
        )
        current = segment_by_key.get(key, {})
        for field, value in row.items():
            if current.get(field) in (None, "") and value not in (None, ""):
                current[field] = value
        segment_by_key[key] = current
    merged["segment_records"] = [segment_by_key[key] for key in sorted(segment_by_key)]

    metric_by_key = {
        (
            str(row.get("fiscal_year")), str(row.get("result_type", "実績")),
            str(row.get("row_label")),
        ): dict(row)
        for row in primary.get("segment_metrics", [])
    }
    for row in supplement.get("segment_metrics", []):
        key = (
            str(row.get("fiscal_year")), str(row.get("result_type", "実績")),
            str(row.get("row_label")),
        )
        metric_by_key.setdefault(key, dict(row))
    merged["segment_metrics"] = [metric_by_key[key] for key in sorted(metric_by_key)]

    profile = dict(primary.get("company_profile") or {})
    for field, value in (supplement.get("company_profile") or {}).items():
        if value and not profile.get(field):
            profile[field] = value
    merged["company_profile"] = profile
    catalysts = [*primary.get("catalyst_candidates", [])]
    known_names = {str(row.get("name")) for row in catalysts}
    for candidate in supplement.get("catalyst_candidates", []):
        if str(candidate.get("name")) not in known_names:
            catalysts.append(candidate)
            known_names.add(str(candidate.get("name")))
    merged["catalyst_candidates"] = catalysts

    documents = [*primary.get("documents", []), *supplement.get("documents", [])]
    merged["documents"] = list({(row.get("title"), row.get("url")): row for row in documents}.values())
    merged["warnings"] = [*primary.get("warnings", []), *supplement.get("warnings", [])]
    return merged


def save_structured_pl(project_id: int, records: list[dict]) -> int:
    """取得PLを同年度のみ更新し、手入力した他年度は残して保存する。"""
    by_key = {
        (str(row["fiscal_year"]), str(row.get("result_type", "実績"))): row
        for row in get_pl_entries(project_id)
    }
    for row in records:
        year = str(row["fiscal_year"])
        result_type = str(row.get("result_type", "実績"))
        key = (year, result_type)
        merged = dict(by_key.get(key, {}))
        if result_type == "会社予想":
            # 未開示項目を古い値で補わず、APIの空欄をそのまま反映する。
            merged.update(row)
        else:
            merged.update({key: value for key, value in row.items() if value is not None})
        merged.update({
            "fiscal_year": row["fiscal_year"],
            "result_type": result_type,
            "shares_outstanding": row.get("average_shares", row.get("shares_outstanding")),
            "source": row.get("source", "J-Quants"),
            "basis_date": row.get("basis_date", ""),
        })
        by_key[key] = merged
    if records:
        save_pl_entries(project_id, [by_key[key] for key in sorted(by_key)])
        # data_editorが以前の空表を保持しないよう、次回表示時にDBから読み直す。
        st.session_state.pop(f"pl_{project_id}", None)
        st.session_state.pop(f"pl_years_state_{project_id}", None)
        clear_state_prefix(f"pl_matrix_simple_{project_id}")
        clear_state_prefix(f"pl_matrix_mobile_{project_id}")
    return len(records)


def company_fetch_panel(project: dict | None) -> None:
    """取得、プレビュー、選択反映。投資仮説等の手入力テキストには触れない。"""
    st.subheader("企業データ取得（J-Quants API V2）")
    api_key = get_jquants_api_key()
    if not api_key:
        st.info("APIキー未設定です。環境変数 `JQUANTS_API_KEY` または `.streamlit/secrets.toml` に `JQUANTS_API_KEY = \"...\"` を設定してください。手入力はそのまま利用できます。")
        return
    query = st.text_input("証券コード（4桁・5桁）または会社名の一部", key="jq_query")
    c1, c2 = responsive_columns(2)
    search_clicked = c1.button("企業を検索")
    refresh_clicked = c2.button("再取得（キャッシュ更新）")
    if refresh_clicked:
        st.session_state.jq_refresh_nonce = time.time_ns()
    nonce = int(st.session_state.get("jq_refresh_nonce", 0))
    if search_clicked and query.strip():
        st.session_state.jq_search_succeeded = False
        st.session_state.jq_candidates = []
        try:
            st.session_state.jq_candidates = cached_company_search(api_key, query.strip(), nonce)
            st.session_state.jq_search_succeeded = True
        except (JQuantsApiError, ValueError) as exc:
            st.error(str(exc))
    candidates = st.session_state.get("jq_candidates", [])
    if candidates:
        selected = st.selectbox("取得する企業を選択", candidates,
                                format_func=lambda row: f"{row['stock_code']} {row['company_name']}（{row['market']}）", key="jq_candidate")
        if st.button("データを取得"):
            try:
                st.session_state.jq_preview = cached_company_data(api_key, selected, nonce)
            except JQuantsApiError as exc:
                st.error(str(exc))
    elif should_show_no_results(search_clicked, bool(st.session_state.get("jq_search_succeeded")), candidates):
        st.warning("該当する企業が見つかりませんでした。コードまたは会社名を確認してください。")
    preview = st.session_state.get("jq_preview")
    if not preview:
        return
    master, price = preview["master"], preview["price"]
    st.markdown("#### 取得プレビュー（反映するまで保存されません）")
    st.caption(f"株価基準日：{price['date'] or '取得不可'} ／ データ取得日時：{price['retrieved_at']}")
    if price["date"] and str(price["date"]) < pd.Timestamp.now().strftime("%Y-%m-%d"):
        st.warning("契約プラン等により遅延データの可能性があります。")
    project = project or {}
    fields = [("company_name", "会社名", master["company_name"]), ("stock_code", "証券コード", master["stock_code"]),
              ("current_price", "終値（円）", price["close"]), ("company_name_en", "英文会社名", master["company_name_en"]),
              ("market", "市場区分", master["market"]), ("sector17", "17業種", master["sector17"]), ("sector33", "33業種", master["sector33"])]
    share_info = preview.get("share_info") or {}
    if share_info.get("net_issued_shares") is not None:
        fields.append(("shares_outstanding", "発行済株式数（百万株）", share_info["net_issued_shares"]))
        fields.append(("shares_source", "株式数の利用方法", share_info["shares_source"]))
    if share_info.get("bps") is not None:
        fields.append(("bps", "BPS（円）", share_info["bps"]))
    if share_info.get("annual_dividend") is not None:
        fields.append(("annual_dividend", "年間配当（円／株）", share_info["annual_dividend"]))
    comparison = pd.DataFrame([{"項目": label, "現在の値": project.get(key, ""), "API取得値": value,
                                "反映後の値": value} for key, label, value in fields])
    st.dataframe(comparison, hide_index=True, width="stretch")
    selected_keys = [key for key, label, value in fields if st.checkbox(f"{label}を反映", value=True, key=f"apply_{key}")]
    st.dataframe(pd.DataFrame(preview["financials"]), hide_index=True, width="stretch")
    company_forecast = build_company_forecast_record(preview.get("forecast"))
    if preview.get("forecast"):
        st.caption(f"最新会社予想：売上 {preview['forecast']['forecast_sales']} 百万円 ／ 営業利益 {preview['forecast']['forecast_operating_profit']} 百万円 ／ 純利益 {preview['forecast']['forecast_net_income']} 百万円")
    st.caption(f"時価総額（億円）：{preview['calculated']['market_cap']} ／ 現在PER：{preview['calculated']['current_per']} ／ 会社予想PER：{preview['calculated']['forecast_per']}")
    st.caption(
        f"PBR：{preview['calculated'].get('pbr')} ／ "
        f"配当利回り：{preview['calculated'].get('dividend_yield')}％"
    )
    apply_pl = st.checkbox("取得した本決算実績PL（最大5年）を反映", value=False)
    apply_forecast = st.checkbox(
        "取得した会社予想をPLへ反映",
        value=bool(company_forecast),
        disabled=company_forecast is None,
        help="会社が開示していない項目は空欄のまま保存します。",
    )
    if st.button("選択したデータを反映", type="primary"):
        updated = dict(project)
        api_values = {key: value for key, label, value in fields}
        for key in selected_keys:
            updated[key] = api_values[key]
        updated["price_date"] = price["date"] or ""
        updated["data_retrieved_at"] = price["retrieved_at"]
        updated["id"] = project.get("id")
        if not updated.get("project_name"):
            updated["project_name"] = master["company_name"] or master["stock_code"]
        project_id = save_project(updated)
        if apply_pl:
            save_structured_pl(project_id, build_pl_import_records(preview["financials"]))
        if apply_forecast and company_forecast:
            save_structured_pl(project_id, [company_forecast])
        set_active_project(project_id)
        st.success("選択したAPIデータを反映しました。投資仮説・カタリスト・リスクは変更していません。")
        st.rerun()


def free_data_panel(project: dict | None) -> None:
    """APIキーなしで使えるJPX検索と、確認後に反映するPDF/EDINET入口。"""
    st.subheader("無料データから企業を検索")
    query = st.text_input("証券コード（4桁）または会社名", key="jpx_query")
    a, b = responsive_columns(2)
    update = a.button("JPX企業マスター更新")
    search = b.button("無料データから企業を検索")
    if update or search:
        try:
            rows, _ = get_master(refresh=update)
            st.session_state.jpx_candidates = search_master(rows, query) if search else []
            st.session_state.jpx_search_ok = True
        except JPXMasterError as exc:
            st.session_state.jpx_search_ok = False
            st.error(str(exc))
    candidates = st.session_state.get("jpx_candidates", [])
    if candidates:
        selected = st.selectbox("JPX検索結果", candidates, format_func=lambda row: f"{row['stock_code']} {row['company_name']}（{row['market']}）", key="jpx_candidate")
        if st.button("JPX企業情報をプレビュー"):
            st.session_state.jpx_preview = selected
    elif search and st.session_state.get("jpx_search_ok"):
        st.warning("JPX企業マスター内に該当する企業が見つかりませんでした。")
    preview = st.session_state.get("jpx_preview")
    if preview:
        st.dataframe(pd.DataFrame([{"現在値": (project or {}).get("company_name", ""), "取得値": preview["company_name"], "データソース": "JPX", "基準日": "企業マスター"}]), hide_index=True)
        if st.checkbox("JPX企業情報を反映", key="apply_jpx") and st.button("JPX情報を反映"):
            data = dict(project or {})
            data.update({"id": data.get("id"), "project_name": data.get("project_name") or preview["company_name"], "company_name": preview["company_name"], "stock_code": preview["stock_code"], "market": preview["market"], "sector33": preview["sector33"]})
            set_active_project(save_project(data))
            st.success("JPX企業情報を反映しました。")

    st.subheader("EDINET財務データ取得")
    edinet_code = st.text_input("EDINETコード（例：E00000）", value=(project or {}).get("edinet_code", ""), key="edinet_code_input")
    st.caption("EDINETコードは企業ごとに確認して入力します。APIキーが必要な場合は `EDINET_API_KEY` を環境変数またはsecretsへ設定してください。")
    if st.button("EDINET財務データ取得"):
        if not edinet_code:
            st.info("EDINETコードを入力してください。手入力は継続して利用できます。")
        else:
            st.info("EDINETの書類一覧から有価証券報告書を選択して取得する機能は、APIキー設定後に利用できます。XBRLの取得結果は確認してからPLへ反映されます。")

    st.subheader("決算短信PDFを読み込む")
    uploaded = st.file_uploader("決算短信PDF（確認後に反映）", type=["pdf"])
    if uploaded:
        preview_pdf = extract_pdf_preview(uploaded.getvalue())
        st.text_area("PDF抽出プレビュー", preview_pdf["text"], height=180)
        st.json(preview_pdf["candidates"])
        st.info("抽出値は候補です。確認なしに保存・反映されません。PL画面で手入力または確認後の反映を行ってください。")


def ir_pl_panel(project: dict | None) -> None:
    """公式IRまたはアップロードPDFを解析し、確認済みデータだけPLへ渡す。"""
    st.subheader("IR資料・PL取得（無料）")
    project = project or {}
    c1, c2, c3 = responsive_columns(3)
    code = c1.text_input("銘柄コード（4桁）", value=project.get("stock_code", ""), key="ir_stock_code")
    company = c2.text_input("企業名", value=project.get("company_name", ""), key="ir_company_name")
    fiscal_period = c3.text_input("決算期", key="ir_fiscal_period")
    ir_url = st.text_input("企業公式IRページURL（任意）", key="official_ir_url")
    fetch_ir, refresh_cache = responsive_columns(2)
    fetch_clicked = fetch_ir.button("決算データを取得", type="primary")
    refresh_clicked = refresh_cache.button("資料キャッシュを更新")
    if refresh_clicked:
        st.session_state.ir_refresh = True
        st.session_state.ir_refresh_nonce = time.time_ns()
        st.session_state.pop("ir_structured_preview", None)
        st.info("次の取得時に最新データを確認します。")

    if fetch_clicked:
        if not code.strip() and not ir_url.strip():
            st.warning("銘柄コード、または企業公式IRページURLを入力してください。")
        if code.strip():
            api_key = get_jquants_api_key()
            if api_key:
                try:
                    nonce = int(st.session_state.get("ir_refresh_nonce", 0))
                    candidates = cached_company_search(api_key, code.strip(), nonce)
                    if not candidates:
                        st.warning("銘柄コードに該当する企業が見つかりませんでした。")
                    else:
                        exact = next(
                            (row for row in candidates if row["stock_code"] == code.strip()[:4]),
                            candidates[0],
                        )
                        st.session_state.ir_structured_preview = cached_company_data(
                            api_key, exact, nonce
                        )
                except (JQuantsApiError, ValueError) as exc:
                    st.error(f"決算データを取得できませんでした：{exc}")
            else:
                try:
                    master_rows, _ = get_master()
                    matches = search_master(master_rows, code.strip())
                    if matches:
                        st.info(
                            f"{matches[0]['company_name']}を確認しました。"
                            "J-Quants未設定のため、公式IRページURLを入力するか、決算短信PDFをアップロードしてください。"
                        )
                    else:
                        st.warning("銘柄コードに該当する企業が見つかりませんでした。")
                except JPXMasterError as exc:
                    st.error(str(exc))
        if ir_url.strip():
            try:
                st.session_state.ir_pdf_links = fetch_ir_pdf_links(ir_url.strip())
                if not st.session_state.ir_pdf_links:
                    st.info("公式IRページ内にPDF資料が見つかりませんでした。PDFをアップロードして確認できます。")
            except DataSourceError as exc:
                st.error(str(exc))

    structured_preview = st.session_state.get("ir_structured_preview")
    if structured_preview:
        company_name = structured_preview["master"]["company_name"]
        pl_records = build_pl_import_records(structured_preview["financials"])
        st.success(f"{company_name}の本決算PLを{len(pl_records)}年分取得しました。")
        if pl_records:
            preview_frame = pd.DataFrame(pl_records).rename(columns={
                "fiscal_year": "年度",
                "sales": "売上高（百万円）",
                "operating_profit": "営業利益（百万円）",
                "ordinary_profit": "経常利益（百万円）",
                "net_income": "純利益（百万円）",
                "average_shares": "平均株式数（百万株）",
                "eps": "EPS（円）",
                "basis_date": "開示日",
            })
            st.dataframe(
                preview_frame[[
                    "年度", "売上高（百万円）", "営業利益（百万円）",
                    "経常利益（百万円）", "純利益（百万円）",
                    "平均株式数（百万株）", "EPS（円）", "開示日",
                ]],
                hide_index=True,
            )
            current_project_id = project.get("id")
            if not current_project_id:
                st.warning("PLへ反映するには、先にプロジェクトを保存してください。")
            elif st.button("確認したPLをプロジェクトへ反映", type="primary"):
                imported_count = save_structured_pl(int(current_project_id), pl_records)
                st.success(f"{imported_count}年分を保存しました。左の「PL・業績予想」で確認できます。")
        else:
            st.warning("保存に必要な売上高・営業利益・純利益・平均株式数が揃った本決算データがありませんでした。")
    links = st.session_state.get("ir_pdf_links", [])
    selected_url = st.selectbox("取得する決算資料PDF", links, index=None, placeholder="公式IR資料を検索すると候補を表示します") if links else None
    source_name, source_url, content = "", "", None
    if selected_url and st.button("PDFを取得・解析"):
        try:
            content, metadata = download_pdf(selected_url, refresh=bool(st.session_state.pop("ir_refresh", False)))
            source_name, source_url = metadata["source_name"], metadata["source_url"]
            st.session_state.ir_pdf = {"content": content, "source_name": source_name, "source_url": source_url, "retrieved_at": metadata["retrieved_at"]}
        except DataSourceError as exc:
            st.error(str(exc))
    uploaded = st.file_uploader("決算短信PDFをアップロード", type=["pdf"], key="ir_pdf_upload")
    if uploaded:
        st.session_state.ir_pdf = {"content": uploaded.getvalue(), "source_name": uploaded.name, "source_url": "アップロード", "retrieved_at": pd.Timestamp.now().isoformat()}
    document = st.session_state.get("ir_pdf")
    if not document:
        return
    pages = extract_pdf_pages(document["content"])
    if not any(pages):
        st.warning("PDFからテキストを抽出できませんでした（画像PDFの可能性があります）。数値を手入力してください。")
        return
    records = parse_pl_text(pages, document["source_name"], document["source_url"], document["retrieved_at"])
    if not records:
        st.warning("連結PLの主要項目を安全に抽出できませんでした。抽出テキストを確認し、手入力してください。")
        return
    frame = pd.DataFrame([record.as_dict() for record in records])
    st.caption("抽出候補です。連結を優先し、四半期単独値と根拠不明な数値は反映しません。")
    editable = st.data_editor(frame, hide_index=True, width="stretch", key="ir_pl_editor")
    years = sorted(editable["fiscal_year"].dropna().unique())
    horizontal = pd.DataFrame({"項目": ["売上高", "売上総利益", "営業利益", "経常利益", "税引前利益", "純利益", "EPS"]})
    keys = ["sales", "gross_profit", "operating_profit", "ordinary_profit", "pretax_profit", "net_income", "eps"]
    for year in years:
        row = editable[editable["fiscal_year"] == year].iloc[-1]
        horizontal[str(year)] = [row.get(key) for key in keys]
    st.dataframe(horizontal, hide_index=True, width="stretch")
    st.download_button("PLをCSVでダウンロード", pl_to_csv(horizontal), "pl_summary.csv", "text/csv")
    try:
        st.download_button("PLをExcelでダウンロード", pl_to_excel(horizontal), "pl_summary.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    except ImportError:
        st.info("Excel出力にはopenpyxlのインストールが必要です。CSVは利用できます。")
    st.dataframe(editable[["fiscal_year", "source_name", "source_url", "page", "scope", "unit", "retrieved_at"]], hide_index=True)
    if st.checkbox("確認した抽出値をPL画面へ反映", key="ir_apply_confirm"):
        st.session_state.imported_pl_candidates = editable.to_dict("records")
        st.success("PL候補を保持しました。PL・業績予想画面で平均株式数を確認して保存してください。")


def simple_company_data_panel(project: dict | None) -> None:
    """銘柄コードまたは会社名から企業・株価・本決算PLをまとめて取得する入口。"""
    st.subheader("企業・決算データ取得")
    st.caption("4桁の銘柄コードまたは会社名を入力してください。会社名が曖昧な場合は近い候補を表示します。")
    current = project or {}
    api_key = get_jquants_api_key()
    pending_code = st.session_state.pop("simple_selected_company_code", "")
    feedback = st.session_state.pop("simple_lookup_feedback", None)
    if feedback:
        getattr(st, feedback[0])(feedback[1])

    with st.form("simple_company_lookup_form"):
        code = st.text_input(
            "銘柄コード（4桁）または会社名",
            value=pending_code or current.get("stock_code", ""),
            key="simple_fetch_code",
            placeholder="例：7713、シグマ光機、ELEMENTS",
        )
        submitted = st.form_submit_button("この銘柄を開く・取得", type="primary")
    submitted = submitted or bool(pending_code)

    if not api_key:
        st.info("J-Quants未設定でも、保存済みデータと無料のJPX企業マスターを利用できます。株価など一部の自動取得だけ利用できない場合があります。")

    if submitted:
        normalized_code = ""
        try:
            normalized_code = display_code(code.strip())
            st.session_state.pop("simple_company_candidates", None)
        except ValueError:
            try:
                master_rows, _ = get_master()
                matches = search_master(master_rows, code.strip())
            except JPXMasterError as exc:
                st.error(f"企業検索を利用できませんでした：{exc} 手入力機能は引き続き使えます。")
                matches = []
            if len(matches) == 1:
                normalized_code = str(matches[0]["stock_code"])
                st.session_state.pop("simple_company_candidates", None)
            elif matches:
                st.session_state.simple_company_candidates = matches[:15]
                st.info("近い会社が複数見つかりました。下の候補から選んでください。")
            else:
                st.warning("該当する会社が見つかりませんでした。会社名の一部または4桁コードで再検索してください。")
        if normalized_code:
            existing = find_project_by_code(list_projects(), normalized_code)
            if existing:
                existing_id = int(existing["id"])
                set_active_project(existing_id)
                with st.spinner("会社概要と投資の確認ポイントを整えています…"):
                    enrich_project_profile(existing_id, existing)
                st.session_state.pop("simple_company_preview", None)
                st.session_state[f"auto_financial_fetch_{existing_id}"] = True
                st.session_state.simple_lookup_feedback = (
                    "success",
                    f"{existing.get('company_name') or existing.get('project_name') or normalized_code}（{normalized_code}）を開きました。PL・業績予想を選ぶと、PL・セグメントの取得候補を表示します。",
                )
                st.rerun()

            if api_key:
                try:
                    candidates = cached_company_search(api_key, normalized_code, 0)
                    exact = next(
                        (row for row in candidates if row["stock_code"] == normalized_code),
                        candidates[0] if candidates else None,
                    )
                    if exact is None:
                        st.warning("該当する企業が見つかりませんでした。")
                    else:
                        st.session_state.simple_company_preview = cached_company_data(api_key, exact, 0)
                except (JQuantsApiError, ValueError) as exc:
                    st.error(f"データを取得できませんでした：{exc}")
            else:
                company_name = ""
                master_warning = ""
                try:
                    master_rows, _ = get_master()
                    matches = search_master(master_rows, normalized_code)
                    if matches:
                        company_name = matches[0].get("company_name", "")
                except JPXMasterError as exc:
                    master_warning = str(exc)

                project_id = save_project({
                    "project_name": company_name or f"銘柄 {normalized_code}",
                    "company_name": company_name,
                    "stock_code": normalized_code,
                    "ir_url": VERIFIED_IR_SOURCES.get(normalized_code, ""),
                })
                with st.spinner("会社概要と投資の確認ポイントを整えています…"):
                    enrich_project_profile(project_id, get_project(project_id) or {})
                set_active_project(project_id)
                st.session_state[f"auto_financial_fetch_{project_id}"] = True
                message = f"{company_name or normalized_code}のプロジェクトを作成しました。この画面のまま会社概要を確認・編集できます。PL・業績予想を選ぶと取得候補を表示します。"
                if master_warning:
                    message += " JPX企業マスターは取得できなかったため、会社名は手入力してください。"
                st.session_state.simple_lookup_feedback = ("success", message)
                st.rerun()

    candidates = st.session_state.get("simple_company_candidates") or []
    if candidates:
        with st.container(border=True):
            st.markdown("#### 会社候補")
            selected_company = st.selectbox(
                "近い候補から選択",
                candidates,
                format_func=lambda row: (
                    f"{row['stock_code']}｜{row.get('company_name', '')}｜{row.get('market', '')}"
                ),
                key="simple_company_candidate_choice",
            )
            if st.button("この会社を開く", type="primary", key="open_simple_company_candidate"):
                st.session_state.simple_selected_company_code = selected_company["stock_code"]
                st.session_state.pop("simple_company_candidates", None)
                st.session_state.pop("simple_fetch_code", None)
                st.rerun()

    preview = st.session_state.get("simple_company_preview")
    if not preview:
        return
    master = preview["master"]
    price = preview["price"]
    shares = preview.get("share_info") or {}
    calculated = preview.get("calculated") or {}
    with st.container(border=True):
        st.markdown(f"### {master['company_name']}（{master['stock_code']}）")
        metrics = metric_slots(4)
        metrics[0].metric("株価", "-" if price["close"] is None else f"{price['close']:,.0f}円")
        metrics[1].metric("株式数", "-" if shares.get("net_issued_shares") is None else f"{shares['net_issued_shares']:,.3f}百万株")
        metrics[2].metric("時価総額", "-" if calculated.get("market_cap") is None else f"{calculated['market_cap']:,.1f}億円")
        metrics[3].metric("PER", "-" if calculated.get("current_per") is None else f"{calculated['current_per']:,.1f}倍")
        pl_records = select_recent_pl_records(build_pl_import_records(preview["financials"]), 3)
        if pl_records:
            st.dataframe(pd.DataFrame(pl_records)[[
                "fiscal_year", "sales", "operating_profit", "ordinary_profit",
                "net_income", "average_shares", "eps",
            ]].rename(columns={
                "fiscal_year": "年度", "sales": "売上高", "operating_profit": "営業利益",
                "ordinary_profit": "経常利益", "net_income": "純利益",
                "average_shares": "平均株式数", "eps": "EPS",
            }), hide_index=True)
        apply_company = st.checkbox("企業情報・株価・株式数を反映", value=True, key="simple_apply_company")
        apply_pl = st.checkbox("本決算PLを反映", value=True, key="simple_apply_pl")
        if st.button("確認したデータを反映", type="primary"):
            updated = dict(current)
            if apply_company:
                updated.update({
                    "company_name": master["company_name"], "stock_code": master["stock_code"],
                    "company_name_en": master.get("company_name_en", ""), "market": master.get("market", ""),
                    "sector17": master.get("sector17", ""), "sector33": master.get("sector33", ""),
                    "current_price": price["close"] or updated.get("current_price", 0),
                    "price_date": price.get("date") or "", "data_retrieved_at": price.get("retrieved_at") or "",
                    "shares_outstanding": shares.get("net_issued_shares") or updated.get("shares_outstanding", 0),
                    "shares_source": shares.get("shares_source", ""), "bps": shares.get("bps"),
                    "annual_dividend": shares.get("annual_dividend"), "price_source": "J-Quants",
                    "price_input_method": "自動取得",
                })
            updated["id"] = current.get("id")
            updated["project_name"] = updated.get("project_name") or master["company_name"]
            project_id = save_project(updated)
            if apply_pl:
                save_structured_pl(project_id, select_recent_pl_records(pl_records, 3))
            with st.spinner("会社概要と投資の確認ポイントを整えています…"):
                enrich_project_profile(project_id, get_project(project_id) or updated)
            set_active_project(project_id)
            st.session_state[f"auto_financial_fetch_{project_id}"] = True
            st.success("確認したデータを反映しました。現在の画面は切り替えません。PL・業績予想を選ぶと、PL・セグメントの取得候補を表示します。")
            st.rerun()


def _uploaded_financial_result(files: list) -> dict:
    pl_by_year: dict[str, dict] = {}
    segments_by_key: dict[tuple[str, str], dict] = {}
    metrics_by_key: dict[tuple[str, str, str], dict] = {}
    warnings: list[str] = []
    documents: list[dict[str, str]] = []
    for uploaded in files:
        parsed = parse_financial_document(
            uploaded.getvalue(),
            uploaded.name,
            "アップロード",
            pd.Timestamp.now(tz="Asia/Tokyo").isoformat(),
        )
        documents.append({"title": uploaded.name, "url": "アップロード"})
        warnings.extend(parsed["warnings"])
        for row in parsed["pl_records"]:
            pl_by_year[str(row["fiscal_year"])] = row
        for row in parsed["segment_records"]:
            segments_by_key[(str(row["fiscal_year"]), str(row["segment_name"]))] = row
        for row in parsed.get("segment_metrics", []):
            key = (
                str(row["fiscal_year"]), str(row.get("result_type", "実績")),
                str(row["row_label"]),
            )
            metrics_by_key[key] = row
    return {
        "pl_records": [pl_by_year[year] for year in sorted(pl_by_year)],
        "segment_records": [segments_by_key[key] for key in sorted(segments_by_key)],
        "segment_metrics": [metrics_by_key[key] for key in sorted(metrics_by_key)],
        "warnings": warnings,
        "documents": documents,
    }


def _financial_result_needs_ai(result: dict, location: str) -> bool:
    """通常解析で対象画面の主要項目が不足しているか判定する。"""
    # Geminiは数値の不足補完だけでなく、同じ公式PDFから企業概要と
    # カタリスト根拠も抽出する。結果がなければ一度だけ（24時間キャッシュ）呼ぶ。
    if not result.get("company_profile"):
        return True
    if location == "segment":
        return not result.get("segment_records")
    actual = [row for row in result.get("pl_records", []) if row.get("result_type", "実績") == "実績"]
    if not actual:
        return True
    detail_fields = (
        "sales", "cost_of_sales", "gross_profit", "sga_expenses", "operating_profit",
        "ordinary_profit", "pretax_profit", "net_income",
    )
    return any(any(row.get(field) is None for field in detail_fields) for row in actual)


def _has_ai_extraction(result: dict) -> bool:
    return any(result.get(key) for key in (
        "pl_records", "segment_records", "segment_metrics",
        "company_profile", "catalyst_candidates",
    ))


def _uploaded_ai_result(files: list, model: str) -> dict:
    api_key = get_openai_api_key()
    if not api_key:
        raise AIFinancialParserError("OPENAI_API_KEYが設定されていません。")
    parser = OpenAIFinancialParser(api_key, model=model)
    result = {"pl_records": [], "segment_records": [], "segment_metrics": [], "warnings": [], "documents": []}
    for uploaded in files[:5]:
        parsed = parser.parse_pdf(uploaded.getvalue(), uploaded.name, "アップロード")
        result = merge_financial_previews(result, parsed)
    return result


def _uploaded_gemini_result(files: list, model: str) -> dict:
    api_key = get_gemini_api_key()
    if not api_key:
        raise GeminiFinancialParserError("GEMINI_API_KEYが設定されていません。")
    parser = GeminiFinancialParser(api_key, model=model)
    result = {"pl_records": [], "segment_records": [], "segment_metrics": [], "warnings": [], "documents": []}
    for uploaded in files[:5]:
        parsed = parser.parse_pdf(uploaded.getvalue(), uploaded.name, "アップロード")
        result = merge_financial_previews(result, parsed)
    return result


def _apply_financial_result(project_id: int, result: dict, location: str) -> int:
    """取得結果のうち、開いている画面の表だけをSQLiteへ反映する。"""
    project = get_project(project_id) or {"id": project_id}
    profile = result.get("company_profile") or {}
    auto_prefixes = ("確認候補：", "検討候補：", "検討仮説：", "候補：", "主な確認リスク：", "Gemini要約", "Gemini分析候補")
    changed = False
    for field in ("business_description", "strengths", "investment_thesis", "catalysts", "risks"):
        current_value = str(project.get(field) or "").strip()
        new_value = str(profile.get(field) or "").strip()
        if new_value and (not current_value or current_value.startswith(auto_prefixes)):
            project[field] = new_value
            changed = True
    candidates = result.get("catalyst_candidates") or []
    if candidates and (not str(project.get("catalysts") or "").strip() or str(project.get("catalysts") or "").startswith(auto_prefixes)):
        lines = []
        for row in candidates[:5]:
            impact = f"／会社への影響：{row.get('company_impact')}" if row.get("company_impact") else ""
            lines.append(
                f"・{row.get('name')}：{row.get('rationale')}{impact}"
                f"（確度：{row.get('confidence', '低')}、出典：{row.get('source', '')}）"
            )
        project["catalysts"] = "Gemini分析候補（要確認）\n" + "\n".join(lines)
        changed = True
    if changed:
        project["id"] = project_id
        save_project(project)
    if location == "pl":
        records = select_recent_pl_records(result.get("pl_records", []), 3)
        if not records:
            return 0
        save_pl_entries(project_id, merge_imported_pl(get_pl_entries(project_id), records))
        st.session_state.pop(f"pl_years_state_{project_id}", None)
        clear_state_prefix(f"pl_matrix_simple_{project_id}")
        clear_state_prefix(f"pl_matrix_mobile_{project_id}")
        return len(records)

    records = result.get("segment_records", [])
    metrics = result.get("segment_metrics", [])
    if not records and not metrics:
        return 0
    if records:
        save_segment_entries(
            project_id,
            merge_imported_segments(get_segment_entries(project_id), records),
        )
    if metrics:
        save_segment_metrics(
            project_id,
            merge_imported_segment_metrics(get_segment_metrics(project_id), metrics),
        )
    st.session_state.pop(f"segment_years_state_{project_id}", None)
    st.session_state.pop(f"segment_names_state_{project_id}", None)
    st.session_state.pop(f"segment_metrics_editor_{project_id}", None)
    clear_state_prefix(f"segment_sales_simple_{project_id}")
    clear_state_prefix(f"segment_profit_simple_{project_id}")
    clear_state_prefix(f"segment_combined_simple_{project_id}")
    clear_state_prefix(f"segment_combined_mobile_{project_id}")
    st.session_state[f"segment_return_to_display_{project_id}"] = True
    return len(records) + len(metrics)


def financial_document_import_panel(project: dict, location: str) -> None:
    """PLまたはセグメントを、画面ごとの1回の操作で取得・反映する。"""
    project_id = int(project["id"])
    preview_key = f"financial_document_preview_{project_id}"
    source_key = f"financial_document_source_{project_id}"
    upload_signature_key = f"financial_document_upload_signature_{location}_{project_id}"
    stock_code = str(project.get("stock_code", "")).strip()
    company_name = str(project.get("company_name") or project.get("project_name") or "").strip()
    saved_url = str(project.get("ir_url") or "").strip()

    target_label = "PL" if location == "pl" else "セグメント"
    notice_key = f"financial_import_notice_{location}_{project_id}"
    notice = st.session_state.pop(notice_key, None)
    if notice:
        st.success(notice, icon=":material/check_circle:")

    with st.container(border=True):
        heading, action = responsive_columns([4, 1], vertical_alignment="center")
        heading.markdown(f"#### 決算資料から{target_label}を自動入力")
        heading.caption("銘柄コードから公式IRを探し、決算短信と決算説明資料の連結データを表へ直接反映します。")
        source_info = st.session_state.get(source_key)
        source_url = (source_info or {}).get("url") or saved_url
        if source_url:
            source_method = (source_info or {}).get("method", "保存済み公式IR")
            heading.caption(f"取得先：{company_name or stock_code} 公式IR（{source_method}）")
        gemini_available = bool(get_gemini_api_key())
        openai_available = bool(get_openai_api_key())
        heading.caption("無料解析：決算短信の文字・罫線・表レイアウトを組み合わせて抽出します。")
        heading.caption(
            "Gemini無料補助：有効（通常解析の空欄だけ自動補完）"
            if gemini_available else
            "Gemini無料補助：未設定（設定後は不足項目だけ自動補完）"
        )
        heading.caption(
            "EDINET XBRL：有効（無料）"
            if get_edinet_api_key() else
            "EDINET XBRL：未設定（無料キーを設定するとPL・セグメント精度が上がります）"
        )
        use_paid_ai = False
        with heading.popover("その他", icon=":material/settings:"):
            refresh_source = st.checkbox(
                "取得先とキャッシュを更新する",
                key=f"document_refresh_{location}_{project_id}",
            )
            st.caption("最新資料が出た直後など、通常取得で更新されない時だけオンにします。")
            uploaded = st.file_uploader(
                "自動取得できない場合：本決算短信PDF（複数可）",
                type=["pdf"],
                accept_multiple_files=True,
                key=f"document_upload_{location}_{project_id}",
            )
            if openai_available:
                use_paid_ai = st.checkbox(
                    "有料のOpenAI補助を使う",
                    value=False,
                    key=f"use_paid_ai_{location}_{project_id}",
                    help="通常はオフのままで無料です。オンにした取得だけAPI料金が発生します。",
                )
            else:
                st.caption("OpenAI有料補助は未設定です。無料解析とGeminiだけで動作します。")
        fetch_clicked = action.button(
            f"{target_label}情報を取得",
            type="primary",
            icon=":material/travel_explore:",
            key=f"document_fetch_{location}_{project_id}",
            width="stretch",
        )
        auto_fetch = bool(st.session_state.pop(f"auto_financial_fetch_{project_id}", False))
        if fetch_clicked or auto_fetch:
            try:
                nonce = time.time_ns() if refresh_source else 0
                with st.spinner("企業公式IRを探し、最新の本決算短信を取得・解析しています…"):
                    source_info = cached_ir_source(stock_code, company_name, saved_url, nonce)
                    st.session_state[source_key] = source_info
                    result = cached_official_financials(source_info["url"], 3, nonce)
                    if get_edinet_api_key():
                        try:
                            edinet_result = cached_edinet_financials(stock_code, 3, nonce)
                            # 標準化されたXBRLを基準にし、短信PDFで詳細行・KPIを補う。
                            result = merge_financial_previews(edinet_result, result)
                        except (EDINETError, EDINETMasterError, ValueError, OSError) as edinet_exc:
                            result.setdefault("warnings", []).append(f"EDINET補完を完了できませんでした：{edinet_exc}")
                    if get_jquants_api_key():
                        try:
                            result = merge_financial_previews(
                                result,
                                jquants_financial_fallback(stock_code, nonce),
                            )
                        except (DataSourceError, JQuantsApiError, ValueError):
                            pass
                    if not result.get("pl_records") and get_jquants_api_key():
                        # 決算説明資料からセグメントだけ取得できた場合でも、
                        # J-QuantsのPL補完でセグメントを消さない。
                        result = merge_financial_previews(
                            result,
                            jquants_financial_fallback(stock_code, nonce),
                        )
                    if gemini_available and _financial_result_needs_ai(result, location):
                        try:
                            gemini_result = cached_gemini_official_financials(
                                source_info["url"], get_gemini_financial_model(), 3, nonce,
                            )
                            result = merge_financial_previews(result, gemini_result)
                            if _has_ai_extraction(gemini_result):
                                result.setdefault("warnings", []).append(
                                    "Geminiが公式資料から根拠付きの不足項目を補完しました。出典ページを確認してください。"
                                )
                        except GeminiFinancialParserError as gemini_exc:
                            result.setdefault("warnings", []).append(str(gemini_exc))
                    if use_paid_ai and _financial_result_needs_ai(result, location):
                        try:
                            ai_result = cached_ai_official_financials(
                                source_info["url"], get_openai_financial_model(), 3, nonce,
                            )
                            result = merge_financial_previews(result, ai_result)
                            result.setdefault("warnings", []).append(
                                "通常解析で不足した項目をAIで補助抽出しました。出典ページを確認してください。"
                            )
                        except AIFinancialParserError as ai_exc:
                            result.setdefault("warnings", []).append(str(ai_exc))
                    st.session_state[preview_key] = result
                save_project({"id": project_id, "ir_url": source_info["url"]})
                count = _apply_financial_result(project_id, result, location)
                if count:
                    if location == "pl":
                        displayed = select_recent_pl_records(result.get("pl_records", []), 3)
                        actual_count = sum(
                            str(row.get("result_type", "実績")) == "実績" for row in displayed
                        )
                        forecast_count = len(displayed) - actual_count
                        forecast_text = f"、会社予想を{forecast_count}年度" if forecast_count else ""
                        st.session_state[notice_key] = (
                            f"PL実績を{actual_count}年度{forecast_text}取得し、下の表へ反映しました。"
                        )
                    else:
                        st.session_state[notice_key] = f"{target_label}を{count}行取得し、下の表へ反映しました。"
                    st.rerun()
                if location == "segment" and result.get("pl_records"):
                    st.warning("PLは取得できましたが、この資料からセグメント数値を安全に抽出できませんでした。")
                else:
                    st.warning(f"{target_label}を資料から抽出できませんでした。下の表へ手入力できます。")
            except (IRSourceDiscoveryError, DataSourceError, ValueError, OSError) as exc:
                try:
                    if get_edinet_api_key():
                        with st.spinner("無料のEDINET XBRLへ切り替えています…"):
                            result = cached_edinet_financials(
                                stock_code, 3, time.time_ns() if refresh_source else 0,
                            )
                        count = _apply_financial_result(project_id, result, location)
                        if count:
                            st.session_state[preview_key] = result
                            st.session_state[notice_key] = f"EDINETから{target_label}を取得し、下の表へ反映しました。"
                            st.rerun()
                    if location != "pl":
                        raise ValueError("公式IRからセグメントを取得できませんでした。")
                    with st.spinner("J-QuantsのPLへ切り替えています…"):
                        result = jquants_financial_fallback(stock_code, time.time_ns() if refresh_source else 0)
                        st.session_state[preview_key] = result
                    count = _apply_financial_result(project_id, result, location)
                    if count:
                        displayed = select_recent_pl_records(result.get("pl_records", []), 3)
                        actual_count = sum(
                            str(row.get("result_type", "実績")) == "実績" for row in displayed
                        )
                        forecast_count = len(displayed) - actual_count
                        forecast_text = f"、会社予想を{forecast_count}年度" if forecast_count else ""
                        st.session_state[notice_key] = (
                            f"J-QuantsからPL実績を{actual_count}年度{forecast_text}取得し、下の表へ反映しました。"
                        )
                        st.rerun()
                    st.warning("公式IRとJ-QuantsのどちらからもPLを取得できませんでした。")
                except (DataSourceError, JQuantsApiError, EDINETError, EDINETMasterError, ValueError, OSError) as fallback_exc:
                    st.error(f"自動取得できませんでした：{fallback_exc}")

        upload_signature = tuple((item.name, item.size) for item in uploaded)
        if uploaded and upload_signature != st.session_state.get(upload_signature_key):
            with st.spinner("アップロードしたPDFを解析しています…"):
                result = _uploaded_financial_result(uploaded)
                if gemini_available and _financial_result_needs_ai(result, location):
                    try:
                        gemini_result = _uploaded_gemini_result(uploaded, get_gemini_financial_model())
                        result = merge_financial_previews(result, gemini_result)
                        if _has_ai_extraction(gemini_result):
                            result.setdefault("warnings", []).append(
                                "GeminiがPDFから根拠付きの不足項目を補完しました。出典ページを確認してください。"
                            )
                    except GeminiFinancialParserError as gemini_exc:
                        result.setdefault("warnings", []).append(str(gemini_exc))
                if use_paid_ai and _financial_result_needs_ai(result, location):
                    try:
                        result = merge_financial_previews(
                            result,
                            _uploaded_ai_result(uploaded, get_openai_financial_model()),
                        )
                        result.setdefault("warnings", []).append(
                            "通常解析で不足した項目をAIで補助抽出しました。出典ページを確認してください。"
                        )
                    except AIFinancialParserError as ai_exc:
                        result.setdefault("warnings", []).append(str(ai_exc))
                st.session_state[preview_key] = result
                st.session_state[upload_signature_key] = upload_signature
            count = _apply_financial_result(project_id, result, location)
            if count:
                unit = "年度" if location == "pl" else "行"
                st.session_state[notice_key] = f"PDFから{target_label}を{count}{unit}取得し、下の表へ反映しました。"
                st.rerun()
            st.warning(f"PDFから{target_label}を安全に抽出できませんでした。下の表へ手入力できます。")

        preview = st.session_state.get(preview_key)
        if preview:
            for warning in preview.get("warnings", []):
                st.caption(f"・{warning}")
        if preview and preview.get("documents"):
            st.caption("出典：" + " ｜ ".join(row["title"] for row in preview["documents"]))
        st.caption("無料解析でも資料にない項目は推測せず空欄にします。出典資料とページを確認してください。")


def project_page() -> None:
    st.title("銘柄プロジェクト")
    project_id = st.session_state.get("project_id")
    project = get_project(project_id) if project_id else None
    simple_company_data_panel(project)
    st.divider()
    with st.form("project_form"):
        c1, c2, c3 = responsive_columns(3)
        form_suffix = str(project_id or "new")
        name = c1.text_input("プロジェクト名 *", value=project.get("project_name", "") if project else "", key=f"project_form_name_{form_suffix}")
        company = c2.text_input("会社名", value=project.get("company_name", "") if project else "", key=f"project_form_company_{form_suffix}")
        code = c3.text_input("銘柄コード", value=project.get("stock_code", "") if project else "", key=f"project_form_code_{form_suffix}")
        c4, c5 = responsive_columns(2)
        price = c4.number_input("現在株価（円）", min_value=0.0, value=number(project.get("current_price")) if project else 0.0, step=10.0, key=f"project_form_price_{form_suffix}")
        shares = c5.number_input("発行済株式数（百万株）", min_value=0.0, value=number(project.get("shares_outstanding")) if project else 0.0, step=0.1, format="%.2f", key=f"project_form_shares_{form_suffix}")
        pl_entries = get_pl_entries(project_id) if project_id else []
        latest_eps = None
        for latest in reversed(pl_entries):
            net_income = latest.get("net_income")
            average_shares = latest.get("average_shares") or latest.get("shares_outstanding")
            if net_income is not None and average_shares is not None and number(average_shares) > 0:
                latest_eps = eps(number(net_income), number(average_shares))
                break
            if latest.get("reported_eps") is not None:
                latest_eps = number(latest.get("reported_eps"))
                break
        valuation = valuation_snapshot(
            price,
            shares,
            latest_eps,
            project.get("bps") if project else None,
            project.get("annual_dividend") if project else None,
        )
        if is_mobile_view():
            cards = metric_slots(5)
        else:
            # 5枚を1列に詰めず、3枚＋2枚に分けて数値を省略させない。
            cards = [*st.columns(3), *st.columns(2)]
        values = [
            "―" if valuation["price"] is None else f"{valuation['price']:,.0f} 円",
            "―" if valuation["market_cap"] is None else f"{valuation['market_cap']:,.1f} 億円",
            "―" if valuation["per"] is None else ("赤字" if valuation["per"] <= 0 else f"{valuation['per']:,.1f} 倍"),
            "―" if valuation["pbr"] is None else f"{valuation['pbr']:,.2f} 倍",
            "―" if valuation["dividend_yield"] is None else f"{valuation['dividend_yield']:,.2f} %",
        ]
        for column, label, value in zip(cards, ["株価", "時価総額", "PER", "PBR", "配当利回り"], values):
            column.metric(label, value, border=True)
        if project:
            price_date = str(project.get("price_date") or "未設定")
            price_source = str(project.get("price_source") or "手入力")
            shares_source = str(project.get("shares_source") or "手入力")
            st.caption(
                f"株価基準日：{price_date} ｜ 株価出典：{price_source} ｜ "
                f"株式数：{shares_source}"
            )
            if price_date != "未設定":
                try:
                    age_days = (pd.Timestamp.now(tz="Asia/Tokyo").date() - pd.Timestamp(price_date).date()).days
                    if age_days > 7:
                        st.warning("株価基準日が7日以上前です。現在値ではなく、取得可能な直近終値として確認してください。")
                except (TypeError, ValueError):
                    pass
        if project and project.get("ir_url"):
            st.caption("決算短信の取得先は銘柄コードから自動設定済みです。PL・セグメント画面で再取得できます。")
        business = st.text_area("事業内容", value=project.get("business_description", "") if project else "", height=90, key=f"project_form_business_{form_suffix}")
        text_left, text_right = responsive_columns(2)
        strengths = text_left.text_area("会社の強み", value=project.get("strengths", "") if project else "", height=100, key=f"project_form_strengths_{form_suffix}")
        thesis = text_right.text_area("投資仮説", value=project.get("investment_thesis", "") if project else "", height=100, key=f"project_form_thesis_{form_suffix}")
        catalyst_col, risk_col = responsive_columns(2)
        catalysts = catalyst_col.text_area("主なカタリスト", value=project.get("catalysts", "") if project else "", height=100, key=f"project_form_catalysts_{form_suffix}")
        risks = risk_col.text_area("主なリスク", value=project.get("risks", "") if project else "", height=100, key=f"project_form_risks_{form_suffix}")
        submitted = st.form_submit_button("保存", type="primary")
    if submitted:
        data = {"id": project_id, "project_name": name, "company_name": company, "stock_code": code, "current_price": price,
                "shares_outstanding": shares, "business_description": business, "strengths": strengths,
                "investment_thesis": thesis, "catalysts": catalysts, "risks": risks}
        errors = validate_project(data)
        if errors:
            st.error("\n".join(errors))
        else:
            set_active_project(save_project(data))
            st.success("プロジェクトを保存しました。")
            st.rerun()
    if project_id and st.button("このプロジェクトを削除"):
        delete_project(project_id)
        set_active_project(None)
        st.rerun()


def pl_page() -> None:
    st.title("PL・業績予想")
    project_id = require_project()
    if project_id is None:
        return
    project = get_project(project_id) or {}
    mobile = is_mobile_view()
    company_label = project.get("company_name") or project.get("project_name") or ""
    stock_code = project.get("stock_code") or "コード未設定"
    st.caption(f"{company_label}（{stock_code}）｜ 金額：百万円 ｜ 平均株式数：百万株 ｜ 比率：％ ｜ EPS：円")
    stored_entries = get_pl_entries(project_id)
    financial_document_import_panel(project, "pl")

    year_state_key = f"pl_years_state_{project_id}"
    if year_state_key not in st.session_state:
        st.session_state[year_state_key] = normalize_metadata(metadata_from_entries(stored_entries))
    metadata = st.session_state[year_state_key]
    mode_key = f"pl_view_mode_{project_id}"
    if st.session_state.get(f"auto_financial_fetch_{project_id}"):
        # 銘柄入力直後は取得パネルを自動で開き、追加クリックを不要にする。
        st.session_state[mode_key] = "数値を編集"
    if st.session_state.pop(f"pl_return_to_display_{project_id}", False):
        st.session_state[mode_key] = "完成表"
    mode = st.segmented_control(
        "PLの表示モード",
        ["完成表", "数値を編集"],
        default="完成表",
        required=True,
        key=mode_key,
        width="stretch",
    )

    if mode == "数値を編集":
        with st.expander("年度と区分を編集", icon=":material/view_column:"):
            st.caption("列を追加し、実績・会社予想・自分予想の区分を設定します。")
            add_year, add_type, add_action = responsive_columns([2, 2, 1], vertical_alignment="bottom")
            new_year = add_year.text_input("年度", placeholder="例：2027.3", key=f"pl_new_year_{project_id}")
            new_type = add_type.selectbox("区分", RESULT_TYPES, key=f"pl_new_type_{project_id}")
            if add_action.button("追加", icon=":material/add:", key=f"pl_add_year_{project_id}"):
                year = new_year.strip()
                if not year:
                    st.warning("年度を入力してください。")
                elif any(row["fiscal_year"] == year and row["result_type"] == new_type for row in metadata):
                    st.warning("同じ年度・区分はすでにあります。")
                else:
                    st.session_state[year_state_key] = [*metadata, {"fiscal_year": year, "result_type": new_type}]
                    clear_state_prefix(f"pl_matrix_simple_{project_id}")
                    clear_state_prefix(f"pl_matrix_mobile_{project_id}")
                    st.rerun()
            if metadata:
                st.caption("現在の列：" + " ｜ ".join(f"{row['fiscal_year']}（{row['result_type']}）" for row in metadata))
                remove_year, remove_action = responsive_columns([4, 1], vertical_alignment="bottom")
                remove_options = [f"{row['fiscal_year']}｜{row['result_type']}" for row in metadata]
                remove_target = remove_year.selectbox(
                    "削除する列",
                    remove_options,
                    key=f"pl_remove_target_{project_id}",
                )
                if remove_action.button("削除", icon=":material/delete:", key=f"pl_remove_year_{project_id}"):
                    st.session_state[year_state_key] = [
                        row for row in metadata
                        if f"{row['fiscal_year']}｜{row['result_type']}" != remove_target
                    ]
                    clear_state_prefix(f"pl_matrix_simple_{project_id}")
                    clear_state_prefix(f"pl_matrix_mobile_{project_id}")
                    st.rerun()

    if not metadata:
        st.subheader("PL")
        rows = ["売上高", "売上原価", "売上総利益", "販管費", "営業利益", "経常利益", "税引前利益", "純利益", "EPS"]
        st.dataframe(
            pd.DataFrame({
                "科目": rows,
                "実績（未取得）": [None] * len(rows),
                "会社予想（未開示）": [None] * len(rows),
                "自分予想（未入力）": [None] * len(rows),
            }),
            hide_index=True,
            column_config={"科目": st.column_config.TextColumn("科目", pinned=True)},
        )
        st.info("表は準備済みです。「数値を編集」から年度を追加すると入力・保存できます。会社予想の未開示項目は空欄のまま扱います。", icon=":material/info:")
        return

    by_key = {
        (str(row.get("fiscal_year")), str(row.get("result_type", "実績"))): row
        for row in stored_entries
    }
    display_records = [
        {
            **by_key.get((meta["fiscal_year"], meta["result_type"]), {}),
            "fiscal_year": meta["fiscal_year"],
            "result_type": meta["result_type"],
        }
        for meta in metadata
    ]

    if mode == "完成表":
        calculated = calculate_pl_records(display_records)
        populated = [row for row in calculated if row.get("sales") is not None]
        if populated:
            latest = populated[-1]
            kpi = metric_slots(4)
            sales_delta = None if latest.get("sales_growth") is None else f"{latest['sales_growth']:+.1f}% 前年比"
            kpi[0].metric("売上高（百万円）", f"{latest['sales']:,.0f}", sales_delta)
            kpi[1].metric(
                "営業利益（百万円）",
                "-" if latest.get("operating_profit") is None else f"{latest['operating_profit']:,.0f}",
                None if latest.get("operating_margin") is None else f"利益率 {latest['operating_margin']:.1f}%",
            )
            kpi[2].metric(
                "純利益（百万円）",
                "-" if latest.get("net_income") is None else f"{latest['net_income']:,.0f}",
                None if latest.get("net_margin") is None else f"利益率 {latest['net_margin']:.1f}%",
            )
            kpi[3].metric("EPS（円）", "-" if latest.get("eps") is None else f"{latest['eps']:,.2f}")

        st.subheader("PL")
        st.markdown(":red-badge[実績]  :orange-badge[会社予想]  :blue-badge[自分予想]")
        if mobile:
            st.caption("表示する年度を選ぶと、科目を縦にして見やすく表示します。")
        else:
            st.caption("太字は主要利益、青字は利益率・前年比です。年度を横、科目を縦に表示しています。")
        completed_frame = pl_display_frame(display_records)
        completed_metadata = metadata
        if mobile:
            mobile_titles = [column_title(row["fiscal_year"], row["result_type"]) for row in metadata]
            selected_title = st.selectbox(
                "表示する年度",
                mobile_titles,
                index=len(mobile_titles) - 1,
                key=f"pl_mobile_display_year_{project_id}",
            )
            completed_frame = completed_frame[["科目", selected_title]]
            completed_metadata = [
                row for row in metadata
                if column_title(row["fiscal_year"], row["result_type"]) == selected_title
            ]
        st.dataframe(
            color_columns(completed_frame, completed_metadata),
            hide_index=True,
            # ヘッダー分だけ余白を確保し、末尾に空欄行を表示しない。
            height=min(900, 34 * (len(completed_frame) + 1)),
            column_config={"科目": st.column_config.TextColumn("科目", pinned=True)},
        )

        scenarios = get_scenarios(project_id)
        if scenarios:
            current_price = number(project.get("current_price"))
            summary_rows = []
            for row in scenarios:
                result = scenario_calculation(
                    number(row.get("base_sales")), number(row.get("existing_sales_growth_rate")),
                    number(row.get("catalyst_sales")), number(row.get("operating_margin_rate")),
                    number(row.get("effective_tax_rate")), number(row.get("shares_outstanding")),
                    number(row.get("per")),
                )
                target = result.get("target_price")
                summary_rows.append({
                    "シナリオ": row.get("scenario_name", ""),
                    "予想EPS（円）": result.get("eps"),
                    "想定PER（倍）": row.get("per"),
                    "目標株価（円）": target,
                    "上昇余地（％）": None if not target or current_price <= 0 else (target / current_price - 1) * 100,
                })
            st.subheader("目標株価")
            st.dataframe(
                pd.DataFrame(summary_rows),
                hide_index=True,
                column_config={
                    "予想EPS（円）": st.column_config.NumberColumn(format="%,.2f"),
                    "想定PER（倍）": st.column_config.NumberColumn(format="%,.1f"),
                    "目標株価（円）": st.column_config.NumberColumn(format="%,.0f"),
                    "上昇余地（％）": st.column_config.NumberColumn(format="%+.1f%%"),
                },
            )
        return

    st.subheader("PL数値を編集")
    st.caption("白い数値セルを編集します。会社予想の未開示項目は空欄のまま保存できます。空欄は0に変換しません。")
    matrix_source = pl_input_frame(stored_entries, metadata)
    matrix_columns = [column for column in matrix_source.columns if column != "科目"]
    matrix_config = {"科目": st.column_config.TextColumn("科目", pinned=True)}
    matrix_config.update({column: st.column_config.NumberColumn(column, format="%,.2f") for column in matrix_columns})
    if mobile:
        selected_column = st.selectbox(
            "編集する年度",
            matrix_columns,
            index=len(matrix_columns) - 1,
            key=f"pl_mobile_edit_year_{project_id}",
        )
        selected_index = matrix_columns.index(selected_column)
        mobile_source = matrix_source[["科目", selected_column]].rename(columns={selected_column: "金額・株式数"})
        mobile_edited = st.data_editor(
            mobile_source,
            hide_index=True,
            disabled=["科目"],
            column_config={
                "科目": st.column_config.TextColumn("科目", pinned=True),
                "金額・株式数": st.column_config.NumberColumn("入力値", format="%,.2f"),
            },
            key=f"pl_matrix_mobile_{project_id}_{selected_index}",
            height=560,
        )
        matrix_edited = matrix_source.copy()
        matrix_edited[selected_column] = mobile_edited["金額・株式数"]
    else:
        matrix_edited = st.data_editor(
            matrix_source,
            hide_index=True,
            disabled=["科目"],
            column_config=matrix_config,
            key=f"pl_matrix_simple_{project_id}",
            height=560,
        )
    records = pl_records_from_frame(matrix_edited, metadata)
    complete_rows = [
        row for row in records
        if all(row.get(key) not in (None, "") for key in ("sales", "operating_profit", "net_income", "shares_outstanding"))
    ]
    warning_rows = [
        {"年度": row["fiscal_year"], "実績／会社予想／自分予想": row["result_type"],
         "売上高（百万円）": row["sales"], "営業利益（百万円）": row["operating_profit"],
         "純利益（百万円）": row["net_income"], "平均株式数（百万株）": row["shares_outstanding"]}
        for row in complete_rows
    ]
    show_warnings(pl_warnings(warning_rows))
    if st.button("変更を保存", type="primary", icon=":material/save:"):
        save_pl_entries(project_id, records)
        st.toast("PL・業績予想を保存しました。", icon=":material/check_circle:")
        st.session_state[f"pl_return_to_display_{project_id}"] = True
        st.rerun()


def catalyst_page() -> None:
    st.title("カタリスト計算")
    project_id = require_project()
    if project_id is None:
        return
    stored = get_catalyst(project_id) or {}
    project = get_project(project_id) or {}
    pl_entries = get_pl_entries(project_id)
    segment_entries = get_segment_entries(project_id)

    actual_rows = [row for row in pl_entries if row.get("result_type") == "実績"]
    latest_actual = actual_rows[-1] if actual_rows else {}
    latest_sales = number(latest_actual.get("sales"))
    latest_operating_profit = number(latest_actual.get("operating_profit"))
    observed_margin = latest_operating_profit / latest_sales * 100 if latest_sales > 0 else 0.0
    # 赤字企業でもカタリスト単体の採算まで赤字と決めつけず、編集可能な10％を初期値にする。
    default_margin = observed_margin if observed_margin > 0 else 10.0
    default_shares = (
        number(project.get("shares_outstanding"))
        or number(latest_actual.get("shares_outstanding"))
    )
    current_price = number(project.get("current_price"))
    latest_eps = eps(number(latest_actual.get("net_income")), default_shares) if default_shares else None
    default_per = current_price / latest_eps if current_price > 0 and latest_eps and latest_eps > 0 else 15.0

    category_default = stored.get("evidence_category", "自分の仮定")
    method_default = stored.get("calculation_method", "数量モデル")
    if category_default not in EVIDENCE_CATEGORIES:
        category_default = "自分の仮定"
    if method_default not in ("数量モデル", "売上比率モデル"):
        method_default = "数量モデル"
    widget_defaults = {
        f"cat_name_{project_id}": stored.get("catalyst_name", ""),
        f"cat_year_{project_id}": stored.get("target_year", ""),
        f"cat_desc_{project_id}": stored.get("description", ""),
        f"cat_method_{project_id}": method_default,
        f"target_count_{project_id}": number(stored.get("target_count")),
        f"target_rate_{project_id}": number(stored.get("target_rate")),
        f"capture_rate_{project_id}": number(stored.get("capture_rate")),
        f"price_{project_id}": number(stored.get("price_per_case")),
        f"cat_base_sales_{project_id}": number(stored.get("base_sales")),
        f"cat_impact_rate_{project_id}": number(stored.get("impact_rate")),
        f"cat_incremental_margin_{project_id}": number(stored.get("incremental_margin")) or default_margin,
        f"cat_tax_rate_{project_id}": number(stored.get("effective_tax_rate")) or 30.0,
        f"cat_valuation_per_{project_id}": number(stored.get("valuation_per")) or default_per,
        f"cat_category_{project_id}": category_default,
        f"cat_source_{project_id}": stored.get("source", ""),
        f"cat_note_{project_id}": stored.get("note", ""),
    }
    for key, value in widget_defaults.items():
        st.session_state.setdefault(key, value)

    company_name = str(project.get("company_name") or project.get("project_name") or "")
    sector = str(project.get("sector33") or project.get("sector17") or "")
    external_context = cached_catalyst_context(company_name, sector) if company_name else []
    suggestions = suggest_catalysts(project, pl_entries, segment_entries, external_context)
    with st.container(border=True):
        st.subheader("カタリスト候補")
        st.caption("企業周辺の公開情報、技術進化、法令・制度・業界ルールから候補を作り、株価への影響率を試算します。")
        if not suggestions:
            st.info("実績売上をPLへ保存すると、カタリスト候補と売上影響レンジを表示できます。")
        else:
            suggestion_margin = number(st.session_state[f"cat_incremental_margin_{project_id}"])
            suggestion_tax = number(st.session_state[f"cat_tax_rate_{project_id}"])
            suggestion_per = number(st.session_state[f"cat_valuation_per_{project_id}"])

            def suggestion_impact(additional_sales: float) -> float | None:
                return catalyst_price_impact(
                    additional_sales, suggestion_margin, suggestion_tax,
                    default_shares, suggestion_per, current_price,
                )["impact_percent"]

            comparison = pd.DataFrame([
                {
                    "候補": row["name"],
                    "弱気（株価％）": suggestion_impact(row["low_impact"]),
                    "標準（株価％）": suggestion_impact(row["standard_impact"]),
                    "強気（株価％）": suggestion_impact(row["high_impact"]),
                    "根拠強度": row["confidence"],
                }
                for row in suggestions
            ])
            st.dataframe(
                comparison,
                hide_index=True,
                column_config={
                    "弱気（株価％）": st.column_config.NumberColumn(format="%.1f%%"),
                    "標準（株価％）": st.column_config.NumberColumn(format="%.1f%%"),
                    "強気（株価％）": st.column_config.NumberColumn(format="%.1f%%"),
                },
            )
            selected = st.selectbox(
                "詳しく見る候補",
                suggestions,
                format_func=lambda row: row["name"],
                key=f"catalyst_suggestion_{project_id}",
            )
            st.write(selected["rationale"])
            if selected.get("source_url"):
                st.link_button(
                    f"出典を確認：{selected.get('source_title') or '公開情報'}",
                    selected["source_url"],
                    icon=":material/open_in_new:",
                )
                if selected.get("published"):
                    st.caption(f"公開日時：{selected['published']}")
            low, standard, high = metric_slots(3)
            selected_impacts = [suggestion_impact(selected[key]) for key in ("low_impact", "standard_impact", "high_impact")]
            for slot, label, value in zip((low, standard, high), ("弱気", "標準", "強気"), selected_impacts):
                slot.metric(f"{label}の株価影響", "算出不可" if value is None else f"{value:+,.1f}％")
            if any(value is None for value in selected_impacts):
                st.info("株価影響を出すには、会社概要で現在株価と株式数を入力してください。")
            with st.expander("株価影響の計算根拠"):
                st.write(f"標準ケースの追加売上：{selected['standard_impact']:,.0f}百万円")
                st.code("追加売上 × 増分営業利益率 × (1－税率) ÷ 株式数 × PER ÷ 現在株価 × 100")
            if st.button("標準ケースを入力欄へ反映", icon=":material/south:", key=f"adopt_catalyst_suggestion_{project_id}"):
                st.session_state[f"cat_name_{project_id}"] = selected["name"]
                st.session_state[f"cat_year_{project_id}"] = selected["target_year"]
                st.session_state[f"cat_desc_{project_id}"] = selected["rationale"]
                st.session_state[f"cat_method_{project_id}"] = "売上比率モデル"
                st.session_state[f"cat_base_sales_{project_id}"] = float(selected["reference_sales"])
                st.session_state[f"cat_impact_rate_{project_id}"] = float(selected["standard_rate"])
                st.session_state[f"cat_category_{project_id}"] = (
                    "外部レポート" if selected.get("source_url") else "自分の仮定"
                )
                st.session_state[f"cat_source_{project_id}"] = (
                    selected.get("source_url") or "保存済みPL・セグメントからの試算"
                )
                st.session_state[f"cat_note_{project_id}"] = selected["assumption"]
                st.rerun()

    st.caption("株価影響は入力した利益率・税率・PERに基づく試算で、将来の株価を保証するものではありません。")
    c1, c2 = responsive_columns(2)
    name = c1.text_input("カタリスト名", key=f"cat_name_{project_id}")
    year = c2.text_input("対象年度", key=f"cat_year_{project_id}")
    description = st.text_area("説明", key=f"cat_desc_{project_id}")
    st.subheader("追加売上の計算")
    method = st.segmented_control(
        "計算方式",
        ["売上比率モデル", "数量モデル"],
        key=f"cat_method_{project_id}",
        required=True,
        width="stretch",
    )
    if method == "売上比率モデル":
        a, b = responsive_columns(2)
        calculation_base_sales = a.number_input(
            "基準売上（百万円）", min_value=0.0, step=100.0, key=f"cat_base_sales_{project_id}"
        )
        impact_rate = b.number_input(
            "売上影響率（％）", step=0.5, key=f"cat_impact_rate_{project_id}"
        )
        additional = catalyst_sales_from_rate(calculation_base_sales, impact_rate)
        st.markdown(
            f"### {calculation_base_sales:,.0f}百万円 × {impact_rate:,.1f}％ "
            f"＝ **{additional:,.0f}百万円**"
        )
        target_count = number(st.session_state[f"target_count_{project_id}"])
        target_rate = number(st.session_state[f"target_rate_{project_id}"])
        capture_rate = number(st.session_state[f"capture_rate_{project_id}"])
        price = number(st.session_state[f"price_{project_id}"])
    else:
        st.caption("50と入力すると50％として計算します。")
        a, b, c, d = responsive_columns(4)
        target_count = a.number_input("対象数（件）", min_value=0.0, step=1.0, key=f"target_count_{project_id}")
        target_rate = b.number_input("対象率（％）", step=1.0, key=f"target_rate_{project_id}")
        capture_rate = c.number_input("自社獲得率（％）", step=1.0, key=f"capture_rate_{project_id}")
        price = d.number_input("1件当たり単価（百万円）", min_value=0.0, step=0.1, key=f"price_{project_id}")
        calculation_base_sales = number(st.session_state[f"cat_base_sales_{project_id}"])
        impact_rate = number(st.session_state[f"cat_impact_rate_{project_id}"])
        additional = catalyst_additional_sales(target_count, target_rate, capture_rate, price)
        st.markdown(f"### {target_count:,.0f}件 × {target_rate:,.1f}％ × {capture_rate:,.1f}％ × {price:,.2f}百万円 ＝ **{additional:,.0f}百万円**")
    st.subheader("株価への影響")
    p1, p2, p3 = responsive_columns(3)
    incremental_margin = p1.number_input(
        "増分営業利益率（％）", step=0.5, key=f"cat_incremental_margin_{project_id}"
    )
    effective_tax_rate = p2.number_input(
        "実効税率（％）", min_value=0.0, max_value=100.0, step=1.0,
        key=f"cat_tax_rate_{project_id}",
    )
    valuation_per = p3.number_input(
        "評価PER（倍）", min_value=0.0, step=1.0, key=f"cat_valuation_per_{project_id}"
    )
    price_impact = catalyst_price_impact(
        additional, incremental_margin, effective_tax_rate,
        default_shares, valuation_per, current_price,
    )
    impact_value = price_impact["impact_percent"]
    st.metric(
        "推定株価影響",
        "算出不可" if impact_value is None else f"{impact_value:+,.1f}％",
        None if price_impact["price_uplift"] is None else f"{price_impact['price_uplift']:+,.0f}円",
    )
    with st.expander("計算式と追加売上を見る"):
        st.write(f"追加売上：{additional:,.0f}百万円")
        if price_impact["eps_uplift"] is not None:
            st.write(
                f"{additional:,.0f}百万円 × {incremental_margin:,.1f}％ × "
                f"(1－{effective_tax_rate:,.1f}％) ÷ {default_shares:,.3f}百万株 "
                f"＝ EPS増分 {price_impact['eps_uplift']:,.2f}円"
            )
            st.write(
                f"EPS増分 {price_impact['eps_uplift']:,.2f}円 × PER {valuation_per:,.1f}倍 "
                f"＝ 株価増分 {price_impact['price_uplift']:,.0f}円"
            )
        else:
            st.info("会社概要で現在株価と株式数を入力すると株価影響を表示できます。")
    base_row, base_label = catalyst_base(pl_entries, year)
    base_sales = None if base_row is None or base_row.get("sales") in (None, "") else float(base_row["sales"])
    with st.container(border=True):
        st.markdown("#### 自分予想への反映")
        if base_sales is None:
            st.info("対象年度の会社予想売上、または実績売上をPLへ入力すると反映できます。")
        else:
            st.write(f"基準：**{base_label} {base_sales:,.0f}百万円**")
            st.markdown(
                f"**{base_sales:,.0f}百万円 ＋ {additional:,.0f}百万円 "
                f"＝ 自分予想 {base_sales + additional:,.0f}百万円**"
            )
            st.caption("営業利益・純利益などは推測で埋めず、PL画面で入力した値を残します。")
    st.subheader("根拠")
    e1, e2 = responsive_columns(2)
    category = e1.selectbox("根拠区分", EVIDENCE_CATEGORIES, key=f"cat_category_{project_id}")
    source = e2.text_input("出典", key=f"cat_source_{project_id}")
    note = st.text_area("補足メモ", key=f"cat_note_{project_id}")
    actual_sales = [
        float(row["sales"]) for row in pl_entries
        if row.get("result_type") == "実績" and row.get("sales") not in (None, "")
    ]
    existing_sales = actual_sales[-1] if actual_sales else None
    catalyst = {"catalyst_name": name, "target_year": year, "description": description, "target_count": target_count,
                "target_rate": target_rate, "capture_rate": capture_rate, "price_per_case": price,
                "evidence_category": category, "source": source, "note": note, "additional_sales": additional,
                "calculation_method": method, "base_sales": calculation_base_sales,
                "impact_rate": impact_rate, "incremental_margin": incremental_margin,
                "effective_tax_rate": effective_tax_rate, "valuation_per": valuation_per}
    show_warnings(catalyst_warnings(catalyst, existing_sales))
    save_only, save_and_apply = responsive_columns(2)
    if save_only.button("カタリストのみ保存", icon=":material/save:"):
        save_catalyst(project_id, catalyst)
        st.success("カタリストを保存しました。")
    if save_and_apply.button("保存して自分予想へ反映", type="primary", icon=":material/arrow_forward:"):
        save_catalyst(project_id, catalyst)
        updated, reflected_base, reflected_label = apply_catalyst_to_self_forecast(
            pl_entries,
            year,
            additional,
            number(project.get("shares_outstanding")) or None,
        )
        if reflected_base is None:
            st.warning("カタリストは保存しました。PLに基準売上がないため、自分予想への反映は行っていません。")
        else:
            save_pl_entries(project_id, updated)
            st.session_state.pop(f"pl_years_state_{project_id}", None)
            clear_state_prefix(f"pl_matrix_simple_{project_id}")
            clear_state_prefix(f"pl_matrix_mobile_{project_id}")
            st.success(
                f"{reflected_label}を基準に、{year}の自分予想売上を"
                f"{reflected_base + additional:,.0f}百万円へ反映しました。"
            )


def segment_page() -> None:
    """セグメント売上・利益・会社固有KPIを年度横並びで表示する。"""
    st.title("セグメント分析")
    project_id = require_project()
    if project_id is None:
        return
    mobile = is_mobile_view()
    st.caption("年度を横、セグメント売上・利益・会社固有KPIを縦に並べます。金額単位は百万円です。")
    project = get_project(project_id) or {}
    financial_document_import_panel(project, "segment")
    stored_entries = get_segment_entries(project_id)
    stored_metrics = get_segment_metrics(project_id)
    year_state_key = f"segment_years_state_{project_id}"
    name_state_key = f"segment_names_state_{project_id}"
    if year_state_key not in st.session_state:
        st.session_state[year_state_key] = normalize_metadata(
            metadata_from_entries([*stored_entries, *stored_metrics] or get_pl_entries(project_id))
        )
    if name_state_key not in st.session_state:
        st.session_state[name_state_key] = segment_names_from_entries(stored_entries)
    metadata = st.session_state[year_state_key]
    segment_names = st.session_state[name_state_key]

    has_segment_data = bool(stored_entries or stored_metrics)
    mode = "数値を編集"
    if has_segment_data and metadata:
        mode_key = f"segment_view_mode_{project_id}"
        if st.session_state.pop(f"segment_return_to_display_{project_id}", False):
            st.session_state[mode_key] = "完成表"
        mode = st.segmented_control(
            "セグメントの表示モード",
            ["完成表", "数値を編集"],
            default="完成表",
            required=True,
            key=mode_key,
            width="stretch",
        )
    if mode == "完成表":
        completed_metadata = metadata
        completed = segment_analysis_frame(stored_entries, metadata, stored_metrics)
        if mobile:
            titles = [column_title(row["fiscal_year"], row["result_type"]) for row in metadata]
            selected_title = st.selectbox(
                "表示する年度",
                titles,
                index=len(titles) - 1,
                key=f"segment_mobile_display_year_{project_id}",
            )
            completed = completed[["科目", selected_title]]
            completed_metadata = [
                row for row in metadata
                if column_title(row["fiscal_year"], row["result_type"]) == selected_title
            ]
        st.subheader("セグメント別業績")
        st.markdown(":red-badge[実績]  :orange-badge[会社予想]  :blue-badge[自分予想]")
        st.dataframe(
            color_columns(completed, completed_metadata),
            hide_index=True,
            height=min(760, 36 * (len(completed) + 1)),
            column_config={"科目": st.column_config.TextColumn("科目", pinned=True, width="large")},
        )
        sources = list(dict.fromkeys(
            str(row.get("source", "")).strip()
            for row in [*stored_entries, *stored_metrics]
            if str(row.get("source", "")).strip()
        ))
        if sources:
            st.caption("出典：" + " ｜ ".join(sources[:6]))
        return

    with st.expander("手入力の行・列を設定", expanded=not metadata or not segment_names, icon=":material/table_edit:"):
        year_col, type_col, year_action = responsive_columns([2, 2, 1], vertical_alignment="bottom")
        new_year = year_col.text_input("年度", placeholder="例：2026.3", key=f"segment_new_year_{project_id}")
        new_type = type_col.selectbox("区分", RESULT_TYPES, key=f"segment_new_type_{project_id}")
        if year_action.button("列を追加", icon=":material/add:", key=f"segment_add_year_{project_id}"):
            year = new_year.strip()
            if not year:
                st.warning("年度を入力してください。")
            elif any(row["fiscal_year"] == year and row["result_type"] == new_type for row in metadata):
                st.warning("同じ年度・区分はすでにあります。")
            else:
                st.session_state[year_state_key] = [*metadata, {"fiscal_year": year, "result_type": new_type}]
                clear_state_prefix(f"segment_combined_simple_{project_id}")
                clear_state_prefix(f"segment_combined_mobile_{project_id}")
                st.rerun()
        segment_col, segment_action = responsive_columns([4, 1], vertical_alignment="bottom")
        new_segment = segment_col.text_input("セグメント名", key=f"segment_new_name_{project_id}")
        if segment_action.button("行を追加", icon=":material/add:", key=f"segment_add_name_{project_id}"):
            name = new_segment.strip()
            if not name:
                st.warning("セグメント名を入力してください。")
            elif name in segment_names:
                st.warning("同じセグメント名は追加できません。")
            else:
                st.session_state[name_state_key] = [*segment_names, name]
                clear_state_prefix(f"segment_combined_simple_{project_id}")
                clear_state_prefix(f"segment_combined_mobile_{project_id}")
                st.rerun()
        if metadata and segment_names:
            delete_kind, delete_target, delete_action = responsive_columns([2, 4, 1], vertical_alignment="bottom")
            delete_mode = delete_kind.selectbox("削除対象", ["年度", "セグメント"], key=f"segment_delete_mode_{project_id}")
            choices = (
                [f"{row['fiscal_year']}｜{row['result_type']}" for row in metadata]
                if delete_mode == "年度" else segment_names
            )
            target = delete_target.selectbox("削除する項目", choices, key=f"segment_delete_target_{project_id}")
            if delete_action.button("削除", icon=":material/delete:", key=f"segment_delete_{project_id}"):
                if delete_mode == "年度":
                    st.session_state[year_state_key] = [
                        row for row in metadata
                        if f"{row['fiscal_year']}｜{row['result_type']}" != target
                    ]
                else:
                    st.session_state[name_state_key] = [name for name in segment_names if name != target]
                clear_state_prefix(f"segment_combined_simple_{project_id}")
                clear_state_prefix(f"segment_combined_mobile_{project_id}")
                st.rerun()
    if not metadata or not segment_names:
        st.subheader("セグメント別業績")
        st.caption("抽出できなかった場合も、この表から直接入力できます。空欄は保存されません。")
        starter_year = metadata[-1]["fiscal_year"] if metadata else ""
        starter_type = metadata[-1]["result_type"] if metadata else "実績"
        starter = pd.DataFrame([{
            "年度": starter_year,
            "区分": starter_type,
            "セグメント": "",
            "売上高（百万円）": None,
            "利益（百万円）": None,
        }])
        edited_starter = st.data_editor(
            starter,
            hide_index=True,
            num_rows="dynamic",
            column_config={
                "年度": st.column_config.TextColumn("年度", required=True),
                "区分": st.column_config.SelectboxColumn("区分", options=RESULT_TYPES, required=True),
                "セグメント": st.column_config.TextColumn("セグメント", required=True),
                "売上高（百万円）": st.column_config.NumberColumn("売上高（百万円）", format="%,.0f"),
                "利益（百万円）": st.column_config.NumberColumn("利益（百万円）", format="%,.0f"),
            },
            key=f"segment_quick_start_{project_id}",
        )
        if st.button("入力したセグメントを保存", type="primary", icon=":material/save:", key=f"segment_quick_save_{project_id}"):
            quick_records = []
            for row in edited_starter.where(pd.notnull(edited_starter), None).to_dict("records"):
                year = str(row.get("年度") or "").strip()
                name = str(row.get("セグメント") or "").strip()
                sales = row.get("売上高（百万円）")
                profit = row.get("利益（百万円）")
                if year and name and (sales is not None or profit is not None):
                    quick_records.append({
                        "fiscal_year": year,
                        "result_type": row.get("区分") or "実績",
                        "segment_name": name,
                        "sales": sales,
                        "operating_profit": profit,
                        "source": "手入力",
                        "note": "",
                    })
            if not quick_records:
                st.warning("年度、セグメント名、売上高または利益を入力してください。")
            else:
                save_segment_entries(project_id, quick_records)
                st.session_state.pop(year_state_key, None)
                st.session_state.pop(name_state_key, None)
                st.success("セグメント別業績を保存しました。")
                st.rerun()
        return
    number_config = {
        "セグメント": st.column_config.TextColumn("セグメント", pinned=True),
        "項目": st.column_config.TextColumn("項目", pinned=True),
    }
    for meta in metadata:
        title = f"{meta['fiscal_year']}｜{meta['result_type']}"
        number_config[title] = st.column_config.NumberColumn(title, format="%,.0f")

    st.subheader("セグメント別業績を編集")
    combined_source = segment_combined_input_frame(stored_entries, metadata, segment_names)
    selected_segment_meta = metadata
    if mobile:
        mobile_titles = [column_title(row["fiscal_year"], row["result_type"]) for row in metadata]
        selected_title = st.selectbox(
            "編集する年度",
            mobile_titles,
            index=len(mobile_titles) - 1,
            key=f"segment_mobile_year_{project_id}",
        )
        selected_index = mobile_titles.index(selected_title)
        selected_segment_meta = [metadata[selected_index]]
        mobile_rows = []
        for name in segment_names:
            sales_match = combined_source[
                (combined_source["セグメント"] == name) & (combined_source["項目"] == "売上高")
            ]
            profit_match = combined_source[
                (combined_source["セグメント"] == name) & (combined_source["項目"] == "利益")
            ]
            mobile_rows.append({
                "セグメント": name,
                "売上高（百万円）": None if sales_match.empty else sales_match.iloc[0][selected_title],
                "利益（百万円）": None if profit_match.empty else profit_match.iloc[0][selected_title],
            })
        mobile_edited = st.data_editor(
            pd.DataFrame(mobile_rows),
            hide_index=True,
            disabled=["セグメント"],
            column_config={
                "セグメント": st.column_config.TextColumn("セグメント", pinned=True, width="medium"),
                "売上高（百万円）": st.column_config.NumberColumn("売上高", format="%,.0f", width="small"),
                "利益（百万円）": st.column_config.NumberColumn("利益", format="%,.0f", width="small"),
            },
            key=f"segment_combined_mobile_{project_id}_{selected_index}",
            height=min(560, 38 * (len(segment_names) + 2)),
        )
        edited = combined_source.copy()
        for row in mobile_edited.where(pd.notnull(mobile_edited), None).to_dict("records"):
            for item, value_key in (("売上高", "売上高（百万円）"), ("利益", "利益（百万円）")):
                mask = (edited["セグメント"] == row["セグメント"]) & (edited["項目"] == item)
                edited.loc[mask, selected_title] = row.get(value_key)
    else:
        edited = st.data_editor(
            combined_source,
            hide_index=True,
            disabled=["セグメント", "項目"],
            column_config=number_config,
            key=f"segment_combined_simple_{project_id}",
            height=min(650, 36 * (len(segment_names) * 2 + 2)),
        )
    source = stored_entries[0]["source"] if stored_entries else "手入力"
    note = stored_entries[0]["note"] if stored_entries else ""
    records = [
        row for row in segment_records_from_combined_frame(edited, metadata, source, note)
        if row["sales"] is not None or row["operating_profit"] is not None
    ]

    if records:
        st.caption("入力値の合計")
        st.dataframe(
            color_columns(segment_totals_frame(records, selected_segment_meta), selected_segment_meta),
            hide_index=True,
            column_config={"セグメント": st.column_config.TextColumn("セグメント", pinned=True)},
        )

    with st.expander("契約社数・店舗数・平均単価などのKPIを編集", expanded=bool(stored_metrics)):
        metric_source = segment_metrics_input_frame(stored_metrics, metadata)
        if metric_source.empty:
            metric_source = pd.DataFrame([{
                "KPI": "", "単位": "",
                **{column_title(row["fiscal_year"], row["result_type"]): None for row in metadata},
            }])
        metric_config = {
            "KPI": st.column_config.TextColumn("KPI", required=True, pinned=True),
            "単位": st.column_config.TextColumn("単位"),
        }
        metric_config.update({
            column_title(row["fiscal_year"], row["result_type"]): st.column_config.NumberColumn(format="%,.2f")
            for row in metadata
        })
        edited_metrics = st.data_editor(
            metric_source,
            hide_index=True,
            num_rows="dynamic",
            column_config=metric_config,
            key=f"segment_metrics_editor_{project_id}",
        )
    metric_source_name = stored_metrics[0]["source"] if stored_metrics else "手入力"
    metric_records = segment_metrics_from_frame(edited_metrics, metadata, metric_source_name)

    if st.button("セグメント表を保存", type="primary", icon=":material/save:"):
        if not records and not metric_records:
            st.error("セグメント売上・利益またはKPIを1つ以上入力してください。")
        else:
            save_segment_entries(project_id, records)
            save_segment_metrics(project_id, metric_records)
            st.session_state[f"segment_return_to_display_{project_id}"] = True
            st.success("セグメント表を保存しました。")
            st.rerun()


def scenario_page() -> None:
    st.title("シナリオ分析")
    project_id = require_project()
    if project_id is None:
        return
    project = get_project(project_id) or {}
    saved = {row["scenario_name"]: row for row in get_scenarios(project_id)}
    pl = get_pl_entries(project_id)
    default_sales = number(pl[-1]["sales"]) if pl else 0.0
    default_shares = number(project.get("shares_outstanding"))
    catalyst = get_catalyst(project_id)
    default_catalyst = catalyst_additional_sales(catalyst["target_count"], catalyst["target_rate"], catalyst["capture_rate"], catalyst["price_per_case"]) if catalyst else 0.0
    inputs = []
    for scenario in SCENARIOS:
        row = saved.get(scenario, {})
        with st.expander(f"{scenario}シナリオ", expanded=True):
            a, b, c, d = responsive_columns(4)
            base = a.number_input("既存事業売上高（基準・百万円）", min_value=0.0, value=number(row.get("base_sales"), default_sales), key=f"base_{project_id}_{scenario}")
            growth = b.number_input("既存事業売上成長率（％）", value=number(row.get("existing_sales_growth_rate")), key=f"growth_{project_id}_{scenario}")
            cat_sales = c.number_input("カタリスト追加売上高（百万円）", min_value=0.0, value=number(row.get("catalyst_sales"), default_catalyst), key=f"cat_sales_{project_id}_{scenario}")
            margin = d.number_input("営業利益率（％）", value=number(row.get("operating_margin_rate")), key=f"margin_{project_id}_{scenario}")
            e, f, g = responsive_columns(3)
            tax = e.number_input("実効税率（％）", min_value=0.0, max_value=100.0, value=number(row.get("effective_tax_rate")), key=f"tax_{project_id}_{scenario}")
            shares = f.number_input("平均株式数（百万株）", min_value=0.0, value=number(row.get("shares_outstanding"), default_shares), key=f"shares_{project_id}_{scenario}")
            per = g.number_input("想定PER（倍）", min_value=0.0, value=number(row.get("per")), key=f"per_{project_id}_{scenario}")
            result = scenario_calculation(base, growth, cat_sales, margin, tax, shares, per)
            target = result["target_price"]
            upside = None if target is None or number(project.get("current_price")) == 0 else target / number(project.get("current_price")) - 1
            m1, m2, m3, m4 = metric_slots(4)
            m1.metric("予想売上高（百万円）", f"{result['forecast_sales']:,.0f}")
            m2.metric("予想営業利益（百万円）", f"{result['operating_profit']:,.0f}")
            m3.metric("予想EPS（円）", "-" if result["eps"] is None else f"{result['eps']:,.2f}")
            m4.metric("目標株価（円）", "-" if target is None else f"{target:,.0f}", None if upside is None else f"上昇余地 {upside:.1%}")
            inputs.append({"scenario_name": scenario, "base_sales": base, "existing_sales_growth_rate": growth, "catalyst_sales": cat_sales,
                           "operating_margin_rate": margin, "effective_tax_rate": tax, "shares_outstanding": shares, "per": per})
    if st.button("シナリオを保存", type="primary"):
        save_scenarios(project_id, inputs)
        st.success("シナリオを保存しました。")


def main() -> None:
    apply_responsive_styles()
    st.session_state.setdefault("display_mode", DISPLAY_AUTO)
    st.sidebar.title("銘柄発表")
    st.sidebar.segmented_control(
        "表示モード",
        DISPLAY_OPTIONS,
        key="display_mode",
        on_change=reset_navigation_widgets,
        width="stretch",
    )
    mobile = is_mobile_view()
    st.sidebar.caption(
        "現在はスマホ向け表示です。" if mobile else "現在はPC向け表示です。"
    )
    projects = list_projects()
    labels = {p["id"]: f"{p['project_name']}（{p['stock_code'] or 'コード未設定'}）" for p in projects}
    ids = [None, *labels]
    pages = ["銘柄プロジェクト", "PL・業績予想", "セグメント分析", "カタリスト計算", "シナリオ分析"]
    current_project = st.session_state.get("project_id")
    current_project = current_project if current_project in ids else None
    current_page = st.session_state.get("current_page", pages[0])
    current_page = current_page if current_page in pages else pages[0]
    navigation_nonce = int(st.session_state.get("navigation_nonce", 0))

    if mobile:
        with st.container(border=True):
            st.caption(":material/smartphone: スマホ表示")
            selected = st.selectbox(
                "プロジェクト",
                ids,
                format_func=lambda value: "未選択" if value is None else labels[value],
                index=ids.index(current_project),
                key=f"mobile_project_selector_{navigation_nonce}",
            )
            st.button(
                "新規プロジェクト",
                icon=":material/add:",
                width="stretch",
                key="mobile_new_project",
                on_click=start_new_project,
            )
        # 長いPL・セグメント画面でも、画面切替を常に見える位置に置く。
        mobile_page_labels = {
            "プロジェクト": "銘柄プロジェクト",
            "PL": "PL・業績予想",
            "セグメント": "セグメント分析",
            "カタリスト": "カタリスト計算",
            "シナリオ": "シナリオ分析",
        }
        current_mobile_label = next(
            label
            for label, page_name in mobile_page_labels.items()
            if page_name == current_page
        )
        with st.bottom:
            selected_mobile_label = st.segmented_control(
                "画面",
                list(mobile_page_labels),
                default=current_mobile_label,
                required=True,
                key=f"mobile_page_selector_{navigation_nonce}",
                width="stretch",
            )
        page = mobile_page_labels.get(selected_mobile_label, current_page)
    else:
        selected = st.sidebar.selectbox(
            "プロジェクトを選択",
            ids,
            format_func=lambda value: "未選択" if value is None else labels[value],
            index=ids.index(current_project),
            key=f"desktop_project_selector_{navigation_nonce}",
        )
        st.sidebar.button(
            "新規プロジェクト",
            icon=":material/add:",
            width="stretch",
            on_click=start_new_project,
        )
        page = st.sidebar.radio(
            "画面",
            pages,
            index=pages.index(current_page),
            key=f"desktop_page_selector_{navigation_nonce}",
        )

    st.session_state.project_id = selected
    st.session_state.current_page = page
    {"銘柄プロジェクト": project_page, "PL・業績予想": pl_page, "セグメント分析": segment_page, "カタリスト計算": catalyst_page, "シナリオ分析": scenario_page}[page]()


if __name__ == "__main__":
    main()
