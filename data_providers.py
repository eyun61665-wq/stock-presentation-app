"""外部データ取得と解析をUIから切り離すアダプター定義。"""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from financial_data_models import (
    CompanyRecord,
    RawFinancialCandidate,
    RawSegmentCandidate,
    SourceDocument,
)


@runtime_checkable
class CompanyMasterProvider(Protocol):
    """証券コードまたは会社名から企業候補を返す。"""

    def search(self, query: str) -> list[CompanyRecord]: ...

    def get_by_code(self, stock_code: str) -> CompanyRecord | None: ...


@runtime_checkable
class MarketDataProvider(Protocol):
    """株価・時価総額等を取得する。取得不能項目はNoneを許容する。"""

    def fetch(self, stock_code: str) -> dict[str, Any]: ...


@runtime_checkable
class EdinetDocumentProvider(Protocol):
    """EDINET書類一覧と原本を取得する将来拡張用インターフェース。"""

    def list_documents(self, company: CompanyRecord, years: int = 5) -> list[SourceDocument]: ...

    def fetch_document(self, document: SourceDocument) -> bytes: ...


@runtime_checkable
class XbrlFinancialParser(Protocol):
    """XBRLを根拠情報付きの候補へ変換する。"""

    def parse_financials(
        self, content: bytes, document: SourceDocument
    ) -> list[RawFinancialCandidate]: ...

    def parse_segments(
        self, content: bytes, document: SourceDocument
    ) -> list[RawSegmentCandidate]: ...


@runtime_checkable
class PdfCandidateExtractor(Protocol):
    """PDFから確定前の候補だけを抽出する。"""

    def extract_financials(
        self, content: bytes, document: SourceDocument
    ) -> list[RawFinancialCandidate]: ...

    def extract_segments(
        self, content: bytes, document: SourceDocument
    ) -> list[RawSegmentCandidate]: ...


class YFinanceMarketDataProvider:
    """現在のyfinance取得処理を汎用インターフェースへ接続する。"""

    def fetch(self, stock_code: str) -> dict[str, Any]:
        from market_data import fetch_market_data

        return fetch_market_data(stock_code)


class JpxCompanyMasterProvider:
    """JPX上場銘柄マスターをCompanyMasterProviderへ接続する。"""

    def __init__(self, rows: list[dict[str, Any]] | None = None, *, refresh: bool = False) -> None:
        if rows is None:
            from jpx_master import get_master

            rows, _ = get_master(refresh=refresh)
        self.rows = list(rows)

    @staticmethod
    def _record(row: dict[str, Any]) -> CompanyRecord:
        return CompanyRecord(
            project_id=0,
            stock_code=str(row.get("stock_code") or "")[:4],
            company_name=str(row.get("company_name") or ""),
            market=str(row.get("market") or ""),
            industry=str(row.get("sector33") or ""),
            provider="JPX",
            raw_data=dict(row),
        )

    def search(self, query: str) -> list[CompanyRecord]:
        from jpx_master import search_master

        return [self._record(row) for row in search_master(self.rows, query)]

    def get_by_code(self, stock_code: str) -> CompanyRecord | None:
        matches = self.search(stock_code)
        return matches[0] if matches else None


class PyMuPdfCandidateExtractor:
    """既存の安全なPDF候補抽出をアダプターとして公開する。"""

    def extract_financials(
        self, content: bytes, document: SourceDocument
    ) -> list[RawFinancialCandidate]:
        from pdf_extractor import PL_KEYWORDS, extract_pdf_candidates

        result = extract_pdf_candidates(content, PL_KEYWORDS)
        return [
            RawFinancialCandidate(
                raw_label=str(row.get("項目") or ""),
                value=row.get("候補値"),
                unit=str(row.get("単位") or ""),
                fiscal_year=str(row.get("年度") or document.fiscal_year),
                source=document.title or document.provider,
                document_id=document.document_id,
                metadata={
                    "page": row.get("ページ"),
                    "context": row.get("周辺原文"),
                    "source_url": document.source_url,
                },
            )
            for row in result.get("candidates", [])
        ]

    def extract_segments(
        self, content: bytes, document: SourceDocument
    ) -> list[RawSegmentCandidate]:
        from pdf_extractor import SEGMENT_KEYWORDS, extract_pdf_candidates

        result = extract_pdf_candidates(content, SEGMENT_KEYWORDS)
        return [
            RawSegmentCandidate(
                raw_label=str(row.get("項目") or ""),
                value=row.get("候補値"),
                unit=str(row.get("単位") or ""),
                fiscal_year=str(row.get("年度") or document.fiscal_year),
                source=document.title or document.provider,
                document_id=document.document_id,
                metadata={
                    "page": row.get("ページ"),
                    "context": row.get("周辺原文"),
                    "source_url": document.source_url,
                },
            )
            for row in result.get("candidates", [])
        ]
