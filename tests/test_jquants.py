import pytest
import requests

from company_data_service import (build_company_forecast_record, build_pl_import_records, fetch_company_data,
                                  find_companies, find_project_by_code,
                                  should_show_no_results)
from data_normalizer import (extract_latest_fy_records, extract_latest_forecast, normalize_code,
                             normalize_financial_record, search_companies, to_million_shares, to_million_yen)
from jquants_client import JQuantsApiError, JQuantsClient


MASTER = [
    {"Code": "72030", "CoName": "トヨタ自動車", "CoNameEn": "TOYOTA MOTOR", "Mkt": "プライム", "S17": "自動車", "S33": "輸送用機器"},
    {"Code": "72670", "CoName": "ホンダ", "CoNameEn": "HONDA", "Mkt": "プライム"},
    {"Code": "77130", "CoName": "株式会社 シグマ 光機", "CoNameEn": "SIGMAKOKI", "Mkt": "スタンダード"},
]

FY_OLD = {"Code": "72030", "DocType": "FinancialStatements", "CurPerType": "FY", "CurPerEn": "2024-03-31", "DisclosedDate": "2024-05-10", "NetSales": 2_000_000_000, "OperatingProfit": 200_000_000, "OrdinaryProfit": 180_000_000, "Profit": 100_000_000, "EarningsPerShare": 10, "TotalNumberOfIssuedShares": 11_000_000, "TreasuryStock": 1_000_000, "AverageNumberOfShares": 10_000_000}
FY_NEW = {**FY_OLD, "DisclosedDate": "2024-06-01", "Profit": 500_000_000, "EarningsPerShare": 50,
          "FSSales": 2_200_000_000, "FSOperatingProfit": 220_000_000, "FSProfit": 550_000_000, "FSEarningsPerShare": 55}
QUARTER = {**FY_OLD, "CurPerType": "3Q", "CurPerEn": "2024-12-31", "DisclosedDate": "2025-02-01"}
V2_FY = {
    "Code": "77130",
    "DocType": "FinancialStatements",
    "CurPerType": "FY",
    "CurPerEn": "2025-05-31",
    "DiscDate": "2025-07-10",
    "Sales": 11_580_000_000,
    "OP": 1_500_000_000,
    "OdP": 1_600_000_000,
    "NP": 986_000_000,
    "EPS": 139.23,
    "ShOutFY": 7_647_000,
    "TrShFY": 565_000,
    "AvgSh": 7_082_178,
    "BPS": 1_250.0,
    "DivAnn": 50.0,
    "FSales": 12_000_000_000,
    "FOP": 1_650_000_000,
    "FOdP": 1_700_000_000,
    "FNP": 1_050_000_000,
    "FEPS": 148.26,
}


def test_code_normalization_and_company_name_search():
    assert normalize_code("7203") == "72030"
    assert normalize_code("72030") == "72030"
    assert search_companies(MASTER, "トヨタ")[0]["stock_code"] == "7203"
    assert search_companies(MASTER, "7267")[0]["company_name"] == "ホンダ"


def test_japanese_company_search_handles_spaces_and_corporate_form_without_exception():
    assert search_companies(MASTER, "シグマ光機")[0]["stock_code"] == "7713"
    assert search_companies(MASTER, "シグマ　光機")[0]["stock_code"] == "7713"
    assert search_companies(MASTER, "株式会社シグマ")[0]["stock_code"] == "7713"


def test_unit_conversion_and_net_issued_shares():
    assert to_million_yen(2_000_000) == 2
    assert to_million_shares(10_000_000) == 10
    record = normalize_financial_record(FY_NEW)
    assert record["sales"] == 2_000
    assert record["net_issued_shares"] == 10
    assert record["shares_source"] == "発行済株式数－自己株式数"


def test_latest_fy_ignores_quarter_and_uses_latest_disclosure():
    records = extract_latest_fy_records([FY_OLD, QUARTER, FY_NEW])
    assert records == [FY_NEW]
    assert extract_latest_forecast([FY_OLD, QUARTER, FY_NEW]) == FY_NEW


def test_v2_abbreviated_financial_fields_are_normalized():
    record = normalize_financial_record(V2_FY)
    assert record["sales"] == 11_580
    assert record["operating_profit"] == 1_500
    assert record["ordinary_profit"] == 1_600
    assert record["net_income"] == 986
    assert record["issued_shares"] == 7.647
    assert record["treasury_shares"] == 0.565
    assert record["net_issued_shares"] == pytest.approx(7.082)
    assert record["average_shares"] == pytest.approx(7.082178)
    assert record["forecast_sales"] == 12_000
    assert record["forecast_operating_profit"] == 1_650
    assert record["forecast_net_income"] == 1_050
    assert record["forecast_eps"] == 148.26
    assert extract_latest_forecast([V2_FY]) == V2_FY


