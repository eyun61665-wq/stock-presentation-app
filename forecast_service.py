"""会社予想とカタリストを、自分予想へ安全に反映する処理。"""
from __future__ import annotations

from typing import Any

from financial_tables import PL_RAW_ITEMS, normalize_result_type


RESULT_ORDER = {"実績": 0, "会社予想": 1, "自分予想": 2}


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def catalyst_base(entries: list[dict], target_year: str) -> tuple[dict | None, str]:
    """同年度の会社予想、なければ最新実績を反映元にする。"""
    year = str(target_year).strip()
    company = next(
        (
            row for row in entries
            if str(row.get("fiscal_year", "")).strip() == year
            and normalize_result_type(row.get("result_type")) == "会社予想"
            and _number(row.get("sales")) is not None
        ),
        None,
    )
    if company:
        return company, f"{year} 会社予想"
    actuals = [
        row for row in entries
        if normalize_result_type(row.get("result_type")) == "実績"
        and _number(row.get("sales")) is not None
    ]
    if actuals:
        latest = max(actuals, key=lambda row: str(row.get("fiscal_year", "")))
        return latest, f"{latest.get('fiscal_year', '')} 実績"
    return None, "反映元なし"


def apply_catalyst_to_self_forecast(
    entries: list[dict],
    target_year: str,
    additional_sales: float,
    default_shares: float | None = None,
) -> tuple[list[dict], float | None, str]:
    """既存行を壊さず、対象年度の自分予想売上だけを更新する。"""
    year = str(target_year).strip()
    base, base_label = catalyst_base(entries, year)
    base_sales = _number((base or {}).get("sales"))
    if not year or base_sales is None:
        return [dict(row) for row in entries], None, base_label

    rows = [dict(row) for row in entries]
    self_row = next(
        (
            row for row in rows
            if str(row.get("fiscal_year", "")).strip() == year
            and normalize_result_type(row.get("result_type")) == "自分予想"
        ),
        None,
    )
    if self_row is None:
        self_row = {
            "fiscal_year": year,
            "result_type": "自分予想",
            **{key: None for key, _ in PL_RAW_ITEMS},
        }
        rows.append(self_row)

    self_row["sales"] = base_sales + float(additional_sales)
    if _number(self_row.get("shares_outstanding")) is None:
        base_shares = _number((base or {}).get("shares_outstanding"))
        self_row["shares_outstanding"] = base_shares if base_shares is not None else _number(default_shares)
    self_row.setdefault("source", "自分の仮定（カタリスト反映）")
    self_row.setdefault("basis_date", "")
    rows.sort(key=lambda row: (
        str(row.get("fiscal_year", "")),
        RESULT_ORDER.get(normalize_result_type(row.get("result_type")), 9),
    ))
    return rows, base_sales, base_label
