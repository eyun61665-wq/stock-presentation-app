"""PL・セグメントを年度横並びで編集・表示するための変換処理。"""
from __future__ import annotations

from typing import Any

import pandas as pd


RESULT_TYPES = ["実績", "会社予想", "自分予想"]

PL_RAW_ITEMS = [
    ("sales", "売上高（百万円）"),
    ("cost_of_sales", "売上原価（百万円）"),
    ("gross_profit", "売上総利益（百万円）"),
    ("sga_expenses", "販管費（百万円）"),
    ("operating_profit", "営業利益（百万円）"),
    ("non_operating_income", "営業外収益（百万円）"),
    ("non_operating_expenses", "営業外費用（百万円）"),
    ("ordinary_profit", "経常利益（百万円）"),
    ("extraordinary_income", "特別利益（百万円）"),
    ("extraordinary_loss", "特別損失（百万円）"),
    ("pretax_profit", "税引前利益（百万円）"),
    ("income_taxes", "法人税等（百万円）"),
    ("net_income", "親会社株主に帰属する当期純利益（百万円）"),
    ("shares_outstanding", "平均株式数（百万株）"),
]

PL_LABEL_TO_KEY = {label: key for key, label in PL_RAW_ITEMS}

PL_DISPLAY_ITEMS = [
    ("sales", "売上高", "amount"),
    ("sales_growth", "売上成長率", "percent"),
    ("cost_of_sales", "売上原価", "amount"),
    ("gross_profit", "売上総利益", "amount"),
    ("gross_margin", "売上高総利益率", "percent"),
    ("sga_expenses", "販管費", "amount"),
    ("sga_ratio", "販管費率", "percent"),
    ("operating_profit", "営業利益", "amount"),
    ("operating_margin", "営業利益率", "percent"),
    ("non_operating_income", "営業外収益", "amount"),
    ("non_operating_expenses", "営業外費用", "amount"),
    ("ordinary_profit", "経常利益", "amount"),
    ("ordinary_margin", "経常利益率", "percent"),
    ("extraordinary_income", "特別利益", "amount"),
    ("extraordinary_loss", "特別損失", "amount"),
    ("pretax_profit", "税引前利益", "amount"),
    ("income_taxes", "法人税等", "amount"),
    ("tax_rate", "法人税等合計率", "percent"),
    ("net_income", "親会社株主に帰属する当期純利益", "amount"),
    ("net_margin", "純利益率", "percent"),
    ("net_growth", "純利益前年比", "percent"),
    ("eps", "EPS（円）", "eps"),
    ("shares_outstanding", "平均株式数（百万株）", "shares"),
]


def clean_number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        if pd.isna(value):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_result_type(value: Any) -> str:
    return str(value) if value in RESULT_TYPES else "実績"


def column_title(fiscal_year: Any, result_type: Any) -> str:
    return f"{str(fiscal_year).strip()}｜{normalize_result_type(result_type)}"


def metadata_from_entries(entries: list[dict]) -> pd.DataFrame:
    rows = [
        {"年度": row.get("fiscal_year", ""), "区分": normalize_result_type(row.get("result_type"))}
        for row in entries
        if str(row.get("fiscal_year", "")).strip()
    ]
    return pd.DataFrame(rows, columns=["年度", "区分"])


def normalize_metadata(frame: pd.DataFrame) -> list[dict]:
    rows = []
    seen: set[tuple[str, str]] = set()
    for row in frame.where(pd.notnull(frame), None).to_dict("records"):
        year = str(row.get("年度") or "").strip()
        result_type = normalize_result_type(row.get("区分"))
        key = (year, result_type)
        if not year or key in seen:
            continue
        seen.add(key)
        rows.append({"fiscal_year": year, "result_type": result_type})
    return rows


def pl_input_frame(entries: list[dict], metadata: list[dict]) -> pd.DataFrame:
    by_key = {
        (str(row.get("fiscal_year")), normalize_result_type(row.get("result_type"))): row
        for row in entries
    }
    rows = []
    for key, label in PL_RAW_ITEMS:
        output = {"科目": label}
        for meta in metadata:
            year = meta["fiscal_year"]
            entry = by_key.get((year, meta["result_type"]), {})
            output[column_title(year, meta["result_type"])] = entry.get(key)
        rows.append(output)
    return pd.DataFrame(rows)