def test_pl_import_records_keep_average_shares_and_skip_incomplete_rows():
    complete = normalize_financial_record(V2_FY)
    incomplete = {**complete, "fiscal_year": "2024-05-31", "average_shares": None}
    records = build_pl_import_records([incomplete, complete])
    assert len(records) == 1
    assert records[0]["fiscal_year"] == "2025-05-31"
    assert records[0]["sales"] == 11_580
    assert records[0]["average_shares"] == pytest.approx(7.082178)
    assert records[0]["source"] == "J-Quants"


def test_company_forecast_keeps_undisclosed_fields_blank():
    forecast = build_company_forecast_record(normalize_financial_record(V2_FY))
    assert forecast["result_type"] == "会社予想"
    assert forecast["sales"] == 12_000
    assert forecast["operating_profit"] == 1_650
    assert forecast["net_income"] == 1_050
    assert forecast.get("cost_of_sales") is None


def test_v2_service_calculates_shares_market_cap_pbr_and_dividend_yield():
    session = FakeSession({"/equities/bars/daily": FakeResponse(200, {"data": [{"Date": "2026-07-17", "C": 1921}]}),
                           "/fins/summary": FakeResponse(200, {"data": [V2_FY]})})
    data = fetch_company_data(JQuantsClient("test-key", session=session), {
        "code": "77130", "stock_code": "7713", "company_name": "SIGMAKOKI"
    })
    assert data["share_info"]["net_issued_shares"] == pytest.approx(7.082)
    assert data["calculated"]["market_cap"] == pytest.approx(136.04522)
    assert data["calculated"]["pbr"] == pytest.approx(1921 / 1250)
    assert data["calculated"]["dividend_yield"] == pytest.approx(50 / 1921 * 100)


class FakeResponse:
    def __init__(self, status_code, body):
        self.status_code, self.body = status_code, body

    def json(self):
        return self.body


class FakeSession:
    def __init__(self, responses):
        self.responses = responses

    def get(self, url, **kwargs):
        self.last_url = url
        self.last_kwargs = kwargs
        for key, response in self.responses.items():
            if url.endswith(key):
                return response
        return FakeResponse(404, {})


def test_service_uses_mocked_api_responses():
    session = FakeSession({"/equities/master": FakeResponse(200, {"data": MASTER}),
                           "/equities/bars/daily": FakeResponse(200, {"data": [{"Date": "2026-07-15", "C": 1000}]}),
                           "/fins/summary": FakeResponse(200, {"data": [FY_NEW, QUARTER]})})
    client = JQuantsClient("test-key", session=session)
    company = find_companies(client, "7203")[0]
    data = fetch_company_data(client, company)
    assert data["price"]["close"] == 1000
    assert data["financials"][0]["net_income"] == 500
    assert data["calculated"]["market_cap"] == 100
    assert data["calculated"]["current_per"] == 20


def test_master_request_does_not_put_japanese_query_in_headers_or_params():
    session = FakeSession({"/equities/master": FakeResponse(200, {"data": MASTER})})
    companies = find_companies(JQuantsClient("test-key", session=session), "シグマ光機")
    assert companies[0]["stock_code"] == "7713"
    assert session.last_kwargs["params"] == {}
    assert "シグマ光機" not in str(session.last_kwargs["headers"])


def test_non_ascii_text_cannot_be_used_as_api_key_header():
    with pytest.raises(JQuantsApiError, match="APIキー"):
        JQuantsClient("シグマ光機")


def test_no_results_message_only_after_successful_api_search():
    assert not should_show_no_results(True, False, [])
    assert should_show_no_results(True, True, [])
    assert not should_show_no_results(True, True, [{"code": "72030"}])


def test_find_project_by_code_accepts_four_and_five_digit_codes():
    projects = [
        {"id": 8, "stock_code": "7713", "project_name": "シグマ光機"},
        {"id": 2, "stock_code": "72030", "project_name": "トヨタ"},
    ]

    assert find_project_by_code(projects, "7713")["id"] == 8
    assert find_project_by_code(projects, "77130")["id"] == 8
    assert find_project_by_code(projects, "7203")["id"] == 2
    assert find_project_by_code(projects, "123") is None


@pytest.mark.parametrize("status, message", [(401, "認証"), (404, "見つかりません"), (429, "利用回数")])
def test_api_errors_are_japanese(status, message):
    client = JQuantsClient("key", session=FakeSession({"/equities/master": FakeResponse(status, {})}))
    with pytest.raises(JQuantsApiError, match=message):
        client.equities_master()


def test_manual_data_is_not_changed_until_apply(tmp_path):
    from database import get_project, initialize_database, save_project
    db_path = tmp_path / "test.db"
    initialize_database(db_path)
    project_id = save_project({"project_name": "手入力", "company_name": "手入力会社", "investment_thesis": "仮説を維持"}, db_path)
    # 取得処理は辞書を返すだけで、save_project が呼ばれるまでDBを変更しない。
    preview = {"company_name": "API会社", "current_price": 1000}
    assert preview["company_name"] == "API会社"
    assert get_project(project_id, db_path)["company_name"] == "手入力会社"
    assert get_project(project_id, db_path)["investment_thesis"] == "仮説を維持"
