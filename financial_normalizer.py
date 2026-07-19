"""財務候補を共通キーへ変換し、未確定候補を推測せず残す。"""
from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping

from financial_data_models import (
    COMMON_FINANCIAL_KEYS,
    MappingRule,
    NormalizedFinancialFact,
    NormalizedSegmentFact,
    RawFinancialCandidate,
    RawSegmentCandidate,
)


GLOBAL_MAPPINGS = {
    "売上高": "revenue",
    "売上収益": "revenue",
    "営業収益": "revenue",
    "revenue": "revenue",
    "netsales": "revenue",
    "売上原価": "cost_of_sales",
    "costofsales": "cost_of_sales",
    "売上総利益": "gross_profit",
    "grossprofit": "gross_profit",
    "販売費及び一般管理費": "sga",
    "販管費": "sga",
    "sellinggeneralandadministrativeexpenses": "sga",
    "営業利益": "operating_income",
    "operatingincome": "operating_income",
    "operatingprofit": "operating_income",
    "経常利益": "ordinary_income",
    "ordinaryincome": "ordinary_income",
    "親会社株主に帰属する当期純利益": "net_income",
    "当期純利益": "net_income",
    "profitlossattributabletoownersofparent": "net_income",
    "eps": "eps",
    "1株当たり当期純利益": "eps",
    "basicearningspershare": "eps",
}

ACCOUNTING_STANDARD_MAPPINGS = {
    "JGAAP": {
        "jppfs_cor:NetSales": "revenue",
        "jppfs_cor:CostOfSales": "cost_of_sales",
        "jppfs_cor:GrossProfit": "gross_profit",
        "jppfs_cor:SellingGeneralAndAdministrativeExpenses": "sga",
        "jppfs_cor:OperatingIncome": "operating_income",
        "jppfs_cor:OrdinaryIncome": "ordinary_income",
        "jppfs_cor:ProfitLossAttributableToOwnersOfParent": "net_income",
        "jppfs_cor:BasicEarningsPerShare": "eps",
    },
    "IFRS": {
        "ifrs-full:Revenue": "revenue",
        "ifrs-full:CostOfSales": "cost_of_sales",
        "ifrs-full:GrossProfit": "gross_profit",
        "ifrs-full:OperatingProfitLoss": "operating_income",
        "ifrs-full:ProfitLossAttributableToOwnersOfParent": "net_income",
        "ifrs-full:BasicEarningsLossPerShare": "eps",
    },
}


def normalize_identifier(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return "".join(text.split())


def normalize_accounting_standard(value: object) -> str:
    text = normalize_identifier(value)
    if text in {"ifrs", "国際会計基準", "国際財務報告基準"}:
        return "IFRS"
    if text in {"jgaap", "日本基準", "日本会計基準"}:
        return "JGAAP"
    return str(value or "").strip().upper()


def _rule(row: MappingRule | Mapping[str, object]) -> MappingRule:
    if isinstance(row, MappingRule):
        return row
    return MappingRule(
        scope_type=str(row.get("scope_type") or ""),
        raw_identifier=str(row.get("raw_identifier") or ""),
        common_key=str(row.get("common_key") or ""),
        project_id=int(row["project_id"]) if row.get("project_id") is not None else None,
        accounting_standard=str(row.get("accounting_standard") or ""),
        confirmed_at=str(row.get("confirmed_at") or ""),
        note=str(row.get("note") or ""),
    )


class FinancialMetricNormalizer:
    """確認済みルールを優先して共通財務キーへ変換する。"""

    def __init__(self, rules: Iterable[MappingRule | Mapping[str, object]] = ()) -> None:
        self.rules = [_rule(row) for row in rules]

    def resolve(
        self,
        project_id: int,
        accounting_standard: str,
        raw_identifier: str,
        raw_label: str = "",
    ) -> tuple[str | None, str]:
        identifiers = {
            key for key in (normalize_identifier(raw_identifier), normalize_identifier(raw_label)) if key
        }
        standard = normalize_accounting_standard(accounting_standard)

        for rule in self.rules:
            if (
                rule.scope_type == "company"
                and rule.project_id == project_id
                and normalize_identifier(rule.raw_identifier) in identifiers
                and rule.common_key in COMMON_FINANCIAL_KEYS
            ):
                return rule.common_key, "company"

        for rule in self.rules:
            if (
                rule.scope_type == "accounting"
                and normalize_accounting_standard(rule.accounting_standard) == standard
                and normalize_identifier(rule.raw_identifier) in identifiers
                and rule.common_key in COMMON_FINANCIAL_KEYS
            ):
                return rule.common_key, "accounting"
        for raw, common_key in ACCOUNTING_STANDARD_MAPPINGS.get(standard, {}).items():
            if normalize_identifier(raw) in identifiers:
                return common_key, "accounting"

        for rule in self.rules:
            if (
                rule.scope_type == "global"
                and normalize_identifier(rule.raw_identifier) in identifiers
                and rule.common_key in COMMON_FINANCIAL_KEYS
            ):
                return rule.common_key, "global"
        for raw, common_key in GLOBAL_MAPPINGS.items():
            if normalize_identifier(raw) in identifiers:
                return common_key, "global"
        return None, "unresolved"

    def normalize(
        self, project_id: int, candidates: Iterable[RawFinancialCandidate]
    ) -> list[NormalizedFinancialFact]:
        facts: list[NormalizedFinancialFact] = []
        for candidate in candidates:
            common_key, scope = self.resolve(
                project_id,
                candidate.accounting_standard,
                candidate.raw_identifier,
                candidate.raw_label,
            )
            facts.append(
                NormalizedFinancialFact(
                    project_id=project_id,
                    common_key=common_key,
                    raw_label=candidate.raw_label,
                    xbrl_tag=candidate.xbrl_tag,
                    value=candidate.value,
                    unit=candidate.unit,
                    fiscal_year=candidate.fiscal_year,
                    accounting_standard=normalize_accounting_standard(candidate.accounting_standard),
                    source=candidate.source,
                    document_id=candidate.document_id,
                    mapping_scope=scope,
                    status="mapped" if common_key else "candidate",
                    metadata=dict(candidate.metadata),
                )
            )
        return facts


class SegmentNormalizer:
    """セグメント名を勝手に生成せず、確認済み対応だけを確定する。"""

    def __init__(self, confirmed_names: Mapping[str, str] | None = None) -> None:
        self.confirmed_names = {
            normalize_identifier(raw): str(name).strip()
            for raw, name in (confirmed_names or {}).items()
            if str(name).strip()
        }

    def normalize(
        self, project_id: int, candidates: Iterable[RawSegmentCandidate]
    ) -> list[NormalizedSegmentFact]:
        facts: list[NormalizedSegmentFact] = []
        for candidate in candidates:
            confirmed = self.confirmed_names.get(normalize_identifier(candidate.raw_identifier))
            facts.append(
                NormalizedSegmentFact(
                    project_id=project_id,
                    segment_name=confirmed,
                    raw_label=candidate.raw_label,
                    xbrl_tag=candidate.xbrl_tag,
                    metric=candidate.metric,
                    value=candidate.value,
                    unit=candidate.unit,
                    fiscal_year=candidate.fiscal_year,
                    source=candidate.source,
                    document_id=candidate.document_id,
                    display_order=candidate.display_order,
                    status="confirmed" if confirmed else "candidate",
                    metadata=dict(candidate.metadata),
                )
            )
        return facts