def pl_records_from_frame(frame: pd.DataFrame, metadata: list[dict]) -> list[dict]:
    by_label = {
        str(row.get("科目")): row
        for row in frame.where(pd.notnull(frame), None).to_dict("records")
    }
    records = []
    for meta in metadata:
        title = column_title(meta["fiscal_year"], meta["result_type"])
        record = {"fiscal_year": meta["fiscal_year"], "result_type": meta["result_type"]}
        for key, label in PL_RAW_ITEMS:
            record[key] = clean_number(by_label.get(label, {}).get(title))
        records.append(record)
    return records


def _safe_ratio(numerator: float | None, denominator: float | None) -> float | None:
    return None if numerator is None or denominator in (None, 0) else numerator / denominator * 100


def calculate_pl_records(records: list[dict]) -> list[dict]:
    calculated = []
    previous_by_type: dict[str, dict[str, float | None]] = {}
    last_actual: dict[str, float | None] = {"sales": None, "net_income": None}
    for raw in records:
        row = {key: clean_number(raw.get(key)) for key, _ in PL_RAW_ITEMS}
        row.update({"fiscal_year": raw.get("fiscal_year", ""), "result_type": normalize_result_type(raw.get("result_type"))})
        previous = previous_by_type.get(row["result_type"], last_actual)
        previous_sales = previous.get("sales")
        previous_net = previous.get("net_income")
        if row["gross_profit"] is None and row["sales"] is not None and row["cost_of_sales"] is not None:
            row["gross_profit"] = row["sales"] - row["cost_of_sales"]
        if row["sga_expenses"] is None and row["gross_profit"] is not None and row["operating_profit"] is not None:
            row["sga_expenses"] = row["gross_profit"] - row["operating_profit"]
        if (row["ordinary_profit"] is None and row["operating_profit"] is not None
                and row["non_operating_income"] is not None and row["non_operating_expenses"] is not None):
            row["ordinary_profit"] = row["operating_profit"] + row["non_operating_income"] - row["non_operating_expenses"]
        if (row["pretax_profit"] is None and row["ordinary_profit"] is not None
                and row["extraordinary_income"] is not None and row["extraordinary_loss"] is not None):
            row["pretax_profit"] = row["ordinary_profit"] + row["extraordinary_income"] - row["extraordinary_loss"]
        row["sales_growth"] = _safe_ratio(
            None if previous_sales is None or row["sales"] is None else row["sales"] - previous_sales,
            previous_sales,
        )
        row["gross_margin"] = _safe_ratio(row["gross_profit"], row["sales"])
        row["sga_ratio"] = _safe_ratio(row["sga_expenses"], row["sales"])
        row["operating_margin"] = _safe_ratio(row["operating_profit"], row["sales"])
        row["ordinary_margin"] = _safe_ratio(row["ordinary_profit"], row["sales"])
        row["tax_rate"] = _safe_ratio(row["income_taxes"], row["pretax_profit"])
        row["net_margin"] = _safe_ratio(row["net_income"], row["sales"])
        row["net_growth"] = _safe_ratio(
            None if previous_net is None or row["net_income"] is None else row["net_income"] - previous_net,
            previous_net,
        )
        row["eps"] = None if row["net_income"] is None or row["shares_outstanding"] in (None, 0) else row["net_income"] / row["shares_outstanding"]
        calculated.append(row)
        previous_by_type[row["result_type"]] = {
            "sales": row["sales"] if row["sales"] is not None else previous_sales,
            "net_income": row["net_income"] if row["net_income"] is not None else previous_net,
        }
        if row["result_type"] == "実績":
            last_actual = dict(previous_by_type["実績"])
    return calculated


def _format_value(value: Any, kind: str) -> str:
    number = clean_number(value)
    if number is None:
        return ""
    if kind == "percent":
        return f"{number:,.1f}%"
    if kind == "eps":
        return f"{number:,.2f}"
    if kind == "shares":
        return f"{number:,.3f}"
    return f"{number:,.0f}"


