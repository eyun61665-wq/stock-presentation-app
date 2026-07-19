from __future__ import annotations

from datetime import datetime

import pytest

from data_providers import (
    CompanyMasterProvider,
    EdinetDocumentProvider,
    JpxCompanyMasterProvider,
    MarketDataProvider,
    PdfCandidateExtractor,
    XbrlFinancialParser,
)
from database import (
    confirm_financial_fact_mapping,
    get_company_master_record,
    get_pl_entries,
    get_segment_entries,
    initialize_database,
    list_financial_facts,
    list_mapping_rules,
    list_segment_facts,
    list_source_documents,
    save_company_master_record,
    save_financial_facts,
    save_mapping_rule,
    save_pl_entries,
    save_project,
    save_segment_entries,
    save_segment_facts,
    save_source_document,
)
from financial_data_models import (
    CompanyRecord,
    MappingRule,
    RawFinancialCandidate,
    RawSegmentCandidate,
    SourceDocument,
    standard_pl_support,
)
from financial_normalizer import FinancialMetricNormalizer, SegmentNormalizer


@pytest.fixture
def architecture_db(tmp_path):
    path = tmp_path / "architecture.sqlite3"
    initialize_database(path)
    return path


def _project(db_path, name: str, code: str) -> int:
    return save_project(
        {
            "project_name": name,
            "company_name": name,
            "stock_code": code,
        },
        db_path,
    )


def test_three_company_types_are_generic_and_isolated(architecture_db):
    companies = [
        ("日本基準製造テスト", "1111", "JGAAP", "jppfs_cor:NetSales", 1_000.0),
        ("IFRSサービス検証", "2222", "IFRS", "ifrs-full:Revenue", 2_000.0),
        ("独自タグ企業検証", "3333", "JGAAP", "custom:CloudPlatformSales", 3_000.0),
    ]
    project_ids = []
    for index, (name, code, standard, tag, revenue) in enumerate(companies, start=1):
        project_id = _project(architecture_db, name, code)
        project_ids.append(project_id)
        save_company_master_record(
            CompanyRecord(
                project_id=project_id,
                stock_code=code,
                company_name=name,
                market="東証",
                industry="一般事業会社",
                accounting_standard=standard,
                provider="テスト企業マスター",
            ),
            architecture_db,
        )
        document = SourceDocument(
            project_id=project_id,
            provider="EDINET",
            document_id=f"DOC-{code}",
            document_type="有価証券報告書",
            fiscal_year="2026-03-31",
            retrieved_at=datetime.now().isoformat(),
        )
        save_source_document(document, architecture_db)
        candidate = RawFinancialCandidate(
            raw_label="会社固有売上" if code == "3333" else "売上高",
            xbrl_tag=tag,
            value=revenue,
            unit="百万円",
            fiscal_year="2026-03-31",
            accounting_standard=standard,
            source="有価証券報告書",
            document_id=document.document_id,
        )
        rules = []
        if code == "3333":
            rules.append(
                MappingRule(
                    scope_type="company",
                    project_id=project_id,
                    raw_identifier=tag,
                    common_key="revenue",
                )
            )
        facts = FinancialMetricNormalizer(rules).normalize(project_id, [candidate])
        assert facts[0].common_key == "revenue"
        save_financial_facts(facts, architecture_db)
        save_pl_entries(
            project_id,
            [{
                "fiscal_year": "2026-03-31",
                "result_type": "実績",
                "sales": revenue,
                "operating_profit": revenue / 10,
                "source": "EDINET",
            }],
            architecture_db,
        )
        save_segment_entries(
            project_id,
            [{
                "fiscal_year": "2026-03-31",
                "result_type": "実績",
                "segment_name": f"事業{index}",
                "sales": revenue,
                "source": "EDINET",
            }],
            architecture_db,
        )

    for project_id, (_, code, _, tag, revenue) in zip(project_ids, companies):
        assert get_company_master_record(project_id, architecture_db)["stock_code"] == code
        assert get_pl_entries(project_id, architecture_db)[0]["sales"] == revenue
        assert get_segment_entries(project_id, architecture_db)[0]["sales"] == revenue
        saved_fact = list_financial_facts(project_id, architecture_db)[0]
        assert saved_fact["value"] == revenue
        assert saved_fact["xbrl_tag"] == tag
        assert saved_fact["raw_label"]
        assert saved_fact["source"] == "有価証券報告書"
        assert saved_fact["unit"] == "百万円"
        assert saved_fact["fiscal_year"] == "2026-03-31"
        assert len(list_source_documents(project_id, architecture_db)) == 1
    assert {get_pl_entries(project_id, architecture_db)[0]["sales"] for project_id in project_ids} == {
        1_000.0, 2_000.0, 3_000.0
    }


def test_mapping_priority_company_then_accounting_then_global():
    rules = [
        MappingRule("global", "CustomMetric", "gross_profit"),
        MappingRule("accounting", "CustomMetric", "operating_income", accounting_standard="IFRS"),
        MappingRule("company", "CustomMetric", "revenue", project_id=7),
    ]
    normalizer = FinancialMetricNormalizer(rules)
    assert normalizer.resolve(7, "IFRS", "CustomMetric") == ("revenue", "company")
    assert normalizer.resolve(8, "IFRS", "CustomMetric") == ("operating_income", "accounting")
    assert normalizer.resolve(8, "JGAAP", "CustomMetric") == ("gross_profit", "global")


