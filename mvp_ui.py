"""銘柄発表準備をサイト内で完結させるシンプルな6タブUI。"""
from __future__ import annotations

from datetime import date
import os
import unicodedata
from typing import Any

import altair as alt
import pandas as pd
import streamlit as st

from data_providers import YFinanceMarketDataProvider
from database import (
    DEFAULT_DB_PATH,
    delete_project,
    get_migration_report,
    get_pl_entries,
    get_project,
    get_project_mvp_data,
    get_segment_entries,
    initialize_database,
    list_projects,
    normalize_result_type,
    rename_project,
    save_pl_entries,
    save_project,
    save_project_mvp_data,
    save_segment_entries,
)
from exporter import frame_to_csv, frame_to_excel, frame_to_tsv
from financial_data_models import standard_pl_support
from kpi_engine import (
    INPUT_AMOUNT,
    INPUT_DIRECT,
    INPUT_RATE,
    SCENARIOS,
    SCENARIO_LABELS,
    apply_kpi_sales_to_pl,
    calculate_forecast,
    contribution_analysis,
    parse_number,
    segment_reconciliation,
)
from market_data import MarketDataError
from pdf_extractor import (
    PDFExtractionError,
    PL_KEYWORDS,
    SEGMENT_KEYWORDS,
    extract_pdf_candidates,
)
from pl_import_service import acquire_pl_candidates, merge_with_existing


PL_INPUT_COLUMNS = {
    "年度名": "fiscal_year", "区分": "result_type", "売上高": "sales", "売上原価": "cost_of_sales",
    "販管費": "sga_expenses", "営業利益": "operating_profit", "経常利益": "ordinary_profit",
    "当期純利益": "net_income", "EPS": "reported_eps", "平均株式数": "shares_outstanding",
    "出典": "source", "基準日": "basis_date",
}
PL_DISPLAY_ROWS = (
    ("売上高", "sales", "amount"), ("売上原価", "cost_of_sales", "amount"),
    ("売上原価率", "cost_ratio", "ratio"), ("売上総利益", "gross_profit", "amount"),
    ("売上高総利益率", "gross_margin", "ratio"), ("販管費", "sga_expenses", "amount"),
    ("販管費率", "sga_ratio", "ratio"), ("営業利益", "operating_profit", "amount"),
    ("営業利益率", "operating_margin", "ratio"), ("経常利益", "ordinary_profit", "amount"),
    ("経常利益率", "ordinary_margin", "ratio"), ("当期純利益", "net_income", "amount"),
    ("当期純利益率", "net_margin", "ratio"), ("EPS", "eps", "eps"),
)
OVERVIEW_FIELDS = (
    ("what_company", "何をしている会社か"), ("main_business", "主力事業"),
    ("products_services", "主要商品、サービス"), ("customers", "主要顧客"),
    ("competitors", "競合企業"), ("competitive_strength", "競合優位性、強み"),
    ("attention_points", "注意点"),
)
MEMO_FIELDS = (
    ("company_overview", "会社概要"), ("strengths", "会社の強み"), ("investment_thesis", "投資仮説"),
    ("catalyst", "カタリスト"), ("impact_mechanism", "カタリストが業績に反映される仕組み"),
    ("sales_forecast", "売上予測の説明"), ("kpi_basis", "KPI予測の根拠"),
    ("competitive_advantage", "競合優位性"), ("not_priced_in", "市場が織り込んでいない理由"),
    ("risks", "リスク、反論"), ("monitoring", "今後確認する指標"), ("reference_urls", "参考URL"),
    ("presentation_script", "発表原稿"), ("expected_questions", "質問されそうな内容"),
    ("answer_notes", "質問への回答メモ"),
)


def _records(frame: pd.DataFrame) -> list[dict[str, Any]]:
    return frame.where(pd.notnull(frame), None).to_dict("records")


def _has_value(value: Any) -> bool:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return False
    return bool(str(value).strip()) if isinstance(value, str) else True


def _period_title(row: dict[str, Any]) -> str:
    year = str(row.get("fiscal_year") or "").strip()
    kind = normalize_result_type(row.get("result_type"))
    if year in {kind, "独自予想"}:
        return "独自予想" if kind == "自分予想" else kind
    return f"{year} {kind}".strip()


def _number(value: Any) -> float | None:
    return parse_number(value)


def _ratio(numerator: Any, denominator: Any) -> float | None:
    num, den = _number(numerator), _number(denominator)
    return None if num is None or den in (None, 0) else num / den * 100


def _format(value: Any, kind: str = "amount") -> str:
    number = _number(value)
    if number is None:
        return ""
    if kind == "ratio":
        return f"{number:,.1f}%"
    if kind == "eps":
        return f"{number:,.2f}"
    return f"{number:,.2f}" if abs(number) < 100 else f"{number:,.0f}"


