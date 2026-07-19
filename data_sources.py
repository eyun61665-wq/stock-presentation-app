"""企業公式IRページ・PDFの取得とローカルキャッシュ。"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

CACHE_DIR = Path(__file__).resolve().parent / "data_cache" / "ir_documents"
USER_AGENT = "Mozilla/5.0 (compatible; StockPresentationMVP/1.0; personal research)"


class DataSourceError(Exception):
    pass


class _PdfLinkParser(HTMLParser):
    """IRページのPDFリンクと表示名を標準ライブラリだけで収集する。"""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._href: str | None = None
        self._parts: list[str] = []
        self._in_article = False
        self._article_href: str | None = None
        self._article_parts: list[str] = []
        self.documents: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag == "article":
            self._in_article = True
            self._article_href = None
            self._article_parts = []
            return
        if tag != "a":
            return
        href = dict(attrs).get("href")
        if href and ".pdf" in href.lower():
            if self._in_article:
                self._article_href = href
            else:
                self._href = href
                self._parts = []

    def handle_data(self, data: str) -> None:
        if self._in_article:
            self._article_parts.append(data)
        elif self._href:
            self._parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "article" and self._in_article:
            if self._article_href:
                title = " ".join(" ".join(self._article_parts).split())
                self.documents.append({"url": self._article_href, "title": title})
            self._in_article = False
            self._article_href = None
            self._article_parts = []
            return
        if tag != "a" or not self._href or self._in_article:
            return
        title = " ".join(" ".join(self._parts).split())
        self.documents.append({"url": self._href, "title": title})
        self._href = None
        self._parts = []


def cache_path(url: str, suffix: str) -> Path:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return CACHE_DIR / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}{suffix}"


def _response_text(response) -> str:
    """HTTP既定のlatin-1誤判定を避け、日本語ページを正しく読む。"""
    content = getattr(response, "content", b"")
    if not content:
        return response.text
    encoding = (getattr(response, "encoding", None) or "").lower()
    if not encoding or encoding in {"iso-8859-1", "latin-1"}:
        encoding = getattr(response, "apparent_encoding", None) or "utf-8"
    try:
        return content.decode(encoding, errors="replace")
    except (LookupError, AttributeError):
        return response.text


def _parse_jsonp(text: str) -> dict | None:
    match = re.search(r"^[^(]+\(\s*(\{.*\})\s*\)\s*;?\s*$", text.strip(), flags=re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def _fetch_eir_v5_documents(
    html: str, ir_url: str, session=requests, timeout: int = 20,
) -> list[dict[str, str]]:
    """E-IR v5のHTMLに埋め込まれた一覧フィードからPDFを取得する。"""
    feed_urls = [
        urljoin(ir_url, match)
        for match in re.findall(
            r'<script[^>]+src=["\']([^"\']*eir-parts\.net/[^"\']*/announcement_\d+\.js(?:\?[^"\']*)?)["\']',
            html,
            flags=re.I,
        )
    ]
    headers = {"User-Agent": USER_AGENT, "Referer": ir_url}
    documents: list[dict[str, str]] = []
    for feed_url in feed_urls:
        try:
            response = session.get(feed_url, headers=headers, timeout=timeout)
            response.raise_for_status()
        except requests.RequestException:
            continue
        payload = _parse_jsonp(_response_text(response))
        items = payload.get("item", []) if isinstance(payload, dict) else []
        for item in items:
            link = str(item.get("link") or "").strip()
            item_type = str(item.get("type") or item.get("icon") or "").lower()
            if not link or item_type != "pdf":
                continue
            documents.append({
                "url": urljoin(ir_url, link),
                "title": str(item.get("title") or link.rsplit("/", 1)[-1]).strip(),
                "published": str(item.get("published") or item.get("date") or ""),
            })
    return documents


def _fetch_eir_documents(html: str, ir_url: str, session=requests, timeout: int = 20) -> list[dict[str, str]]:
    """JavaScript表示のE-IR（プロネクサス）から公式PDF一覧を取得する。"""
    # eirCode は一覧HTMLではなく eir.js にだけ定義する企業サイトもある。
    if "eir-parts.net" not in html and not re.search(r"eir(?:_v5)?\.js", html, flags=re.I):
        return []
    script_match = re.search(
        r'<script[^>]+src=["\']([^"\']*eir(?:_v5)?\.js)["\']', html, flags=re.I
    )
    if not script_match:
        return []
    script_url = urljoin(ir_url, script_match.group(1))
    headers = {"User-Agent": USER_AGENT, "Referer": ir_url}
    try:
        script_response = session.get(script_url, headers=headers, timeout=timeout)
        script_response.raise_for_status()
        script = _response_text(script_response)
    except requests.RequestException:
        return []
    code_match = re.search(
        r'(?:var|const)\s+eirCode\s*=\s*["\'](\d{4,5})["\']', script
    )
    if not code_match:
        return []
    code = code_match.group(1)[:4]
    configured_ids = [
        int(value)
        for value in re.findall(r'(?:file|area)_tanshin_(\d+)', html, flags=re.I)
    ]
    parts_ids = list(dict.fromkeys([*configured_ids, 2, 1, 3, 4]))
    for parts_id in parts_ids:
        data_url = f"https://ssl4.eir-parts.net/V4Public/EIR/{code}/ja/announcement/announcement_{parts_id}.js"
        try:
            data_response = session.get(data_url, headers=headers, timeout=timeout)
            data_response.raise_for_status()
        except requests.RequestException:
            continue
        data = _parse_jsonp(_response_text(data_response))
        items = data.get("item", []) if isinstance(data, dict) else []
        documents = [
            {
                "url": str(item.get("link", "")),
                "title": str(item.get("title", "")).strip(),
                "published": str(item.get("format_date") or item.get("date") or ""),
            }
            for item in items
            if item.get("type") == "pdf" and item.get("link")
        ]
        if any(item.get("news_type") == "tanshin" for item in items):
            return documents
    return []


def _fetch_irpocket_documents(
    html: str, ir_url: str, session=requests, timeout: int = 20,
) -> list[dict[str, str]]:
    """JavaScript表示のIRPocketから、企業が公開しているPDF一覧を取得する。"""
    match = re.search(
        r"(?:https?:)?//irpocket\.com/([0-9A-Za-z]+)/irpocket/(?:loader|config)\.js",
        html,
        flags=re.I,
    )
    if not match:
        return []
    code = match.group(1)
    feed_url = f"https://xml.irpocket.com/{code}/JS/ir-all-all.js"
    try:
        response = session.get(
            feed_url,
            headers={"User-Agent": USER_AGENT, "Referer": ir_url},
            timeout=timeout,
        )
        response.raise_for_status()
        # JavaScriptはUTF-8固定。requestsのlatin-1推定を使わない。
        raw = getattr(response, "content", b"")
        script = raw.decode("utf-8", errors="replace") if raw else response.text
    except requests.RequestException:
        return []
    payload_match = re.search(r"ir20handler\(\s*(\{.*\})\s*\)\s*;?", script, flags=re.S)
    if not payload_match:
        return []
    try:
        payload = json.loads(payload_match.group(1))
    except json.JSONDecodeError:
        return []
    documents: list[dict[str, str]] = []
    for item in payload.get("item", []):
        link = str(item.get("link") or "").strip()
        if str(item.get("icon") or "").lower() != "pdf" or not link:
            continue
        if link.startswith("//"):
            link = f"https:{link}"
        documents.append({
            "url": urljoin(ir_url, link),
            "title": str(item.get("title") or link.rsplit("/", 1)[-1]).strip(),
            "published": str(item.get("published") or ""),
            "term_end": str(item.get("term_end") or ""),
            "quarter": str(item.get("quarter") or ""),
            "category": str(item.get("category_name") or ""),
        })
    return documents


def _fetch_xj_storage_documents(
    html: str, ir_url: str, session=requests, timeout: int = 20,
) -> list[dict[str, str]]:
    """XJ-Storageで動的表示される公式IR資料を公開JSONから取得する。"""
    match = re.search(
        r"(?:https?:)?//www\.xj-storage\.jp/resources/([0-9A-Za-z]+)/[^\"']+\.js",
        html,
        flags=re.I,
    )
    if not match:
        return []
    company_id = match.group(1)
    try:
        response = session.get(
            "https://www.xj-storage.jp/public-list/GetList2.aspx",
            params={"company": company_id, "len": 10000, "output": "json"},
            headers={"User-Agent": USER_AGENT, "Referer": ir_url},
            timeout=timeout,
        )
        response.raise_for_status()
        payload = json.loads(_response_text(response))
    except (requests.RequestException, json.JSONDecodeError, TypeError):
        return []
    documents: list[dict[str, str]] = []
    for item in payload.get("items", []):
        files = item.get("files") or []
        pdf = next(
            (row for row in files if row.get("type") == "PDF-GENERAL" and row.get("url")),
            next((row for row in files if str(row.get("type", "")).startswith("PDF") and row.get("url")), None),
        )
        if not pdf:
            continue
        documents.append({
            "url": str(pdf["url"]),
            "title": str(item.get("title") or "公式IR資料").strip(),
            "published": str(item.get("publishDate") or ""),
            "category": str(item.get("categoryName") or ""),
        })
    return documents


def fetch_ir_documents(ir_url: str, refresh: bool = False, session=requests, timeout: int = 20) -> list[dict[str, str]]:
    """公式IRページのPDF一覧を取得し、通信失敗時はHTMLキャッシュへ戻る。"""
    html_path = cache_path(ir_url, ".html")
    documents_path = cache_path(ir_url, ".documents.json")
    if documents_path.exists() and not refresh:
        try:
            return json.loads(documents_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    try:
        if html_path.exists() and not refresh:
            html = html_path.read_text(encoding="utf-8")
        else:
            response = session.get(ir_url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
            response.raise_for_status()
            html = _response_text(response)
            html_path.write_text(html, encoding="utf-8")
    except requests.RequestException as exc:
        if html_path.exists():
            html = html_path.read_text(encoding="utf-8")
        else:
            raise DataSourceError("企業公式IRページを取得できませんでした。URLまたは通信を確認してください。") from exc

    parser = _PdfLinkParser()
    parser.feed(html)
    documents: list[dict[str, str]] = []
    seen: set[str] = set()
    for document in parser.documents:
        url = urljoin(ir_url, document["url"])
        if url in seen:
            continue
        seen.add(url)
        documents.append({"url": url, "title": document["title"] or url.rsplit("/", 1)[-1]})
    for document in _fetch_eir_v5_documents(html, ir_url, session=session, timeout=timeout):
        if document["url"] in seen:
            continue
        seen.add(document["url"])
        documents.append(document)
    for document in _fetch_eir_documents(html, ir_url, session=session, timeout=timeout):
        if document["url"] in seen:
            continue
        seen.add(document["url"])
        documents.append(document)
    for document in _fetch_irpocket_documents(html, ir_url, session=session, timeout=timeout):
        if document["url"] in seen:
            continue
        seen.add(document["url"])
        documents.append(document)
    for document in _fetch_xj_storage_documents(html, ir_url, session=session, timeout=timeout):
        if document["url"] in seen:
            continue
        seen.add(document["url"])
        documents.append(document)
    if documents:
        documents_path.write_text(json.dumps(documents, ensure_ascii=False), encoding="utf-8")
    elif documents_path.exists():
        try:
            return json.loads(documents_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
    return documents


def fetch_ir_pdf_links(ir_url: str, session=requests, timeout: int = 20) -> list[str]:
    """後方互換用。新しい処理では表示名付きのfetch_ir_documentsを使う。"""
    return [
        row["url"]
        for row in fetch_ir_documents(ir_url, refresh=session is not requests, session=session, timeout=timeout)
    ]


def download_pdf(url: str, refresh: bool = False, session=requests, timeout: int = 30) -> tuple[bytes, dict]:
    path, metadata_path = cache_path(url, ".pdf"), cache_path(url, ".json")
    if path.exists() and not refresh:
        return path.read_bytes(), json.loads(metadata_path.read_text(encoding="utf-8"))
    try:
        response = session.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
        response.raise_for_status()
    except requests.RequestException as exc:
        raise DataSourceError("PDFをダウンロードできませんでした。前回キャッシュまたは手入力を利用してください。") from exc
    if not response.content.startswith(b"%PDF"):
        raise DataSourceError("取得したファイルはPDFではありません。")
    metadata = {"source_url": url, "retrieved_at": datetime.now().astimezone().isoformat(), "source_name": url.rsplit("/", 1)[-1]}
    path.write_bytes(response.content)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False), encoding="utf-8")
    return response.content, metadata