def test_unknown_custom_tag_is_candidate_until_user_confirms(architecture_db):
    project_id = _project(architecture_db, "拡張タグ確認会社", "4444")
    candidate = RawFinancialCandidate(
        raw_label="独自収益項目",
        xbrl_tag="example:ProprietaryRevenueMetric",
        value=450.0,
        unit="百万円",
        fiscal_year="2026-12-31",
        accounting_standard="JGAAP",
        source="EDINET XBRL",
    )
    unresolved = FinancialMetricNormalizer().normalize(project_id, [candidate])[0]
    assert unresolved.common_key is None
    assert unresolved.status == "candidate"
    save_financial_facts([unresolved], architecture_db)
    fact = list_financial_facts(project_id, architecture_db)[0]
    confirm_financial_fact_mapping(
        fact["id"], "revenue", "company", manual_value=451.0, db_path=architecture_db
    )
    rules = list_mapping_rules(architecture_db)
    resolved = FinancialMetricNormalizer(rules).normalize(project_id, [candidate])[0]
    assert resolved.common_key == "revenue"
    assert resolved.mapping_scope == "company"
    saved = list_financial_facts(project_id, architecture_db)[0]
    assert saved["manual_value"] == 451.0
    assert saved["confirmed_at"]


def test_none_does_not_overwrite_manual_financial_or_segment_value(architecture_db):
    project_id = _project(architecture_db, "手動値保持会社", "5555")
    financial = FinancialMetricNormalizer().normalize(
        project_id,
        [RawFinancialCandidate("売上高", 100.0, "百万円", "2026", "書類")],
    )[0]
    financial.manual_value = 105.0
    save_financial_facts([financial], architecture_db)
    financial.manual_value = None
    financial.value = None
    save_financial_facts([financial], architecture_db)
    assert list_financial_facts(project_id, architecture_db)[0]["manual_value"] == 105.0

    segment = SegmentNormalizer({"クラウド事業": "クラウド事業"}).normalize(
        project_id,
        [RawSegmentCandidate("クラウド事業", 60.0, "百万円", "2026", "書類")],
    )[0]
    segment.manual_value = 61.0
    save_segment_facts([segment], architecture_db)
    segment.manual_value = None
    segment.value = None
    save_segment_facts([segment], architecture_db)
    assert list_segment_facts(project_id, architecture_db)[0]["manual_value"] == 61.0


def test_segment_normalizer_does_not_guess_unconfirmed_name():
    candidate = RawSegmentCandidate(
        raw_label="CompanySpecificMember",
        xbrl_tag="custom:CompanySpecificMember",
        value=10.0,
        unit="百万円",
        fiscal_year="2026",
        source="EDINET",
    )
    unresolved = SegmentNormalizer().normalize(1, [candidate])[0]
    assert unresolved.segment_name is None
    assert unresolved.status == "candidate"
    confirmed = SegmentNormalizer({candidate.raw_identifier: "国内事業"}).normalize(1, [candidate])[0]
    assert confirmed.segment_name == "国内事業"
    assert confirmed.status == "confirmed"


@pytest.mark.parametrize("industry", ["銀行業", "証券・商品先物", "生命保険", "J-REIT", "Regional Banks"])
def test_financial_industries_are_not_forced_into_standard_pl(industry):
    supported, message = standard_pl_support(industry)
    assert supported is False
    assert "未対応" in message


def test_provider_interfaces_accept_swappable_fakes():
    class FakeProvider:
        def search(self, query): return []
        def get_by_code(self, stock_code): return None
        def fetch(self, stock_code): return {}
        def list_documents(self, company, years=5): return []
        def fetch_document(self, document): return b""
        def parse_financials(self, content, document): return []
        def parse_segments(self, content, document): return []
        def extract_financials(self, content, document): return []
        def extract_segments(self, content, document): return []

    fake = FakeProvider()
    assert isinstance(fake, CompanyMasterProvider)
    assert isinstance(fake, MarketDataProvider)
    assert isinstance(fake, EdinetDocumentProvider)
    assert isinstance(fake, XbrlFinancialParser)
    assert isinstance(fake, PdfCandidateExtractor)


def test_jpx_company_master_adapter_is_not_company_specific():
    provider = JpxCompanyMasterProvider(
        [
            {"stock_code": "1234", "company_name": "汎用製造株式会社", "market": "プライム", "sector33": "機械"},
            {"stock_code": "5678", "company_name": "汎用サービス株式会社", "market": "スタンダード", "sector33": "サービス業"},
        ]
    )
    assert provider.get_by_code("5678").company_name == "汎用サービス株式会社"
    assert provider.search("汎用")[-1].stock_code in {"1234", "5678"}


def test_mapping_rule_persists_for_non_specific_company(architecture_db):
    project_id = _project(architecture_db, "ルール保存検証", "6666")
    save_mapping_rule(
        MappingRule(
            scope_type="company",
            project_id=project_id,
            raw_identifier="custom:SubscriptionRevenue",
            common_key="revenue",
            confirmed_at="2026-07-19T12:00:00+09:00",
        ),
        architecture_db,
    )
    saved = list_mapping_rules(architecture_db)
    assert saved[0]["project_id"] == project_id
    assert saved[0]["raw_identifier"] == "custom:SubscriptionRevenue"
