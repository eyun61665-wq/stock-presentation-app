"""JPX東証上場銘柄一覧の取得・ローカルキャッシュ・検索。"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import urljoin

import pandas as pd
import requests

from data_normalizer import canonical_company_name

JPX_PAGE_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/01.html"
CACHE_DIR = Path(__file__).resolve().parent / "data_cache"
CACHE_XLSX = CACHE_DIR / "jpx_listed_issues.xlsx"
CACHE_JSON = CACHE_DIR / "jpx_listed_issues.json"
CACHE_TTL_SECONDS = 7 * 24 * 60 * 60


class JPXMasterError(Exception):
    pass


def cache_is_fresh(path: Path = CACHE_XLSX) -> bool:
    return path.exists() and time.time() - path.stat().st_mtime < CACHE_TTL_SECONDS


def _find_excel_url(html: str) -> str:
    match = re.search(r'href="([^"]+(?:data_j|listed)[^"]*\.(?:xls|xlsx))"', html, flags=re.I)
    if not match:
        raise JPXMasterError("JPX上場銘柄一覧のExcelリンクが見つかりませんでした。")
    return urljoin(JPX_PAGE_URL, match.group(1))


def refresh_master(session=requests, timeout: int = 30) -> Path:
    try:
        page = session.get(JPX_PAGE_URL, timeout=timeout)
        page.raise_for_status()
        excel = session.get(_find_excel_url(page.text), timeout=timeout)
        excel.raise_for_status()
    except requests.RequestException as exc:
        raise JPXMasterError("JPX企業マスターを取得できませんでした。前回キャッシュまたは手入力を利用してください。") from exc
    CACHE_DIR.mkdir(exist_ok=True)
    CACHE_XLSX.write_bytes(excel.content)
    load_master(CACHE_XLSX, write_json=True)
    return CACHE_XLSX


def load_master(path: Path = CACHE_XLSX, write_json: bool = False) -> list[dict]:
    try:
        # キャッシュ名が .xlsx でも、JPX配布元が .xls の場合があるため両方を試す。
        try:
            frame = pd.read_excel(path, dtype=str, engine="openpyxl")
        except Exception:
            frame = pd.read_excel(path, dtype=str, engine="xlrd")
    except Exception as exc:
        raise JPXMasterError("JPX企業マスターExcelを読み込めませんでした。") from exc
    aliases = {"コード": "stock_code", "銘柄名": "company_name", "市場・商品区分": "market", "33業種コード": "sector33_code", "33業種区分": "sector33", "規模区分": "scale"}
    frame = frame.rename(columns={column: aliases.get(str(column).strip(), column) for column in frame.columns})
    required = ["stock_code", "company_name"]
    if any(column not in frame.columns for column in required):
        raise JPXMasterError("JPX企業マスターの必須列（コード・銘柄名）が見つかりませんでした。")
    for column in ["market", "sector33_code", "sector33", "scale"]:
        if column not in frame.columns:
            frame[column] = ""
    rows = [{key: ("" if pd.isna(value) else str(value).strip()) for key, value in row.items()}
            for row in frame[["stock_code", "company_name", "market", "sector33_code", "sector33", "scale"]].to_dict("records")]
    if write_json:
        CACHE_JSON.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return rows


def get_master(refresh: bool = False, session=requests) -> tuple[list[dict], str]:
    if refresh or not cache_is_fresh():
        try:
            refresh_master(session)
        except JPXMasterError:
            if not CACHE_XLSX.exists():
                raise
    if not CACHE_XLSX.exists():
        raise JPXMasterError("JPX企業マスターのキャッシュがありません。ネットワーク接続を確認するか手入力してください。")
    return load_master(), "JPX"


def search_master(rows: list[dict], query: str) -> list[dict]:
    term = canonical_company_name(query)
    if not term:
        return []
    results = []
    for row in rows:
        code = str(row["stock_code"]).zfill(4)[:4]
        if (term.isdigit() and code == term.zfill(4)[:4]) or (not term.isdigit() and term in canonical_company_name(row["company_name"])):
            results.append({**row, "stock_code": code})
    if term.isdigit():
        return results
    # 完全一致、前方一致、短い会社名の順に並べ、近い候補を選びやすくする。
    return sorted(
        results,
        key=lambda row: (
            canonical_company_name(row["company_name"]) != term,
            not canonical_company_name(row["company_name"]).startswith(term),
            abs(len(canonical_company_name(row["company_name"])) - len(term)),
            str(row["stock_code"]),
        ),
    )