def pl_display_frame(records: list[dict]) -> pd.DataFrame:
    calculated = calculate_pl_records(records)
    rows = []
    for key, label, kind in PL_DISPLAY_ITEMS:
        output = {"科目": label}
        for record in calculated:
            output[column_title(record["fiscal_year"], record["result_type"])] = _format_value(record.get(key), kind)
        rows.append(output)
    return pd.DataFrame(rows)


def segment_names_from_entries(entries: list[dict]) -> list[str]:
    return list(dict.fromkeys(str(row.get("segment_name", "")).strip() for row in entries if str(row.get("segment_name", "")).strip()))


def segment_input_frame(entries: list[dict], metadata: list[dict], segment_names: list[str], value_key: str) -> pd.DataFrame:
    lookup = {
        (str(row.get("fiscal_year")), normalize_result_type(row.get("result_type")), str(row.get("segment_name"))): row
        for row in entries
    }
    rows = []
    for segment_name in segment_names:
        output = {"セグメント": segment_name}
        for meta in metadata:
            entry = lookup.get((meta["fiscal_year"], meta["result_type"], segment_name), {})
            output[column_title(meta["fiscal_year"], meta["result_type"])] = entry.get(value_key)
        rows.append(output)
    return pd.DataFrame(rows)


def segment_records_from_frames(
    sales_frame: pd.DataFrame,
    profit_frame: pd.DataFrame,
    metadata: list[dict],
    source: str = "手入力",
    note: str = "",
) -> list[dict]:
    sales_rows = {str(row.get("セグメント")): row for row in sales_frame.where(pd.notnull(sales_frame), None).to_dict("records")}
    profit_rows = {str(row.get("セグメント")): row for row in profit_frame.where(pd.notnull(profit_frame), None).to_dict("records")}
    names = list(dict.fromkeys([*sales_rows, *profit_rows]))
    records = []
    for meta in metadata:
        title = column_title(meta["fiscal_year"], meta["result_type"])
        for name in names:
            if not name.strip():
                continue
            records.append({
                "fiscal_year": meta["fiscal_year"],
                "result_type": meta["result_type"],
                "segment_name": name,
                "sales": clean_number(sales_rows.get(name, {}).get(title)),
                "operating_profit": clean_number(profit_rows.get(name, {}).get(title)),
                "source": source or "手入力",
                "note": note,
            })
    return records


def segment_display_frame(records: list[dict], metadata: list[dict], value_key: str) -> pd.DataFrame:
    names = segment_names_from_entries(records)
    lookup = {
        (str(row.get("fiscal_year")), normalize_result_type(row.get("result_type")), str(row.get("segment_name"))): clean_number(row.get(value_key))
        for row in records
    }
    rows = []
    total_row = {"セグメント": "合計"}
    for meta in metadata:
        values = [lookup.get((meta["fiscal_year"], meta["result_type"], name)) for name in names]
        total = sum(value for value in values if value is not None) if any(value is not None for value in values) else None
        total_row[column_title(meta["fiscal_year"], meta["result_type"])] = _format_value(total, "amount")
    rows.append(total_row)
    for name in names:
        output = {"セグメント": name}
        for meta in metadata:
            output[column_title(meta["fiscal_year"], meta["result_type"])] = _format_value(
                lookup.get((meta["fiscal_year"], meta["result_type"], name)), "amount"
            )
        rows.append(output)
    return pd.DataFrame(rows)


def segment_combined_input_frame(
    entries: list[dict], metadata: list[dict], segment_names: list[str]
) -> pd.DataFrame:
    """売上高と利益を1つの編集表にまとめる。"""
    sales = segment_input_frame(entries, metadata, segment_names, "sales")
    profit = segment_input_frame(entries, metadata, segment_names, "operating_profit")
    sales_rows = {str(row["セグメント"]): row for row in sales.to_dict("records")}
    profit_rows = {str(row["セグメント"]): row for row in profit.to_dict("records")}
    rows = []
    for name in segment_names:
        for item, source in (("売上高", sales_rows.get(name, {})), ("利益", profit_rows.get(name, {}))):
            row = {"セグメント": name, "項目": item}
            for meta in metadata:
                title = column_title(meta["fiscal_year"], meta["result_type"])
                row[title] = source.get(title)
            rows.append(row)
    return pd.DataFrame(rows)


