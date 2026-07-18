"""EDINET XBRLの標準タグだけを安全に抽出する。連結を優先し未知の拡張タグは採用しない。"""
from __future__ import annotations

import io
import zipfile
from xml.etree import ElementTree as ET

from data_normalizer import to_million_shares, to_million_yen

TAGS = {
    "sales": ["NetSales", "Revenue", "RevenueIFRS"], "operating_profit": ["OperatingIncome", "OperatingProfit"],
    "ordinary_profit": ["OrdinaryIncome"], "pretax_profit": ["IncomeBeforeIncomeTaxes"],
    "net_income": ["ProfitLoss", "ProfitLossAttributableToOwnersOfParent"], "eps": ["BasicEarningsPerShare"],
    "issued_shares": ["NumberOfIssuedShares"], "treasury_shares": ["NumberOfTreasuryShares"],
    "average_shares": ["AverageNumberOfShares"], "total_assets": ["Assets"], "net_assets": ["NetAssets"],
    "operating_cf": ["NetCashProvidedByUsedInOperatingActivities"], "investing_cf": ["NetCashProvidedByUsedInInvestingActivities"],
    "financing_cf": ["NetCashProvidedByUsedInFinancingActivities"],
}


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def extract_xbrl(zip_bytes: bytes) -> dict:
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        xbrl_names = [name for name in archive.namelist() if name.endswith(".xbrl") and "PublicDoc" in name]
        if not xbrl_names:
            raise ValueError("XBRL本文が見つかりませんでした。")
        root = ET.fromstring(archive.read(xbrl_names[0]))
    contexts = {node.attrib.get("id", ""): ET.tostring(node, encoding="unicode") for node in root.iter() if _local(node.tag) == "context"}
    result = {key: None for key in TAGS}
    result["period"] = None
    for node in root.iter():
        name, context = _local(node.tag), node.attrib.get("contextRef", "")
        if not node.text or "NonConsolidated" in context or "個別" in contexts.get(context, ""):
            continue
        if "CurrentYear" not in context and "Current" not in context:
            continue
        for key, candidates in TAGS.items():
            if result[key] is None and name in candidates:
                try:
                    value = float(node.text.replace(",", ""))
                except ValueError:
                    continue
                result[key] = value if key == "eps" else (to_million_shares(value) if "shares" in key else to_million_yen(value))
    return result
