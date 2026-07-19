import io
import zipfile
from datetime import date

import pandas as pd
import pytest

from edinet_client import EDINETClient, EDINETError, annual_reports
from jpx_master import load_master, search_master
from xbrl_extractor import extract_xbrl


def test_jpx_excel_load_and_japanese_code_search(monkeypatch, tmp_path):
    frame = pd.DataFrame({"コード": ["7713"], "銘柄名": ["シグマ光機"], "市場・商品区分": ["スタンダード"],
                          "33業種コード": ["31"], "33業種区分": ["精密機器"], "規模区分": ["TOPIX Small 1"]})
    monkeypatch.setattr(pd, "read_excel", lambda *args, **kwargs: frame)
    rows = load_master(tmp_path / "sample.xlsx")
    assert search_master(rows, "シグマ 光機")[0]["stock_code"] == "7713"
    assert search_master(rows, "7713")[0]["sector33"] == "精密機器"


def test_company_name_search_ranks_exact_match_before_longer_candidate():
    rows = [
        {"stock_code": "1111", "company_name": "ELEMENTSホールディングス"},
        {"stock_code": "5246", "company_name": "ELEMENTS"},
    ]
    assert [row["stock_code"] for row in search_master(rows, "elements")] == ["5246", "1111"]


def test_edinet_annual_report_filter_and_code_mapping():
    docs = [{"edinetCode": "E00001", "docTypeCode": "120", "docID": "S1"},
            {"edinetCode": "E00001", "docTypeCode": "140", "docID": "S2"},
            {"edinetCode": "E99999", "docTypeCode": "120", "docID": "S3"}]
    assert annual_reports(docs, "E00001") == [docs[0]]


def make_xbrl_zip(sales_tag: str = "NetSales") -> bytes:
    xml = '''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:j="urn:test">
    <xbrli:context id="CurrentYearConsolidated"><xbrli:entity><xbrli:identifier scheme="x">a</xbrli:identifier></xbrli:entity></xbrli:context>
    <j:{sales_tag} contextRef="CurrentYearConsolidated">2000000000</j:{sales_tag}><j:OperatingIncome contextRef="CurrentYearConsolidated">200000000</j:OperatingIncome>
    <j:ProfitLoss contextRef="CurrentYearConsolidated">500000000</j:ProfitLoss><j:AverageNumberOfShares contextRef="CurrentYearConsolidated">10000000</j:AverageNumberOfShares>
    <j:{sales_tag} contextRef="CurrentYearNonConsolidated">9999999999</j:{sales_tag}></xbrli:xbrl>'''.format(sales_tag=sales_tag)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("XBRL/PublicDoc/sample.xbrl", xml)
    return buffer.getvalue()


def test_xbrl_extracts_consolidated_jgaap_and_avoids_non_consolidated():
    result = extract_xbrl(make_xbrl_zip())
    assert result["sales"] == 2000
    assert result["operating_profit"] == 200
    assert result["net_income"] == 500
    assert result["average_shares"] == 10


def test_xbrl_ifrs_revenue_and_missing_ordinary_profit_are_safe():
    result = extract_xbrl(make_xbrl_zip("RevenueIFRS"))
    assert result["sales"] == 2000
    assert result["ordinary_profit"] is None


class Response:
    def __init__(self, code, body): self.status_code, self._body = code, body
    def json(self): return self._body


class Session:
    def __init__(self, response): self.response = response
    def get(self, *args, **kwargs): return self.response


def test_edinet_auth_error_and_no_manual_data_blocking():
    client = EDINETClient("bad", session=Session(Response(401, {})))
    with pytest.raises(EDINETError, match="認証"):
        client.documents(date.today())
