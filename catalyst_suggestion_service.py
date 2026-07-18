"""保存済みの会社情報・PL・セグメントからカタリスト候補を組み立てる。"""
from __future__ import annotations

import re
from typing import Any

from calculations import catalyst_sales_from_rate


def _number(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def next_fiscal_year(value: str) -> str:
    """年度表記の先頭4桁だけを1年進め、月日表記は維持する。"""
    text = str(value or "").strip()
    match = re.search(r"(?:19|20)\d{2}", text)
    if not match:
        return "翌年度"
    next_year = str(int(match.group()) + 1)
    return f"{text[:match.start()]}{next_year}{text[match.end():]}"


def _impact(reference_sales: float, rates: tuple[float, float, float]) -> tuple[float, float, float]:
    return tuple(catalyst_sales_from_rate(reference_sales, rate) for rate in rates)


def _proposal(
    name: str,
    rationale: str,
    reference_sales: float,
    reference_label: str,
    rates: tuple[float, float, float],
    target_year: str,
    confidence: str,
) -> dict:
    low, standard, high = _impact(reference_sales, rates)
    return {
        "name": name,
        "rationale": rationale,
        "reference_sales": reference_sales,
        "reference_label": reference_label,
        "low_rate": rates[0],
        "standard_rate": rates[1],
        "high_rate": rates[2],
        "low_impact": low,
        "standard_impact": standard,
        "high_impact": high,
        "target_year": target_year,
        "confidence": confidence,
        "formula": f"{reference_label} {reference_sales:,.0f}百万円 × 影響率 {rates[1]:g}％",
        "assumption": (
            f"弱気{rates[0]:g}％・標準{rates[1]:g}％・強気{rates[2]:g}％で試算。"
            "会社開示ではなく、保存済みデータから置いた検討用の仮定です。"
        ),
    }


def suggest_catalysts(
    project: dict,
    pl_entries: list[dict],
    segment_entries: list[dict],
    limit: int = 4,
) -> list[dict]:
    """根拠が追える候補だけを返し、外部事実や未確認数値は作らない。"""
    actuals = [
        row for row in pl_entries
        if str(row.get("result_type", "実績")) == "実績" and _number(row.get("sales")) is not None
    ]
    if not actuals:
        return []
    latest_actual = max(actuals, key=lambda row: str(row.get("fiscal_year", "")))
    company_sales = float(latest_actual["sales"])
    fiscal_year = str(latest_actual.get("fiscal_year", ""))
    target_year = next_fiscal_year(fiscal_year)
    proposals: list[dict] = []

    segment_actuals = [
        row for row in segment_entries
        if str(row.get("result_type", "実績")) == "実績" and _number(row.get("sales")) is not None
    ]
    if segment_actuals:
        latest_segment_year = max(str(row.get("fiscal_year", "")) for row in segment_actuals)
        latest_segments = sorted(
            [row for row in segment_actuals if str(row.get("fiscal_year", "")) == latest_segment_year],
            key=lambda row: float(row.get("sales") or 0),
            reverse=True,
        )
        for row in latest_segments[:2]:
            segment_name = str(row.get("segment_name") or "主要セグメント")
            segment_sales = float(row["sales"])
            proposals.append(_proposal(
                f"{segment_name}の売上拡大",
                f"最新の{segment_name}売上 {segment_sales:,.0f}百万円を基準に、販売数量・受注・市場シェアの上振れを試算します。",
                segment_sales,
                f"{segment_name}売上",
                (2.0, 5.0, 10.0),
                target_year,
                "中（セグメント実績あり）",
            ))

    text = " ".join(str(project.get(key) or "") for key in (
        "business_description", "strengths", "investment_thesis", "catalysts", "sector17", "sector33"
    )).lower()
    themed: list[tuple[str, str, tuple[float, float, float]]] = []
    if any(word in text for word in ("海外", "輸出", "グローバル", "international", "overseas")):
        themed.append(("海外販売・販路の拡大", "事業内容に海外展開の記載があるため、地域拡大による増収余地を検討します。", (1.0, 3.0, 6.0)))
    if any(word in text for word in ("開発", "技術", "製品", "製造", "光学", "半導体", "電子")):
        themed.append(("新製品・高付加価値品の拡販", "事業内容・強みにある製品開発や技術力を、売上拡大へ結び付けるケースです。", (1.0, 4.0, 8.0)))
    if any(word in text for word in ("サービス", "保守", "saas", "サブスク", "ストック")):
        themed.append(("サービス・継続収入の拡大", "サービスや継続収入の積み上がりを全社売上に対する影響率で試算します。", (1.0, 2.5, 5.0)))
    themed.extend([
        ("新規顧客・販売地域の拡大", "既存製品の新規顧客獲得や販売地域拡大による増収余地を検討します。", (1.0, 3.0, 5.0)),
        ("価格改定・製品ミックス改善", "値上げや高単価品比率の上昇が売上高へ与える影響を試算します。", (0.5, 1.5, 3.0)),
    ])
    existing_names = {row["name"] for row in proposals}
    for name, rationale, rates in themed:
        if name in existing_names or len(proposals) >= limit:
            continue
        proposals.append(_proposal(
            name,
            rationale,
            company_sales,
            f"{fiscal_year} 全社売上",
            rates,
            target_year,
            "低（仮定ベース）",
        ))
        existing_names.add(name)
    return proposals[:limit]
