"""銘柄コードと会社名から企業公式の決算短信一覧ページを特定する。"""
from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import requests

from data_normalizer import canonical_company_name, display_code
from data_sources import USER_AGENT, fetch_ir_documents


SEARCH_URL = "https://html.duckduckgo.com/html/"
CACHE_PATH = Path(__file__).resolve().parent / "data_cache" / "ir_source_map.json"

# 実画面で取得確認済みの公式IRページ。自動探索結果もローカルキャッシュへ追加する。
VERIFIED_IR_SOURCES = {
    "3355": "https://www.kuriyama-holdings.com/ir/library/earnings/",
    "6617": "https://www.tktk.co.jp/ir/library/report/",
    "6651": "https://www.nito.co.jp/IR/library/results/",
    "7713": "https://www.sigma-koki.com/document_category/tansin/",
    "4343": "https://www.fantasy.co.jp/company/ircontent/library/library_02.html",
    "4417": "https://www.gsx.co.jp/ir/library/summary/",
    "5246": "https://ir.elementsinc.jp/library/",
}

EXCLUDED_HOSTS = (
    "duckduckgo.com", "google.", "bing.com", "yahoo.co.jp", "kabutan.jp",
    "minkabu.jp", "nikkei.com", "irbank.net", "buffett-code.com", "jpx.co.jp",
    "edinet-fsa.go.jp",
)


class IRSourceDiscoveryError(Exception):
    pass


class _SearchResultParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        values = dict(attrs)
        class_name = values.get("class") or ""
        href = values.get("href") or ""
        if "result__a" in class_name and href:
            self.urls.append(href)


def _direct_search_url(url: str) -> str:
    """検索エンジンのリダイレクトURLを公式URLへ戻す。"""
    if url.startswith("//"):
        url = f"https:{url}"
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        redirected = parse_qs(parsed.query).get("uddg", [])
        if redirected:
            return unquote(redirected[0])
    return url


def extract_search_result_urls(html: str) -> list[str]:
    parser = _SearchResultParser()
    parser.feed(html)
    output: list[str] = []
    for raw in parser.urls:
        url = _direct_search_url(raw)
        host = urlparse(url).netloc.lower()
        if not url.startswith("http") or url.lower().endswith(".pdf"):
            continue
        if any(excluded in host for excluded in EXCLUDED_HOSTS):
            continue
        if url not in output:
            output.append(url)
    return output


def _read_cache(path: Path = CACHE_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return {str(key): str(url) for key, url in value.items() if str(url).startswith("http")}
    except (OSError, ValueError, TypeError):
        return {}


def _write_cache(values: dict[str, str], path: Path = CACHE_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(values, ensure_ascii=False, indent=2), encoding="utf-8")


def _has_annual_results(url: str, company_name: str, session=requests) -> bool:
    """公式候補に決算短信PDFがあり、会社名も不自然でないことを確認する。"""
    try:
        response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=15)
        response.raise_for_status()
    except requests.RequestException:
        return False
    page_name = canonical_company_name(response.text)
    company = canonical_company_name(company_name)
    if company and company not in page_name:
        return False
    try:
        documents = fetch_ir_documents(url, refresh=True, session=session, timeout=15)
    except Exception:
        return False
    return any("決算短信" in document.get("title", "") for document in documents)


def discover_ir_source(
    stock_code: str,
    company_name: str,
    *,
    refresh: bool = False,
    session=requests,
    cache_path: Path = CACHE_PATH,
) -> dict[str, str]:
    """公式IRを既知リスト、ローカルキャッシュ、公開検索の順で解決する。"""
    try:
        code = display_code(stock_code)
    except ValueError as exc:
        raise IRSourceDiscoveryError("公式IRの自動探索には4桁の銘柄コードが必要です。") from exc

    cached = _read_cache(cache_path)
    if code in VERIFIED_IR_SOURCES:
        url = VERIFIED_IR_SOURCES[code]
        cached[code] = url
        _write_cache(cached, cache_path)
        return {"url": url, "method": "確認済み公式IR"}
    if not refresh and code in cached:
        return {"url": cached[code], "method": "ローカルキャッシュ"}
    if not company_name.strip():
        raise IRSourceDiscoveryError("会社名が未設定のため、公式IRを自動探索できませんでした。")

    try:
        response = session.get(
            SEARCH_URL,
            params={"q": f"{code} {company_name} 公式 IR 決算短信"},
            headers={"User-Agent": USER_AGENT},
            timeout=20,
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        if code in cached:
            return {"url": cached[code], "method": "前回キャッシュ"}
        raise IRSourceDiscoveryError("公式IRの自動探索に失敗しました。通信を確認して再取得してください。") from exc

    candidates = extract_search_result_urls(response.text)
    candidates.sort(
        key=lambda url: ("決算" not in url and "result" not in url.lower(), "/ir/" not in url.lower())
    )
    for candidate in candidates[:8]:
        if _has_annual_results(candidate, company_name, session=session):
            cached[code] = candidate
            _write_cache(cached, cache_path)
            return {"url": candidate, "method": "公式サイト自動探索"}
    if code in cached:
        return {"url": cached[code], "method": "前回キャッシュ"}
    raise IRSourceDiscoveryError("企業公式サイトから決算短信一覧を見つけられませんでした。PDFアップロードを利用してください。")
