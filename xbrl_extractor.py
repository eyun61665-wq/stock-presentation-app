"""EDINET XBRLの標準タグを安全に抽出する。連結・当期を優先し、拡張タグは推測しない。"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from xml.etree import ElementTree as ET

from data_normalizer import to_million_shares, to_million_yen


TAGS = {
    "sales": ["NetSales", "Revenue", "RevenueIFRS", "OperatingRevenue1"],
    "cost_of_sales": ["CostOfSales", "CostOfRevenue"],
    "gross_profit": ["GrossProfit"],
    "sga_expenses": ["SellingGeneralAndAdministrativeExpenses"],
    "operating_profit": ["OperatingIncome", "OperatingProfit", "ProfitLossFromOperatingActivities"],
    "non_operating_income": ["NonOperatingIncome"],
    "non_operating_expenses": ["NonOperatingExpenses"],
    "ordinary_profit": ["OrdinaryIncome"],
    "extraordinary_income": ["ExtraordinaryIncome"],
    "extraordinary_loss": ["ExtraordinaryLoss"],
    "pretax_profit": ["IncomeBeforeIncomeTaxes", "ProfitLossBeforeIncomeTaxes"],
    "income_taxes": ["IncomeTaxes", "IncomeTaxExpenseContinuingOperations"],
    "net_income": ["ProfitLossAttributableToOwnersOfParent", "ProfitLoss"],
    "eps": ["BasicEarningsPerShare", "BasicEarningsLossPerShare"],
    "issued_shares": ["NumberOfIssuedShares"],
    "treasury_shares": ["NumberOfTreasuryShares"],
    "average_shares": ["AverageNumberOfShares", "WeightedAverageNumberOfSharesOutstanding"],
    "total_assets": ["Assets"],
    "net_assets": ["NetAssets", "Equity"],
    "operating_cf": ["NetCashProvidedByUsedInOperatingActivities"],
    "investing_cf": ["NetCashProvidedByUsedInInvestingActivities"],
    "financing_cf": ["NetCashProvidedByUsedInFinancingActivities"],
}

SEGMENT_SALES_TAGS = (
    "RevenueFromExternalCustomers", "SalesToExternalCustomers", "NetSales",
    "Revenue", "RevenueIFRS",
)
SEGMENT_PROFIT_TAGS = (
    "SegmentProfitLoss", "OperatingIncome", "OperatingProfit",
    "ProfitLossFromOperatingActivities",
)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].split(":")[-1]


@dataclass
class ContextInfo:
    context_id: str
    consolidated: bool
    current: bool
    period_end: str | None
    members: list[tuple[str, str]]


def _contexts(root: ET.Element) -> dict[str, ContextInfo]:
    result: dict[str, ContextInfo] = {}
    for node in root.iter():
        if _local(node.tag) != "context":
            continue
        context_id = node.attrib.get("id", "")
        xml_text = ET.tostring(node, encoding="unicode")
        end = next((child.text for child in node.iter() if _local(child.tag) in {"endDate", "instant"} and child.text), None)
        members = [
            (_local(child.attrib.get("dimension", "")), _local(child.text or ""))
            for child in node.iter() if _local(child.tag) == "explicitMember" and child.text
        ]
        lower = f"{context_id} {xml_text}".lower()
        result[context_id] = ContextInfo(
            context_id=context_id,
            consolidated="nonconsolidated" not in lower and "個別" not in lower,
            current="current" in lower or "当期" in lower,
            period_end=end,
            members=members,
        )
    return result


def _label_map(archive: zipfile.ZipFile) -> dict[str, str]:
    """拡張タクソノミのメンバーIDを日本語ラベルへ対応する。"""
    labels: dict[str, str] = {}
    for name in archive.namelist():
        if "PublicDoc" not in name or not name.lower().endswith(".xml") or "lab" not in name.lower():
            continue
        try:
            root = ET.fromstring(archive.read(name))
        except ET.ParseError:
            continue
        resources = {
            node.attrib.get("{http://www.w3.org/1999/xlink}label", ""): " ".join((node.text or "").split())
            for node in root.iter() if _local(node.tag) == "label" and node.text
        }
        locators = {
            node.attrib.get("{http://www.w3.org/1999/xlink}label", ""): node.attrib.get("{http://www.w3.org/1999/xlink}href", "").split("#")[-1]
            for node in root.iter() if _local(node.tag) == "loc"
        }
        for arc in root.iter():
            if _local(arc.tag) != "labelArc":
                continue
            source = locators.get(arc.attrib.get("{http://www.w3.org/1999/xlink}from", ""), "")
            label = resources.get(arc.attrib.get("{http://www.w3.org/1999/xlink}to", ""), "")
            if source and label:
                labels[_local(source)] = label
    return labels


def _numeric(node: ET.Element) -> float | None:
    if not node.text:
        return None
    try:
        return float(node.text.replace(",", ""))
    except ValueError:
        return None


def _score_context(context: ContextInfo) -> tuple[int, str]:
    return (
        (4 if context.consolidated else 0)
        + (2 if context.current else 0)
        + (1 if not context.members else 0),
        context.period_end or "",
    )


def _is_segment_context(context: ContextInfo) -> bool:
    for dimension, member in context.members:
        dimension_lower = dimension.lower()
        member_lower = member.lower()
        if (
            any(hint in dimension_lower for hint in ("segment", "business", "service"))
            and not any(excluded in member_lower for excluded in ("adjustment", "elimination", "corporate", "consolidated"))
        ):
            return True
    return False


def _segment_name(context: ContextInfo, labels: dict[str, str]) -> str:
    for dimension, member in context.members:
        if any(hint in dimension.lower() for hint in ("segment", "business", "service")):
            return labels.get(member, re.sub(r"Member$", "", member))
    return ""


def extract_xbrl(zip_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        xbrl_names = [name for name in archive.namelist() if name.endswith(".xbrl") and "PublicDoc" in name]
        if not xbrl_names:
            raise ValueError("XBRL本文が見つかりませんでした。")
        roots = [ET.fromstring(archive.read(name)) for name in xbrl_names]
        labels = _label_map(archive)

    result = {key: None for key in TAGS}
    result["period"] = None
    result["segments"] = []
    candidates: dict[str, list[tuple[tuple[int, str], float]]] = {key: [] for key in TAGS}
    segment_values: dict[tuple[str, str], dict[str, float | None]] = {}

    for root in roots:
        contexts = _contexts(root)
        for node in root.iter():
            name = _local(node.tag)
            context = contexts.get(node.attrib.get("contextRef", ""))
            value = _numeric(node)
            if context is None or value is None:
                continue
            if context.period_end and (result["period"] is None or context.period_end > result["period"]):
                result["period"] = context.period_end
            if not context.members:
                for key, tags in TAGS.items():
                    if name in tags:
                        candidates[key].append((_score_context(context), value))
            if context.consolidated and context.current and _is_segment_context(context):
                segment_name = _segment_name(context, labels)
                if not segment_name:
                    continue
                period = context.period_end or ""
                row = segment_values.setdefault((period, segment_name), {"sales": None, "operating_profit": None})
                if name in SEGMENT_SALES_TAGS and row["sales"] is None:
                    row["sales"] = to_million_yen(value)
                if name in SEGMENT_PROFIT_TAGS and row["operating_profit"] is None:
                    row["operating_profit"] = to_million_yen(value)

    for key, values in candidates.items():
        if not values:
            continue
        raw = max(values, key=lambda pair: pair[0])[1]
        if key == "eps":
            result[key] = raw
        elif key in {"issued_shares", "treasury_shares", "average_shares"}:
            result[key] = to_million_shares(raw)
        else:
            result[key] = to_million_yen(raw)

    for (period, segment_name), values in sorted(segment_values.items()):
        if values["sales"] is None and values["operating_profit"] is None:
            continue
        result["segments"].append({
            "fiscal_year": period or result["period"] or "",
            "segment_name": segment_name,
            **values,
        })
    return result