def _pl_calculated(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    sales, cost = _number(row.get("sales")), _number(row.get("cost_of_sales"))
    gross = None if sales is None or cost is None else sales - cost
    result.update({
        "gross_profit": gross,
        "cost_ratio": _ratio(cost, sales),
        "gross_margin": _ratio(gross, sales),
        "sga_ratio": _ratio(row.get("sga_expenses"), sales),
        "operating_margin": _ratio(row.get("operating_profit"), sales),
        "ordinary_margin": _ratio(row.get("ordinary_profit"), sales),
        "net_margin": _ratio(row.get("net_income"), sales),
    })
    reported_eps = _number(row.get("reported_eps"))
    shares = _number(row.get("shares_outstanding"))
    result["eps"] = reported_eps if reported_eps is not None else (
        None if shares in (None, 0) or _number(row.get("net_income")) is None
        else _number(row.get("net_income")) / shares
    )
    return result


def _five_period_records(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    clean = [dict(row, result_type=normalize_result_type(row.get("result_type"))) for row in entries]
    actual = sorted((row for row in clean if row.get("result_type") == "実績"), key=lambda row: str(row.get("fiscal_year")))[-3:]
    company = sorted((row for row in clean if row.get("result_type") == "会社予想"), key=lambda row: str(row.get("fiscal_year")))[-1:]
    own = sorted((row for row in clean if row.get("result_type") == "自分予想"), key=lambda row: str(row.get("fiscal_year")))[-1:]
    default_actual = ["3期前実績", "2期前実績", "前期実績"]
    missing = 3 - len(actual)
    actual_rows = [
        {"fiscal_year": label, "result_type": "実績", "_placeholder": True} for label in default_actual[:missing]
    ] + [dict(row) for row in actual]
    company_row = dict(company[0]) if company else {"fiscal_year": "会社予想", "result_type": "会社予想", "_placeholder": True}
    own_row = dict(own[0]) if own else {"fiscal_year": "独自予想", "result_type": "自分予想", "_placeholder": True}
    return [*actual_rows, company_row, own_row]


def _pl_edit_frame(entries: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for entry in _five_period_records(entries):
        rows.append({"_id": entry.get("id"), "_placeholder": entry.get("_placeholder", False),
                     **{label: entry.get(key) for label, key in PL_INPUT_COLUMNS.items()}})
    frame = pd.DataFrame(rows, columns=["_id", "_placeholder", *PL_INPUT_COLUMNS])
    for column in list(PL_INPUT_COLUMNS)[2:10]:
        frame[column] = frame[column].map(lambda value: _format(value, "eps" if column == "EPS" else "amount"))
    for column in ["年度名", "区分", "出典", "基準日"]:
        frame[column] = frame[column].fillna("")
    return frame


def _pl_records_from_editor(frame: pd.DataFrame) -> list[dict[str, Any]]:
    rows = []
    for raw in _records(frame):
        year = str(raw.get("年度名") or "").strip()
        if not year:
            continue
        row = {key: raw.get(label) for label, key in PL_INPUT_COLUMNS.items()}
        row["fiscal_year"] = year
        row["result_type"] = normalize_result_type(row.get("result_type"))
        for key in ("sales", "cost_of_sales", "sga_expenses", "operating_profit", "ordinary_profit",
                    "net_income", "reported_eps", "shares_outstanding"):
            row[key] = _number(row.get(key))
        row["id"] = raw.get("_id")
        meaningful = any(_has_value(row.get(key)) for key in (
            "sales", "cost_of_sales", "sga_expenses", "operating_profit", "ordinary_profit",
            "net_income", "reported_eps", "shares_outstanding", "source", "basis_date",
        ))
        if raw.get("_placeholder") and not meaningful:
            continue
        rows.append(row)
    return rows


def _jquants_api_key() -> str | None:
    """APIキーは環境変数またはSecretsから読み、画面やDBへ保存しない。"""
    key = os.getenv("JQUANTS_API_KEY")
    if key:
        return key
    try:
        return st.secrets.get("JQUANTS_API_KEY")
    except Exception:
        return None


@st.cache_data(ttl="6h", max_entries=50, show_spinner=False)
def _cached_pl_candidates(
    stock_code: str,
    company_name: str,
    ir_url: str,
    api_key: str | None,
) -> dict[str, Any]:
    result = acquire_pl_candidates(
        stock_code,
        company_name,
        saved_ir_url=ir_url,
        jquants_api_key=api_key,
    )
    return {
        "records": result.records,
        "warnings": result.warnings,
        "sources": result.sources,
        "ir_url": result.ir_url,
    }


def _pl_candidate_frame(records: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for row in records:
        rows.append({
            "反映": True,
            "年度名": row.get("fiscal_year"),
            "区分": normalize_result_type(row.get("result_type")),
            "売上高": row.get("sales"),
            "売上原価": row.get("cost_of_sales"),
            "販管費": row.get("sga_expenses"),
            "営業利益": row.get("operating_profit"),
            "経常利益": row.get("ordinary_profit"),
            "当期純利益": row.get("net_income"),
            "EPS": row.get("reported_eps", row.get("eps")),
            "平均株式数": row.get("shares_outstanding", row.get("average_shares")),
            "出典": row.get("source", ""),
            "基準日": row.get("basis_date", ""),
        })
    return pd.DataFrame(rows)


def _pl_import_panel(project: dict[str, Any], existing: list[dict[str, Any]]) -> None:
    project_id = int(project["id"])
    preview_key = f"pl_import_preview_{project_id}"
    stock_code = str(project.get("stock_code") or "").strip()
    company_name = str(project.get("company_name") or project.get("project_name") or "").strip()
    with st.container(border=True):
        st.markdown("**過去3期のPLと会社予想を取得**")
        st.caption("企業公式の決算資料を優先し、設定済みの場合のみJ-Quantsで不足を補います。確認するまで保存しません。")
        if not stock_code:
            st.info("銘柄概要で4桁の証券コードを保存すると、PL候補を取得できます。")
        elif st.button("PL候補を取得", icon=":material/download:", key=f"fetch_pl_{project_id}"):
            with st.spinner("決算資料と財務データを確認しています…"):
                st.session_state[preview_key] = _cached_pl_candidates(
                    stock_code,
                    company_name,
                    str(project.get("ir_url") or ""),
                    _jquants_api_key(),
                )

        preview = st.session_state.get(preview_key)
        if not preview:
            return
        for warning in preview.get("warnings", []):
            st.caption(f"・{warning}")
        records = preview.get("records", [])
        if not records:
            st.warning("取得できるPL候補がありませんでした。下のPDF候補検索または手入力を利用できます。")
            return
        if preview.get("sources"):
            st.caption(f"取得元：{' / '.join(preview['sources'])}")
        edited = st.data_editor(
            _pl_candidate_frame(records),
            hide_index=True,
            key=f"pl_import_editor_{project_id}",
            column_config={
                "反映": st.column_config.CheckboxColumn("反映"),
                "区分": st.column_config.SelectboxColumn("区分", options=["実績", "会社予想", "自分予想"]),
                **{column: st.column_config.NumberColumn(column) for column in (
                    "売上高", "売上原価", "販管費", "営業利益", "経常利益", "当期純利益", "EPS", "平均株式数"
                )},
            },
        )
        overwrite = st.checkbox(
            "同じ年度の保存済み数値も取得値で更新する",
            value=False,
            key=f"pl_import_overwrite_{project_id}",
            help="オフの場合、保存済みの手入力値を残し、空欄だけを補完します。",
        )
        if st.button("確認した候補をPLへ反映", type="primary", key=f"apply_pl_import_{project_id}"):
            selected = edited[edited["反映"].fillna(False)].drop(columns=["反映"])
            selected["_placeholder"] = False
            selected["_id"] = None
            imported = _pl_records_from_editor(selected)
            if not imported:
                st.warning("反映する年度を1つ以上選択してください。")
            else:
                save_pl_entries(project_id, merge_with_existing(existing, imported, overwrite=overwrite))
                discovered_ir = str(preview.get("ir_url") or "")
                if discovered_ir and not project.get("ir_url"):
                    save_project({**project, "ir_url": discovered_ir})
                st.session_state.pop(preview_key, None)
                st.success("確認したPLを保存しました。")
                st.rerun()


def _merge_by_period(existing: list[dict], edited: list[dict]) -> list[dict]:
    merged = {(str(row.get("fiscal_year")), str(row.get("result_type"))): dict(row) for row in existing}
    for row in edited:
        merged[(str(row.get("fiscal_year")), str(row.get("result_type")))] = dict(row)
    return list(merged.values())


def _pl_matrix(entries: list[dict[str, Any]]) -> pd.DataFrame:
    calculated = [_pl_calculated(row) for row in _five_period_records(entries)]
    rows = []
    for label, key, kind in PL_DISPLAY_ROWS:
        record = {"項目": label}
        for row in calculated:
            title = _period_title(row)
            record[title] = _format(row.get(key), kind)
        rows.append(record)
    return pd.DataFrame(rows)


def _download_row(frame: pd.DataFrame, prefix: str, sheet: str) -> None:
    with st.container(horizontal=True):
        st.download_button("CSV", frame_to_csv(frame), f"{prefix}.csv", "text/csv", icon=":material/download:")
        st.download_button("Excel", frame_to_excel(frame, sheet), f"{prefix}.xlsx",
                           "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", icon=":material/download:")
        st.download_button("タブ区切り", frame_to_tsv(frame), f"{prefix}.tsv", "text/tab-separated-values",
                           icon=":material/content_copy:")


def _line_chart(frame: pd.DataFrame, x: str, series: list[str], value_title: str) -> None:
    """空欄を除外し、Vegaの無限範囲警告を出さずに推移を描く。"""
    if frame.empty:
        st.caption("グラフに表示できる数値がありません。")
        return
    long = frame.melt(id_vars=[x], value_vars=series, var_name="系列", value_name="値")
    long["値"] = pd.to_numeric(long["値"], errors="coerce")
    long["値"] = long["値"].replace([float("inf"), float("-inf")], pd.NA)
    long = long.dropna(subset=[x, "値"])
    if long.empty:
        st.caption("グラフに表示できる数値がありません。")
        return
    chart = alt.Chart(long).mark_line(point=True).encode(
        x=alt.X(f"{x}:N", title=x),
        y=alt.Y("値:Q", title=value_title),
        color=alt.Color("系列:N", title=""),
        tooltip=[alt.Tooltip(f"{x}:N"), alt.Tooltip("系列:N"), alt.Tooltip("値:Q", format=",.2f")],
    )
    st.altair_chart(chart, width="stretch")


def _bar_chart(frame: pd.DataFrame, x: str, y: str) -> None:
    """数値がない場合はVegaへ渡さず、無限範囲警告を防ぐ。"""
    clean = frame.copy()
    if x not in clean or y not in clean:
        st.caption("グラフに表示できる数値がありません。")
        return
    clean[y] = pd.to_numeric(clean[y], errors="coerce")
    clean[y] = clean[y].replace([float("inf"), float("-inf")], pd.NA)
    clean = clean.dropna(subset=[x, y])
    if clean.empty:
        st.caption("グラフに表示できる数値がありません。")
        return
    st.bar_chart(clean, x=x, y=y)


@st.cache_data(show_spinner=False, ttl=900, max_entries=20)
def _cached_yfinance(code: str) -> dict[str, Any]:
    return YFinanceMarketDataProvider().fetch(code)


@st.cache_data(show_spinner=False, ttl=3600, max_entries=20)
def _cached_pdf(content: bytes, location: str) -> dict[str, Any]:
    return extract_pdf_candidates(content, PL_KEYWORDS if location == "pl" else SEGMENT_KEYWORDS)


def _pdf_panel(project_id: int, location: str, draft_key: str) -> None:
    label = "PL" if location == "pl" else "セグメント"
    with st.expander(f"決算資料PDFから{label}候補を探す", icon=":material/picture_as_pdf:"):
        uploaded = st.file_uploader("決算短信・IR資料（PDF）", type=["pdf"], key=f"pdf_{location}_{project_id}")
        if not uploaded:
            st.caption("PDFは確認中だけメモリ上で処理し、明示的な保存を選ばない限り永続保存しません。")
            return
        try:
            result = _cached_pdf(uploaded.getvalue(), location)
        except PDFExtractionError as exc:
            st.error(str(exc), icon=":material/error:")
            return
        if result["image_pdf"]:
            st.warning(result["message"], icon=":material/image:")
            return
        candidates = pd.DataFrame(result["candidates"])
        if candidates.empty:
            st.warning("対象キーワード周辺に数値候補を見つけられませんでした。原文を確認して手入力してください。")
            return
        st.caption("候補値・単位・年度・ページ・原文を確認し、必要な行だけ採用してください。ここでは保存されません。")
        edited = st.data_editor(
            candidates, hide_index=True, key=f"pdf_candidates_{location}_{project_id}",
            column_config={"採用": st.column_config.CheckboxColumn("採用"), "周辺原文": st.column_config.TextColumn("周辺原文", width="large")},
        )
        if st.button(f"確認した候補を{label}編集表へ反映", key=f"apply_pdf_{location}_{project_id}", icon=":material/input:"):
            st.session_state[draft_key] = [row for row in _records(edited) if row.get("採用")]
            st.success("編集表へ渡しました。数値と年度を修正し、各タブの保存ボタンで確定してください。")


def _overview_tab(project: dict, data: dict, edit_mode: bool) -> None:
    st.subheader("銘柄概要")
    overview = dict(data["overview"])
    auto = overview.get("market_auto", {})
    manual = overview.get("market_manual", {})
    if edit_mode:
        with st.container(border=True):
            with st.form(f"market_fetch_{project['id']}", border=False):
                code = st.text_input("証券コード（4桁）", value=project.get("stock_code") or "")
                submitted = st.form_submit_button("取得", type="primary", icon=":material/search:")
            if submitted:
                try:
                    with st.spinner("yfinanceから取得しています…"):
                        auto = _cached_yfinance(code)
                    overview["market_auto"] = auto
                    update = dict(project)
                    update.update({"stock_code": code, "company_name": auto.get("company_name") or project.get("company_name"),
                                   "current_price": auto.get("current_price") or project.get("current_price"),
                                   "shares_outstanding": auto.get("shares_outstanding_million") or project.get("shares_outstanding"),
                                   "price_source": "yfinance", "data_retrieved_at": auto.get("retrieved_at")})
                    save_project(update)
                    save_project_mvp_data(project["id"], overview=overview)
                    st.success("取得結果を保存しました。取得できない項目は手入力できます。")
                    st.rerun()
                except MarketDataError as exc:
                    st.error(str(exc), icon=":material/error:")
    def preferred(key: str, fallback: Any = None) -> Any:
        return manual[key] if _number(manual.get(key)) is not None else fallback

    price_fallback = project.get("current_price") or auto.get("current_price")
    price_value = preferred("current_price", price_fallback)
    market_cap_value = preferred("market_cap_yen", auto.get("market_cap_yen"))
    shares = _number(project.get("shares_outstanding")) or _number(auto.get("shares_outstanding_million"))
    if _number(market_cap_value) is None and _number(price_value) is not None and shares is not None:
        market_cap_value = _number(price_value) * shares * 1_000_000
    values = {
        "price": price_value,
        "market_cap": market_cap_value,
        "per": preferred("per", auto.get("per")),
        "pbr": preferred("pbr", auto.get("pbr")),
        "yield": preferred("dividend_yield", auto.get("dividend_yield")),
    }
    with st.container(horizontal=True):
        st.metric("株価", "取得できませんでした" if _number(values["price"]) is None else f"{_number(values['price']):,.0f}円", border=True)
        st.metric("時価総額", "取得できませんでした" if _number(values["market_cap"]) is None else f"{_number(values['market_cap']) / 100_000_000:,.1f}億円", border=True)
        st.metric("PER", "取得できませんでした" if _number(values["per"]) is None else f"{_number(values['per']):,.1f}倍", border=True)
        st.metric("PBR", "取得できませんでした" if _number(values["pbr"]) is None else f"{_number(values['pbr']):,.2f}倍", border=True)
        st.metric("配当利回り", "取得できませんでした" if _number(values["yield"]) is None else f"{_number(values['yield']):,.2f}%", border=True)
    st.caption(f"取得元：{auto.get('source') or project.get('price_source') or '手入力'} ｜ 取得日時：{auto.get('retrieved_at') or project.get('data_retrieved_at') or '―'}")
    if edit_mode:
        with st.form(f"overview_{project['id']}"):
            c1, c2, c3 = st.columns(3)
            company_name = c1.text_input("会社名", value=project.get("company_name") or auto.get("company_name") or "")
            fiscal = c2.text_input("決算期", value=str(overview.get("fiscal_year_end") or auto.get("fiscal_year_end") or ""))
            industry = c3.text_input("業種", value=str(overview.get("industry") or auto.get("industry") or project.get("sector33") or ""))
            c1, c2, c3, c4, c5 = st.columns(5)
            price = c1.text_input("株価（円）", value="" if values["price"] is None else str(values["price"]))
            market_cap = c2.text_input("時価総額（円）", value="" if values["market_cap"] is None else str(values["market_cap"]))
            per = c3.text_input("PER（倍）", value="" if values["per"] is None else str(values["per"]))
            pbr = c4.text_input("PBR（倍）", value="" if values["pbr"] is None else str(values["pbr"]))
            dividend = c5.text_input("配当利回り（%）", value="" if values["yield"] is None else str(values["yield"]))
            edited_overview = dict(overview)
            for key, label in OVERVIEW_FIELDS:
                initial = overview.get(key, "")
                if key == "what_company" and not initial:
                    initial = auto.get("description_candidate", "")
                edited_overview[key] = st.text_area(label, value=str(initial), height=90)
            if st.form_submit_button("銘柄概要を保存", type="primary", icon=":material/save:"):
                edited_overview.update({"fiscal_year_end": fiscal, "industry": industry,
                                        "market_manual": {"current_price": _number(price), "market_cap_yen": _number(market_cap),
                                                          "per": _number(per), "pbr": _number(pbr), "dividend_yield": _number(dividend)}})
                save_project({**project, "company_name": company_name, "current_price": _number(price) or 0,
                              "price_source": "手入力", "price_input_method": "手入力"})
                save_project_mvp_data(project["id"], overview=edited_overview)
                st.success("銘柄概要を保存しました。")
                st.rerun()
    else:
        st.markdown(f"**{project.get('company_name') or '会社名未設定'}（{project.get('stock_code') or 'コード未設定'}）**")
        st.caption(f"決算期：{overview.get('fiscal_year_end') or '―'} ｜ 業種：{overview.get('industry') or project.get('sector33') or '―'}")
        for key, label in OVERVIEW_FIELDS:
            with st.container(border=True):
                st.markdown(f"**{label}**")
                st.write(overview.get(key) or "未入力")


def _pl_tab(project: dict, data: dict, edit_mode: bool) -> None:
    st.subheader("PL")
    project_id = project["id"]
    industry = (
        data.get("overview", {}).get("industry")
        or project.get("sector33")
        or project.get("sector17")
        or ""
    )
    supported, reason = standard_pl_support(industry)
    if not supported:
        st.warning(reason, icon=":material/warning:")
        st.caption("誤った共通PLへ変換しないため、業種別の財務モデルを追加するまで候補値を確定しません。")
        return
    entries = get_pl_entries(project_id)
    settings = dict(data["settings"])
    draft_key = f"mvp_pl_draft_{project_id}"
    st.caption(f"金額単位：{settings.get('pl_unit', '百万円')}。実績・会社予想・独自予想を分けて管理します。")
    if edit_mode:
        _pl_import_panel(project, entries)
        _pdf_panel(project_id, "pl", f"pdf_pl_selected_{project_id}")
        unit_options = ["円", "千円", "百万円", "億円", "その他"]
        selected_unit = st.selectbox("金額単位", unit_options, index=unit_options.index(settings.get("pl_unit", "百万円")) if settings.get("pl_unit", "百万円") in unit_options else 4)
        unit = st.text_input("その他の単位", value=settings.get("pl_unit", "") if selected_unit == "その他" else "", disabled=selected_unit != "その他") or selected_unit
        frame = _pl_edit_frame(entries)
        selected_pdf = st.session_state.pop(f"pdf_pl_selected_{project_id}", [])
        if selected_pdf:
            field_map = {"売上高": "売上高", "売上原価": "売上原価", "販売費及び一般管理費": "販管費",
                         "営業利益": "営業利益", "経常利益": "経常利益", "親会社株主に帰属する当期純利益": "当期純利益",
                         "当期純利益": "当期純利益", "EPS": "EPS", "1株当たり当期純利益": "EPS"}
            for candidate in selected_pdf:
                target_col = field_map.get(candidate.get("項目"))
                if target_col:
                    frame.loc[len(frame) - 1, target_col] = candidate.get("候補値")
        edited = st.data_editor(
            frame, hide_index=True, key=f"pl_editor_{project_id}",
            column_config={"_id": None, "_placeholder": None,
                           "区分": st.column_config.SelectboxColumn("区分", options=["実績", "会社予想", "自分予想"]),
                           **{col: st.column_config.TextColumn(col) for col in list(PL_INPUT_COLUMNS)[2:10]}},
        )
        if st.button("PLを保存", type="primary", icon=":material/save:", key=f"save_pl_{project_id}"):
            edited_records = _pl_records_from_editor(edited)
            if entries and not edited_records:
                st.warning("空の表では既存PLを上書きしません。年度と数値を確認してください。")
            else:
                save_pl_entries(project_id, edited_records)
                save_project_mvp_data(project_id, settings={**settings, "pl_unit": unit})
                st.success("PLを保存しました。")
                st.rerun()
    elif not entries:
        st.info("このプロジェクトには保存済みPLがありません。編集モードでPL候補を取得するか、表へ入力してください。")
    matrix = _pl_matrix(get_pl_entries(project_id))
    st.dataframe(matrix, hide_index=True, column_config={"項目": st.column_config.TextColumn("項目", pinned=True)})
    calculated = [_pl_calculated(row) for row in _five_period_records(get_pl_entries(project_id))]
    chart = pd.DataFrame([{"年度": row.get("fiscal_year"), "売上高": row.get("sales"), "営業利益": row.get("operating_profit")} for row in calculated])
    margins = pd.DataFrame([{"年度": row.get("fiscal_year"), "売上総利益率": row.get("gross_margin"), "営業利益率": row.get("operating_margin")} for row in calculated])
    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown("**売上高と営業利益の推移**")
        _line_chart(chart, "年度", ["売上高", "営業利益"], "金額")
    with c2.container(border=True):
        st.markdown("**売上総利益率と営業利益率の推移**")
        _line_chart(margins, "年度", ["売上総利益率", "営業利益率"], "利益率（%）")
    _download_row(matrix, f"{project.get('stock_code') or 'project'}_pl", "PL")


def _segment_matrix(entries: list[dict[str, Any]]) -> pd.DataFrame:
    entries = [dict(row, result_type=normalize_result_type(row.get("result_type"))) for row in entries]
    actual = sorted(
        set((str(row.get("fiscal_year")), "実績") for row in entries if row.get("result_type") == "実績")
    )[-3:]
    default_actual = ["3期前実績", "2期前実績", "前期実績"]
    missing = 3 - len(actual)
    actual = [(label, "実績") for label in default_actual[:missing]] + actual
    company = sorted(
        set((str(row.get("fiscal_year")), "会社予想") for row in entries if row.get("result_type") == "会社予想")
    )[-1:] or [("会社予想", "会社予想")]
    own = sorted(
        set((str(row.get("fiscal_year")), "自分予想") for row in entries if row.get("result_type") == "自分予想")
    )[-1:] or [("独自予想", "自分予想")]
    periods = [*actual, *company, *own]
    names = list(dict.fromkeys(
        str(row.get("segment_name")) for row in sorted(
            entries, key=lambda row: (row.get("display_order", 0), row.get("id", 0), str(row.get("segment_name")))
        ) if str(row.get("segment_name") or "").strip()
    ))
    lookup = {(str(row.get("fiscal_year")), str(row.get("result_type")), str(row.get("segment_name"))): row.get("sales") for row in entries}
    rows = []
    total = {"項目": "売上高（①＋②＋③＋…）"}
    for year, result_type in periods:
        title = _period_title({"fiscal_year": year, "result_type": result_type})
        values = [_number(lookup.get((year, result_type, name))) for name in names]
        total[title] = sum(value for value in values if value is not None) if any(value is not None for value in values) else None
    rows.append(total)
    for index, name in enumerate(names, start=1):
        row = {"項目": f"{index}：{name}"}
        for year, result_type in periods:
            row[_period_title({"fiscal_year": year, "result_type": result_type})] = lookup.get((year, result_type, name))
        rows.append(row)
    return pd.DataFrame(rows).where(lambda frame: frame.notna(), "")


def _segment_tab(project: dict, data: dict, edit_mode: bool) -> None:
    st.subheader("セグメント")
    project_id = project["id"]
    entries = get_segment_entries(project_id)
    settings = dict(data["settings"])
    st.caption(f"金額単位：{settings.get('segment_unit', settings.get('pl_unit', '百万円'))}。並び順に番号を自動表示します。")
    if edit_mode:
        _pdf_panel(project_id, "segment", f"pdf_segment_selected_{project_id}")
        frame = pd.DataFrame([{
            "_id": row.get("id"), "年度名": row.get("fiscal_year"), "区分": normalize_result_type(row.get("result_type")), "セグメント名": row.get("segment_name"),
            "売上高": row.get("sales"), "利益": row.get("operating_profit"), "行種別": row.get("row_type", "セグメント"),
            "並び順": row.get("display_order", 0), "出典": row.get("source", "手入力"), "メモ": row.get("note", ""),
        } for row in entries], columns=["_id", "年度名", "区分", "セグメント名", "売上高", "利益", "行種別", "並び順", "出典", "メモ"])
        for column in ["売上高", "利益"]:
            frame[column] = frame[column].map(lambda value: _format(value, "amount"))
        frame["並び順"] = pd.to_numeric(frame["並び順"], errors="coerce").fillna(0).astype(int)
        for column in ["年度名", "区分", "セグメント名", "行種別", "出典", "メモ"]:
            frame[column] = frame[column].fillna("")
        selected_pdf = st.session_state.pop(f"pdf_segment_selected_{project_id}", [])
        for candidate in selected_pdf:
            frame.loc[len(frame)] = [None, candidate.get("年度") or "", "実績", candidate.get("項目") or "要確認",
                                     candidate.get("候補値"), None, "セグメント", len(frame), "決算資料PDF",
                                     f"p.{candidate.get('ページ')} {candidate.get('周辺原文', '')}"]
        edited = st.data_editor(
            frame, hide_index=True, num_rows="dynamic", key=f"segment_editor_{project_id}",
            column_config={"_id": None,
                           "区分": st.column_config.SelectboxColumn("区分", options=["実績", "会社予想", "自分予想"]),
                           "行種別": st.column_config.SelectboxColumn("行種別", options=["セグメント", "調整額", "内部取引消去", "その他調整"]),
                           "売上高": st.column_config.TextColumn("売上高"),
                           "利益": st.column_config.TextColumn("利益")},
        )
        unit = st.text_input("セグメント金額単位", value=settings.get("segment_unit", settings.get("pl_unit", "百万円")))
        if st.button("セグメントを保存", type="primary", icon=":material/save:"):
            rows = []
            for raw in _records(edited):
                if not str(raw.get("年度名") or "").strip() or not str(raw.get("セグメント名") or "").strip():
                    continue
                rows.append({"id": raw.get("_id"), "fiscal_year": str(raw["年度名"]), "result_type": normalize_result_type(raw.get("区分")),
                             "segment_name": str(raw["セグメント名"]), "sales": _number(raw.get("売上高")),
                             "operating_profit": _number(raw.get("利益")), "row_type": raw.get("行種別") or "セグメント",
                             "display_order": int(_number(raw.get("並び順")) or 0), "source": raw.get("出典") or "手入力",
                             "note": raw.get("メモ") or ""})
            if entries and not rows:
                st.warning("空の表では既存セグメントを上書きしません。年度とセグメント名を確認してください。")
            else:
                save_segment_entries(project_id, rows)
                save_project_mvp_data(project_id, settings={**settings, "segment_unit": unit})
                st.success("セグメントを保存しました。")
                st.rerun()
    entries = get_segment_entries(project_id)
    matrix = _segment_matrix(entries)
    st.dataframe(matrix, hide_index=True, column_config={"項目": st.column_config.TextColumn("項目", pinned=True)})
    pl_lookup = {(str(row.get("fiscal_year")), str(row.get("result_type"))): row.get("sales") for row in get_pl_entries(project_id)}
    periods = list(dict.fromkeys((str(row.get("fiscal_year")), str(row.get("result_type"))) for row in entries))
    reconciliation = []
    for year, kind in periods:
        values = [row.get("sales") for row in entries if str(row.get("fiscal_year")) == year and str(row.get("result_type")) == kind]
        result = segment_reconciliation(values, pl_lookup.get((year, kind)))
        reconciliation.append({"年度": year, "区分": kind, "セグメント合計": result["segment_total"],
                               "全社売上高": result["company_sales"], "差額": result["difference"]})
    if reconciliation:
        st.markdown("**全社売上との照合**")
        st.dataframe(pd.DataFrame(reconciliation), hide_index=True)
        for row in reconciliation:
            if row["差額"] not in (None, 0):
                st.warning(f"{row['年度']} {row['区分']}：セグメント合計と全社売上高の差額は{row['差額']:,.2f}です。")
    if entries:
        chart_df = pd.DataFrame([{"年度": row.get("fiscal_year"), "セグメント": row.get("segment_name"), "売上高": row.get("sales")} for row in entries if row.get("row_type", "セグメント") == "セグメント"])
        chart_df["売上高"] = pd.to_numeric(chart_df["売上高"], errors="coerce")
        chart_df = chart_df.dropna(subset=["年度", "セグメント", "売上高"])
        c1, c2 = st.columns(2)
        with c1.container(border=True):
            st.markdown("**セグメント別売上推移**")
            if chart_df.empty:
                st.caption("グラフに表示できる数値がありません。")
            else:
                segment_chart = alt.Chart(chart_df).mark_line(point=True).encode(
                    x=alt.X("年度:N", title="年度"), y=alt.Y("売上高:Q", title="売上高"),
                    color=alt.Color("セグメント:N", title=""),
                    tooltip=["年度:N", "セグメント:N", alt.Tooltip("売上高:Q", format=",.2f")],
                )
                st.altair_chart(segment_chart, width="stretch")
        latest = sorted(chart_df["年度"].dropna().astype(str).unique())[-1] if not chart_df.empty else None
        if latest:
            latest_df = chart_df[chart_df["年度"].astype(str) == latest]
            with c2.container(border=True):
                st.markdown(f"**最新年度の構成比（{latest}）**")
                st.altair_chart(
                    alt.Chart(latest_df).mark_arc(innerRadius=40).encode(
                        theta="売上高:Q", color="セグメント:N", tooltip=["セグメント", "売上高"]
                    ),
                    width="stretch",
                )
        yoy = chart_df.sort_values("年度").copy()
        yoy["前年比（%）"] = yoy.groupby("セグメント")["売上高"].pct_change() * 100
        st.dataframe(yoy[["年度", "セグメント", "売上高", "前年比（%）"]], hide_index=True)
    _download_row(matrix, f"{project.get('stock_code') or 'project'}_segments", "セグメント")


def _kpi_defaults() -> pd.DataFrame:
    return pd.DataFrame(columns=["KPI名", "分類", "基準値", "弱気予想値", "標準予想値", "強気予想値", "単位", "入力方法",
                                 "弱気増減率", "標準増減率", "強気増減率", "弱気増減額", "標準増減額", "強気増減額",
                                 "根拠メモ", "参考URL", "情報取得日", "並び順"])


def _kpi_tab(project: dict, data: dict, edit_mode: bool) -> None:
    st.subheader("KPI・売上予測")
    project_id = project["id"]
    kpi_data = dict(data["kpi"])
    kpis = list(kpi_data.get("kpis", []))
    items = list(kpi_data.get("revenue_items", []))
    if edit_mode:
        kpi_frame = pd.DataFrame([{
            "KPI名": row.get("kpi_name"), "分類": row.get("classification", "数量"), "基準値": row.get("base_value"),
            "弱気予想値": row.get("bearish_value"), "標準予想値": row.get("standard_value"), "強気予想値": row.get("bullish_value"),
            "単位": row.get("unit", ""), "入力方法": row.get("input_method", INPUT_DIRECT),
            "弱気増減率": row.get("bearish_change_rate"), "標準増減率": row.get("standard_change_rate"), "強気増減率": row.get("bullish_change_rate"),
            "弱気増減額": row.get("bearish_change_amount"), "標準増減額": row.get("standard_change_amount"), "強気増減額": row.get("bullish_change_amount"),
            "根拠メモ": row.get("basis_note", ""), "参考URL": row.get("reference_url", ""), "情報取得日": row.get("information_date", ""),
            "並び順": row.get("display_order", 0),
        } for row in kpis]) if kpis else _kpi_defaults()
        edited_kpis = st.data_editor(
            kpi_frame, hide_index=True, num_rows="dynamic", key=f"kpi_editor_{project_id}",
            column_config={"分類": st.column_config.SelectboxColumn("分類", options=["数量", "単価", "比率、シェア", "その他"]),
                           "入力方法": st.column_config.SelectboxColumn("入力方法", options=[INPUT_DIRECT, INPUT_RATE, INPUT_AMOUNT]),
                           "参考URL": st.column_config.LinkColumn("参考URL")},
        )
        item_frame = pd.DataFrame([{"売上項目名": row.get("name"), "KPI名（カンマ区切り）": ", ".join(row.get("kpi_names", [])) if isinstance(row.get("kpi_names"), list) else row.get("kpi_names", ""), "並び順": row.get("display_order", 0)} for row in items], columns=["売上項目名", "KPI名（カンマ区切り）", "並び順"])
        st.markdown("**売上項目（選んだKPIを掛け算）**")
        edited_items = st.data_editor(item_frame, hide_index=True, num_rows="dynamic", key=f"revenue_item_editor_{project_id}")
        kpis = []
        for row in _records(edited_kpis):
            if not str(row.get("KPI名") or "").strip():
                continue
            kpis.append({"kpi_name": str(row["KPI名"]).strip(), "classification": row.get("分類") or "数量",
                         "base_value": _number(row.get("基準値")), "bearish_value": _number(row.get("弱気予想値")),
                         "standard_value": _number(row.get("標準予想値")), "bullish_value": _number(row.get("強気予想値")),
                         "unit": row.get("単位") or "", "input_method": row.get("入力方法") or INPUT_DIRECT,
                         "bearish_change_rate": _number(row.get("弱気増減率")), "standard_change_rate": _number(row.get("標準増減率")),
                         "bullish_change_rate": _number(row.get("強気増減率")), "bearish_change_amount": _number(row.get("弱気増減額")),
                         "standard_change_amount": _number(row.get("標準増減額")), "bullish_change_amount": _number(row.get("強気増減額")),
                         "basis_note": row.get("根拠メモ") or "", "reference_url": row.get("参考URL") or "",
                         "information_date": str(row.get("情報取得日") or ""), "display_order": int(_number(row.get("並び順")) or 0)})
        items = [{"name": str(row.get("売上項目名") or "").strip(),
                  "kpi_names": [name.strip() for name in str(row.get("KPI名（カンマ区切り）") or "").split(",") if name.strip()],
                  "display_order": int(_number(row.get("並び順")) or 0)} for row in _records(edited_items) if str(row.get("売上項目名") or "").strip()]
        if st.button("KPIと売上項目を保存", type="primary", icon=":material/save:"):
            save_project_mvp_data(project_id, kpi={**kpi_data, "kpis": kpis, "revenue_items": items})
            st.success("KPIと売上項目を保存しました。")
            st.rerun()
    if not kpis or not items:
        st.info("編集モードでKPIと売上項目を追加すると、3シナリオの売上予測が表示されます。")
        return
    result = calculate_forecast(kpis, items)
    rows = []
    for item in result["items"]:
        rows.append({"売上項目": item["name"], "基準売上": item["base"], "弱気売上": item["bearish"],
                     "標準売上": item["standard"], "強気売上": item["bullish"],
                     "標準増収額": item["standard_increase"], "標準成長率（%）": item["standard_growth_rate"]})
    forecast_frame = pd.DataFrame(rows)
    st.dataframe(forecast_frame, hide_index=True)
    totals = result["totals"]
    with st.container(horizontal=True):
        for scenario in SCENARIOS:
            st.metric(f"{SCENARIO_LABELS[scenario]}売上", "―" if totals[scenario] is None else f"{totals[scenario]:,.2f}", border=True)
    contribution = contribution_analysis(kpis, items, "standard")
    contribution_frame = pd.DataFrame([{"KPI": row["kpi_name"], "増収寄与": row["contribution"]} for row in contribution["contributions"]] + [{"KPI": "相乗効果", "増収寄与": contribution["synergy"]}])
    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.markdown("**弱気・標準・強気の売上比較**")
        _bar_chart(pd.DataFrame({"シナリオ": [SCENARIO_LABELS[s] for s in SCENARIOS], "売上": [totals[s] for s in SCENARIOS]}), x="シナリオ", y="売上")
    with c2.container(border=True):
        st.markdown("**KPI別の増収寄与**")
        _bar_chart(contribution_frame, x="KPI", y="増収寄与")
    with st.container(border=True):
        st.markdown("**基準売上と標準予想売上の比較**")
        _bar_chart(
            pd.DataFrame({"区分": ["基準", "標準予想"], "売上": [totals["base"], totals["standard"]]}),
            x="区分",
            y="売上",
        )
    st.caption("寄与分析は、各KPIだけを基準値から予想値へ変えた簡易分解です。残差を相乗効果として表示します。")
    if edit_mode and totals.get("standard") is not None:
        with st.container(border=True):
            st.markdown("**標準シナリオをPL独自予想へ反映**")
            pl_records = get_pl_entries(project_id)
            own = [row for row in pl_records if row.get("result_type") == "自分予想"]
            target_year = st.text_input("独自予想の年度名", value=str(own[-1].get("fiscal_year")) if own else "独自予想")
            current = next((row.get("sales") for row in own if str(row.get("fiscal_year")) == target_year), None)
            difference = totals["standard"] - (_number(current) or 0)
            growth = None if _number(current) in (None, 0) else difference / _number(current) * 100
            st.write(f"現在：{_format(current)} ｜ KPI計算：{_format(totals['standard'])} ｜ 差額：{difference:,.2f} ｜ 増減率：{'―' if growth is None else f'{growth:.1f}%'}")
            method = st.selectbox("営業利益の計算方法", ["独自予想の営業利益率を入力", "増収分に限界利益率を掛ける", "営業利益を直接入力"])
            rate = st.number_input("営業利益率または限界利益率（%）", value=0.0)
            direct = st.number_input("営業利益の直接入力", value=0.0)
            basis = st.text_area("利益率・利益予測の根拠メモ", value=str(kpi_data.get("profit_method", {}).get("basis_note", "")))
            confirmed = st.checkbox("現在値と計算値を確認しました")
            if st.button("標準シナリオをPL独自予想へ反映", type="primary", disabled=not confirmed):
                updated = apply_kpi_sales_to_pl(pl_records, target_year, totals["standard"], method,
                                                operating_value=direct, margin_rate=rate, incremental_margin_rate=rate)
                save_pl_entries(project_id, updated)
                save_project_mvp_data(project_id, kpi={**kpi_data, "kpis": kpis, "revenue_items": items,
                                                       "profit_method": {"method": method, "rate": rate, "direct": direct, "basis_note": basis}})
                st.success("独自予想へ反映しました。会社予想は変更していません。")
    _download_row(forecast_frame, f"{project.get('stock_code') or 'project'}_kpi_forecast", "KPI予測")


def _catalyst_tab(project: dict, data: dict, edit_mode: bool) -> None:
    st.subheader("カタリスト")
    fields = [
        ("main", "本命カタリスト"), ("background", "カタリストが起きる背景"), ("timing", "いつ起きるか"),
        ("target_market", "対象市場"), ("beneficiary", "恩恵を受ける事業、セグメント"),
        ("flow", "カタリストが業績へ反映される流れ"), ("advantage", "会社側の競争優位性"),
        ("not_priced_in", "市場がまだ織り込んでいないと考える理由"), ("check_timing", "確認すべき時期"),
        ("check_metrics", "確認すべき指標"), ("failure", "失敗する条件"), ("counterargument", "反論"),
        ("reference_url", "参考URL"), ("information_date", "情報取得日"),
    ]
    catalyst = dict(data["catalyst"])
    kpi_names = [row.get("kpi_name") for row in data["kpi"].get("kpis", []) if row.get("kpi_name")]
    if edit_mode:
        with st.form(f"catalyst_{project['id']}"):
            edited = {}
            for key, label in fields:
                edited[key] = st.text_area(label, value=str(catalyst.get(key, "")), height=80)
            edited["affected_kpis"] = st.multiselect("影響を受けるKPI", kpi_names, default=[name for name in catalyst.get("affected_kpis", []) if name in kpi_names])
            if st.form_submit_button("カタリストを保存", type="primary", icon=":material/save:"):
                save_project_mvp_data(project["id"], catalyst=edited)
                st.success("カタリストを保存しました。")
                st.rerun()
    else:
        flow = [("背景", catalyst.get("background")), ("カタリスト", catalyst.get("main")),
                ("変化するKPI", "、".join(catalyst.get("affected_kpis", []))),
                ("予想売上", "KPI・売上予測タブを参照"), ("PLへの影響", catalyst.get("flow"))]
        for index, (label, value) in enumerate(flow):
            with st.container(border=True):
                st.markdown(f"**{label}**")
                st.write(value or "未入力")
            if index < len(flow) - 1:
                st.markdown(":material/arrow_downward:", text_alignment="center")
        with st.expander("根拠・反論・確認事項"):
            for key, label in fields:
                if key not in {"background", "main", "flow"}:
                    st.markdown(f"**{label}**")
                    st.write(catalyst.get(key) or "未入力")


def _memo_tab(project: dict, data: dict, edit_mode: bool) -> None:
    st.subheader("発表メモ")
    memo = dict(data["memo"])
    if edit_mode:
        with st.form(f"memo_{project['id']}"):
            edited = {key: st.text_area(label, value=str(memo.get(key, "")), height=120 if key in {"presentation_script", "answer_notes"} else 80) for key, label in MEMO_FIELDS}
            if st.form_submit_button("発表メモを保存", type="primary", icon=":material/save:"):
                save_project_mvp_data(project["id"], memo=edited)
                st.success("発表メモを保存しました。")
                st.rerun()
    else:
        for key, label in MEMO_FIELDS:
            if memo.get(key):
                with st.container(border=True):
                    st.markdown(f"**{label}**")
                    st.write(memo[key])
        if not any(memo.values()):
            st.info("編集モードで発表メモを入力できます。")


def _sidebar() -> tuple[dict | None, bool]:
    projects = list_projects()
    labels = {
        row["id"]: f"{row['project_name']}（{row.get('stock_code') or 'コード未設定'} / ID {row['id']}）"
        for row in projects
    }
    ids = list(labels)
    selected_default = st.session_state.get("mvp_project_id")
    if selected_default not in ids:
        selected_default = ids[0] if ids else None
    st.sidebar.title("銘柄発表")
    selected = None
    if ids:
        selected = st.sidebar.selectbox(
            "既存プロジェクト選択",
            ids,
            index=ids.index(selected_default) if selected_default in ids else 0,
            format_func=lambda value: labels.get(value, "未選択"),
        )
    else:
        st.sidebar.info("プロジェクトがありません。下から新規作成してください。")
    st.session_state["mvp_project_id"] = selected
    with st.sidebar.expander("新規プロジェクト作成", icon=":material/add:"):
        name = st.text_input("新しいプロジェクト名", key="new_mvp_project")
        normalized_name = unicodedata.normalize("NFKC", name).strip().casefold()
        duplicates = [
            row for row in projects
            if unicodedata.normalize("NFKC", str(row.get("project_name") or "")).strip().casefold() == normalized_name
        ] if normalized_name else []
        allow_duplicate = False
        if duplicates:
            st.warning(f"同名プロジェクトが{len(duplicates)}件あります。既存プロジェクトの選択を推奨します。")
            allow_duplicate = st.checkbox("同名でも新規作成する", key="allow_duplicate_mvp_project")
        if st.button("作成", type="primary", key="create_mvp_project"):
            if not name.strip():
                st.error("プロジェクト名を入力してください。")
            elif duplicates and not allow_duplicate:
                st.error("同名プロジェクトを確認し、必要な場合だけチェックを付けてください。")
            else:
                project_id = save_project({"project_name": name.strip()})
                st.session_state["mvp_project_id"] = project_id
                st.rerun()
    with st.sidebar.expander("データ保存先", icon=":material/database:"):
        st.code(str(DEFAULT_DB_PATH.resolve()), language=None)
        st.caption("ローカル版とStreamlit公開版のSQLiteは別環境で、自動同期されません。")
        report = get_migration_report()
        if report:
            st.caption(
                f"最終互換移行：新規{report.get('projects_created', 0)}件、更新{report.get('projects_updated', 0)}件、"
                f"補完{report.get('fields_filled', 0)}項目。旧PL {report.get('pl_rows_reused', 0)}件・"
                f"旧セグメント {report.get('segment_rows_reused', 0)}件を継続利用。"
            )
    edit_mode = st.sidebar.segmented_control("モード", ["閲覧", "編集"], default="閲覧", key="mvp_mode") == "編集"
    if selected is not None:
        current = get_project(selected)
        rename = st.sidebar.text_input("プロジェクト名変更", value=current.get("project_name", ""), key=f"rename_{selected}")
        with st.sidebar.container(horizontal=True):
            if st.button("保存", type="primary", icon=":material/save:", key=f"sidebar_save_{selected}"):
                try:
                    rename_project(selected, rename)
                    st.success("プロジェクト名を保存しました。")
                    st.rerun()
                except ValueError as exc:
                    st.error(str(exc))
            if st.button("削除", icon=":material/delete:", key=f"sidebar_delete_{selected}"):
                st.session_state[f"confirm_delete_{selected}"] = True
        if st.session_state.get(f"confirm_delete_{selected}"):
            st.sidebar.warning("削除すると、このプロジェクトに属するデータも削除されます。")
            if st.sidebar.button("削除を確定", type="primary", key=f"confirm_delete_button_{selected}"):
                delete_project(selected)
                st.session_state["mvp_project_id"] = None
                st.rerun()
        return get_project(selected), edit_mode
    return None, edit_mode


def render_app() -> None:
    initialize_database()
    project, edit_mode = _sidebar()
    st.title("銘柄発表")
    st.caption("会社情報、PL、セグメント、KPI予測、カタリスト、発表メモを銘柄ごとに管理します。")
    if not project:
        st.info("サイドバーから新規プロジェクトを作成してください。")
        return
    st.markdown(f"**{project.get('company_name') or project.get('project_name')}（{project.get('stock_code') or 'コード未設定'}）**　:gray-badge[{'編集モード' if edit_mode else '閲覧モード'}]")
    data = get_project_mvp_data(project["id"])
    overview_tab, pl_tab, segment_tab, kpi_tab, catalyst_tab, memo_tab = st.tabs(
        ["銘柄概要", "PL", "セグメント", "KPI・売上予測", "カタリスト", "発表メモ"]
    )
    with overview_tab:
        _overview_tab(project, data, edit_mode)
    with pl_tab:
        _pl_tab(project, data, edit_mode)
    with segment_tab:
        _segment_tab(project, data, edit_mode)
    with kpi_tab:
        _kpi_tab(project, data, edit_mode)
    with catalyst_tab:
        _catalyst_tab(project, data, edit_mode)
    with memo_tab:
        _memo_tab(project, data, edit_mode)