def segment_records_from_combined_frame(
    frame: pd.DataFrame,
    metadata: list[dict],
    source: str = "手入力",
    note: str = "",
) -> list[dict]:
    """1つの編集表をSQLite保存用レコードへ戻す。"""
    rows = frame.where(pd.notnull(frame), None).to_dict("records")
    by_name_item = {
        (str(row.get("セグメント", "")), str(row.get("項目", ""))): row
        for row in rows
    }
    names = list(dict.fromkeys(str(row.get("セグメント", "")).strip() for row in rows if str(row.get("セグメント", "")).strip()))
    records = []
    for meta in metadata:
        title = column_title(meta["fiscal_year"], meta["result_type"])
        for name in names:
            records.append({
                "fiscal_year": meta["fiscal_year"],
                "result_type": meta["result_type"],
                "segment_name": name,
                "sales": clean_number(by_name_item.get((name, "売上高"), {}).get(title)),
                "operating_profit": clean_number(by_name_item.get((name, "利益"), {}).get(title)),
                "source": source or "手入力",
                "note": note,
            })
    return records


def segment_totals_frame(records: list[dict], metadata: list[dict]) -> pd.DataFrame:
    """年度別の売上高・利益合計だけを小さな表で返す。"""
    rows = []
    for label, key in (("売上高合計", "sales"), ("利益合計", "operating_profit")):
        display = segment_display_frame(records, metadata, key)
        total = display.iloc[0].to_dict() if not display.empty else {"セグメント": "合計"}
        total["セグメント"] = label
        rows.append(total)
    return pd.DataFrame(rows)


def _metric_value(value: Any, unit: str) -> str:
    number = clean_number(value)
    if number is None:
        return ""
    if unit == "%":
        return f"{number:,.1f}%"
    if unit in ("円", "倍"):
        return f"{number:,.2f}"
    return f"{number:,.0f}"


def segment_analysis_frame(
    records: list[dict],
    metadata: list[dict],
    metrics: list[dict] | None = None,
) -> pd.DataFrame:
    """年度を横、セグメントとKPIを縦にした完成表を返す。"""
    metrics = metrics or []
    names = segment_names_from_entries(records)
    lookup = {
        (
            str(row.get("fiscal_year")), normalize_result_type(row.get("result_type")),
            str(row.get("segment_name")),
        ): row
        for row in records
    }
    metric_lookup = {
        (
            str(row.get("fiscal_year")), normalize_result_type(row.get("result_type")),
            str(row.get("row_label")),
        ): row
        for row in metrics
    }
    metric_labels = list(dict.fromkeys(
        str(row.get("row_label", "")).strip()
        for row in sorted(metrics, key=lambda item: (int(item.get("display_order", 0) or 0), str(item.get("row_label", ""))))
        if str(row.get("row_label", "")).strip()
    ))

    rows: list[dict[str, Any]] = []
    total_sales = {"科目": "売上高（百万円）"}
    for meta in metadata:
        values = [
            clean_number(lookup.get((meta["fiscal_year"], meta["result_type"], name), {}).get("sales"))
            for name in names
        ]
        total = sum(value for value in values if value is not None) if any(value is not None for value in values) else None
        total_sales[column_title(meta["fiscal_year"], meta["result_type"])] = _format_value(total, "amount")
    rows.append(total_sales)

    for index, name in enumerate(names, start=1):
        row = {"科目": f"{index}：{name}"}
        for meta in metadata:
            value = lookup.get((meta["fiscal_year"], meta["result_type"], name), {}).get("sales")
            row[column_title(meta["fiscal_year"], meta["result_type"])] = _format_value(value, "amount")
        rows.append(row)

    for label in metric_labels:
        unit = next((str(row.get("unit") or "") for row in metrics if str(row.get("row_label")) == label), "")
        row = {"科目": label + (f"（{unit}）" if unit else "")}
        for meta in metadata:
            item = metric_lookup.get((meta["fiscal_year"], meta["result_type"], label), {})
            row[column_title(meta["fiscal_year"], meta["result_type"])] = _metric_value(item.get("value"), unit)
        rows.append(row)

    if any(clean_number(row.get("operating_profit")) is not None for row in records):
        total_profit = {"科目": "セグメント利益（百万円）"}
        for meta in metadata:
            values = [
                clean_number(lookup.get((meta["fiscal_year"], meta["result_type"], name), {}).get("operating_profit"))
                for name in names
            ]
            total = sum(value for value in values if value is not None) if any(value is not None for value in values) else None
            total_profit[column_title(meta["fiscal_year"], meta["result_type"])] = _format_value(total, "amount")
        rows.append(total_profit)
        for index, name in enumerate(names, start=1):
            row = {"科目": f"{index}：{name} 利益"}
            for meta in metadata:
                value = lookup.get((meta["fiscal_year"], meta["result_type"], name), {}).get("operating_profit")
                row[column_title(meta["fiscal_year"], meta["result_type"])] = _format_value(value, "amount")
            rows.append(row)
    return pd.DataFrame(rows)


