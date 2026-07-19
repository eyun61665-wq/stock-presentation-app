"""企業固有の表記を失わずに財務データを受け渡す共通モデル。"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


COMMON_FINANCIAL_KEYS = (
    "revenue",
    "cost_of_sales",
    "gross_profit",
    "sga",
    "operating_income",
    "ordinary_income",
    "net_income",
    "eps",
)


@dataclass(slots=True)
class CompanyRecord:
    project_id: int
    stock_code: str
    company_name: str
    market: str = ""
    industry: str = ""
    accounting_standard: str = ""
    provider: str = "手入力"
    confirmed_at: str = ""
    raw_data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class SourceDocument:
    project_id: int
    provider: str
    document_id: str
    document_type: str
    title: str = ""
    source_url: str = ""
    filing_date: str = ""
    fiscal_year: str = ""
    retrieved_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RawFinancialCandidate:
    raw_label: str
    value: float | None
    unit: str
    fiscal_year: str
    source: str
    xbrl_tag: str = ""
    accounting_standard: str = ""
    document_id: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def raw_identifier(self) -> str:
        return self.xbrl_tag.strip() or self.raw_label.strip()


@dataclass(slots=True)
class NormalizedFinancialFact:
    project_id: int
    common_key: str | None
    raw_label: str
    xbrl_tag: str
    value: float | None
    unit: str
    fiscal_year: str
    accounting_standard: str
    source: str
    document_id: str = ""
    manual_value: float | None = None
    mapping_scope: str = "unresolved"
    status: str = "candidate"
    confirmed_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class RawSegmentCandidate:
    raw_label: str
    value: float | None
    unit: str
    fiscal_year: str
    source: str
    metric: str = "revenue"
    xbrl_tag: str = ""
    document_id: str = ""
    display_order: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def raw_identifier(self) -> str:
        return self.xbrl_tag.strip() or self.raw_label.strip()


@dataclass(slots=True)
class NormalizedSegmentFact:
    project_id: int
    segment_name: str | None
    raw_label: str
    xbrl_tag: str
    metric: str
    value: float | None
    unit: str
    fiscal_year: str
    source: str
    document_id: str = ""
    manual_value: float | None = None
    display_order: int = 0
    status: str = "candidate"
    confirmed_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class MappingRule:
    scope_type: str
    raw_identifier: str
    common_key: str
    project_id: int | None = None
    accounting_standard: str = ""
    confirmed_at: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


UNSUPPORTED_STANDARD_PL_KEYWORDS = (
    "銀行",
    "bank",
    "証券",
    "securities",
    "保険",
    "insurance",
    "reits",
    "reit",
    "投資法人",
)


def standard_pl_support(industry: str | None) -> tuple[bool, str]:
    """標準PLで誤表示しやすい金融・REIT業種を保守的に除外する。"""
    text = str(industry or "").strip().casefold()
    if any(keyword in text for keyword in UNSUPPORTED_STANDARD_PL_KEYWORDS):
        return False, "銀行・証券・保険・REIT等は現在の標準PLでは未対応です。"
    return True, ""
