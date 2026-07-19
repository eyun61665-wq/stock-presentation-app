"""無料の公開ニュースRSSから企業周辺・技術・制度変更の候補を集める。"""
from __future__ import annotations

from xml.etree import ElementTree

import requests

from data_sources import USER_AGENT

NEWS_RSS_URL = "https://news.google.com/rss/search"


def classify_context(title: str) -> str:
    if any(word in title for word in ("法改正", "改正", "規制", "制度", "義務", "ガイドライン", "基準", "ルール")):
        return "法令・ルール変更"
    if any(word.lower() in title.lower() for word in ("ai", "技術", "次世代", "自動化", "dx", "量子", "半導体")):
        return "技術進化"
    return "企業・市場動向"


def parse_news_rss(content: bytes, limit: int = 8) -> list[dict[str, str]]:
    root = ElementTree.fromstring(content)
    output: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in root.findall("./channel/item"):
        title = " ".join((item.findtext("title") or "").split())
        link = (item.findtext("link") or "").strip()
        if not title or not link or title in seen:
            continue
        seen.add(title)
        output.append({
            "title": title,
            "url": link,
            "published": (item.findtext("pubDate") or "").strip(),
            "category": classify_context(title),
            "source": (item.findtext("source") or "公開ニュース").strip(),
        })
        if len(output) >= limit:
            break
    return output


def research_catalyst_context(
    company_name: str, sector: str = "", *, session=requests, timeout: int = 8, limit: int = 8,
) -> list[dict[str, str]]:
    """会社固有情報と制度・技術情報を検索する。失敗時は空配列で手入力を継続する。"""
    queries = [
        f'"{company_name}" (新サービス OR 提携 OR 投資 OR 技術 OR 規制)',
        f'{sector or company_name} (法改正 OR 規制 OR ガイドライン OR 技術進化)',
    ]
    results: list[dict[str, str]] = []
    seen: set[str] = set()
    for query in queries:
        try:
            response = session.get(
                NEWS_RSS_URL,
                params={"q": query, "hl": "ja", "gl": "JP", "ceid": "JP:ja"},
                headers={"User-Agent": USER_AGENT},
                timeout=timeout,
            )
            response.raise_for_status()
            rows = parse_news_rss(response.content, limit=limit)
        except (requests.RequestException, ElementTree.ParseError, ValueError):
            continue
        for row in rows:
            if row["title"] in seen:
                continue
            seen.add(row["title"])
            results.append(row)
            if len(results) >= limit:
                return results
    return results