def segment_metrics_input_frame(metrics: list[dict], metadata: list[dict]) -> pd.DataFrame:
    """任意KPIを年度横並びで編集するための表を返す。"""
    labels = list(dict.fromkeys(
        str(row.get("row_label", "")).strip() for row in metrics
        if str(row.get("row_label", "")).strip()
    ))
    lookup = {
        (
            str(row.get("fiscal_year")), normalize_result_type(row.get("result_type")),
            str(row.get("row_label")),
        ): row
        for row in metrics
    }
    rows = []
    for label in labels:
        sample = next((row for row in metrics if str(row.get("row_label")) == label), {})
        output = {"KPI": label, "単位": sample.get("unit", "")}
        for meta in metadata:
            output[column_title(meta["fiscal_year"], meta["result_type"])] = lookup.get(
                (meta["fiscal_year"], meta["result_type"], label), {}
            ).get("value")
        rows.append(output)
    return pd.DataFrame(rows)


def segment_metrics_from_frame(
    frame: pd.DataFrame,
    metadata: list[dict],
    source: str = "手入力",
) -> list[dict]:
    records = []
    for order, row in enumerate(frame.where(pd.notnull(frame), None).to_dict("records")):
        label = str(row.get("KPI") or "").strip()
        if not label:
            continue
        for meta in metadata:
            value = clean_number(row.get(column_title(meta["fiscal_year"], meta["result_type"])))
            if value is None:
                continue
            records.append({
                "fiscal_year": meta["fiscal_year"],
                "result_type": meta["result_type"],
                "row_label": label,
                "value": value,
                "unit": str(row.get("単位") or ""),
                "display_order": order,
                "source": source,
                "note": "",
            })
    return records


def color_columns(frame: pd.DataFrame, metadata: list[dict]) -> pd.io.formats.style.Styler:
    """実績・会社予想・自分予想をモデル表らしく色分けする。"""
    colors = {"実績": "#f8dede", "会社予想": "#fff1c2", "自分予想": "#dcecf8"}
    ratio_rows = {
        "売上成長率", "売上高総利益率", "販管費率", "営業利益率",
        "経常利益率", "法人税等合計率", "純利益率", "純利益前年比",
    }
    key_rows = {
        "売上高", "売上総利益", "営業利益", "経常利益", "税引前利益",
        "親会社株主に帰属する当期純利益", "EPS（円）", "合計",
    }
    style = frame.style.set_properties(**{"text-align": "right"})
    first_column = frame.columns[0]
    style = style.set_properties(subset=[first_column], **{"text-align": "left"})
    if first_column in frame:
        ratio_index = frame.index[frame[first_column].isin(ratio_rows)].tolist()
        key_index = frame.index[frame[first_column].isin(key_rows)].tolist()
        if ratio_index:
            style = style.set_properties(subset=pd.IndexSlice[ratio_index, :], **{"color": "#1769aa"})
        if key_index:
            style = style.set_properties(subset=pd.IndexSlice[key_index, :], **{"font-weight": "700"})
    for meta in metadata:
        column = column_title(meta["fiscal_year"], meta["result_type"])
        if column in frame.columns:
            style = style.set_properties(subset=[column], **{"background-color": colors[meta["result_type"]]})
    return style
