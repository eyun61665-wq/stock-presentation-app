from urllib.parse import quote

from ir_source_discovery import (SEARCH_URL, discover_ir_source,
                                 extract_search_result_urls)


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.status_code = status_code
        self.encoding = "utf-8"

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            raise requests.HTTPError(str(self.status_code))


class FakeSession:
    def __init__(self, search_html: str, official_html: str):
        self.search_html = search_html
        self.official_html = official_html
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return FakeResponse(self.search_html if url == SEARCH_URL else self.official_html)


def test_verified_nitto_ir_source_needs_no_manual_url(tmp_path):
    result = discover_ir_source("6651", "日東工業", cache_path=tmp_path / "sources.json")
    assert result["url"] == "https://www.nito.co.jp/IR/library/results/"
    assert result["method"] == "確認済み公式IR"


def test_verified_aeon_fantasy_ir_source_needs_no_search(tmp_path):
    result = discover_ir_source(
        "4343", "イオンファンタジー", cache_path=tmp_path / "sources.json"
    )
    assert result["url"] == (
        "https://www.fantasy.co.jp/company/ircontent/library/library_02.html"
    )
    assert result["method"] == "確認済み公式IR"


def test_verified_kuriyama_ir_source_needs_no_search(tmp_path):
    result = discover_ir_source(
        "3355", "クリヤマホールディングス", cache_path=tmp_path / "sources.json"
    )
    assert result["url"] == "https://www.kuriyama-holdings.com/ir/library/earnings/"
    assert result["method"] == "確認済み公式IR"


def test_search_uses_params_and_validates_official_results(tmp_path):
    official_url = "https://example.test/ir/results/"
    redirect = f"//duckduckgo.com/l/?uddg={quote(official_url, safe='')}"
    search_html = f'<a class="result__a" href="{redirect}">テスト工業 IR</a>'
    official_html = """
        <html><body>テスト工業
        <a href="/ir/2025.pdf">2025年3月期 決算短信</a>
        </body></html>
    """
    session = FakeSession(search_html, official_html)

    result = discover_ir_source(
        "1234", "テスト工業", session=session, cache_path=tmp_path / "sources.json"
    )

    assert result == {"url": official_url, "method": "公式サイト自動探索"}
    search_call = session.calls[0]
    assert search_call[1]["params"]["q"] == "1234 テスト工業 公式 IR 決算短信"
    assert "テスト工業" not in str(search_call[1]["headers"])


def test_search_results_exclude_aggregators_and_pdf_links():
    html = """
      <a class="result__a" href="https://finance.yahoo.co.jp/quote/1234">集約サイト</a>
      <a class="result__a" href="https://example.test/report.pdf">PDF</a>
      <a class="result__a" href="https://example.test/ir/results/">公式IR</a>
    """
    assert extract_search_result_urls(html) == ["https://example.test/ir/results/"]
