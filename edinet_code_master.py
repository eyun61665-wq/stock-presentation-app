"""金融庁公式EDINETコードリストをキャッシュし、証券コードと自動対応する。"""
from __future__ import annotations

import csv
import io
import time
import zipfile
from pathlib import Path
from typing import Any

import requests


MASTER_URL = "https://disclosure2dl.edinet-fsa.go.jp/searchdocument/codelist/Edinetcode.zip"
CACHE_PATH = Path("data_cache/edinet/Edinetcode.zip")
USER_AGENT = "StockPresentationApp/1.0 (personal financial research)"


class EDINETMasterError(RuntimeError):
    pass


def parse_edinet_code_list(content: bytes) -> list[dict[str, str]]:
    """公式ZIP内CSVを上場会社の検索しやすい辞書へ変換する。"""
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            csv_name = next(name for name in archive.namelist() if name.lower().endswith(".csv"))
            text = archive.read(csv_name).decode("cp932")
    except (zipfile.BadZipFile, StopIteration, UnicodeDecodeError) as exc:
        raise EDINETMasterError("EDINETコードリストを読み込めませんでした。") from exc
    lines = text.splitlines()
    if len(lines) < 3:
        return []
    rows: list[dict[str, str]] = []
    for row in csv.DictReader(lines[1:]):
        stock_code5 = str(row.get("証券コード", "")).strip()
        if not stock_code5 or str(row.get("上場区分", "")).strip() != "上場":
            continue
        rows.append({
            "edinet_code": str(row.get("ＥＤＩＮＥＴコード", "")).strip(),
            "stock_code": stock_code5[:4],
            "stock_code5": stock_code5,
            "company_name": str(row.get("提出者名", "")).strip(),
            "fiscal_year_end": str(row.get("決算日", "")).strip(),
            "industry": str(row.get("提出者業種", "")).strip(),
        })
    return rows


def load_edinet_code_master(
    refresh: bool = False,
    cache_path: Path = CACHE_PATH,
    session: Any = requests,
    max_age_days: int = 30,
) -> tuple[list[dict[str, str]], bool]:
    """最新版を試し、失敗時は前回キャッシュを使う。戻り値のboolは旧キャッシュ利用。"""
    cache_path = Path(cache_path)
    fresh = cache_path.exists() and time.time() - cache_path.stat().st_mtime < max_age_days * 86400
    stale_fallback = False
    if refresh or not fresh:
        try:
            response = session.get(MASTER_URL, headers={"User-Agent": USER_AGENT}, timeout=30)
            response.raise_for_status()
            parse_edinet_code_list(response.content)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            cache_path.write_bytes(response.content)
        except (requests.RequestException, OSError, EDINETMasterError) as exc:
            if not cache_path.exists():
                raise EDINETMasterError("EDINETコードリストを取得できませんでした。") from exc
            stale_fallback = True
    try:
        return parse_edinet_code_list(cache_path.read_bytes()), stale_fallback
    except OSError as exc:
        raise EDINETMasterError("EDINETコードリストのキャッシュを読み込めませんでした。") from exc


def find_edinet_company(rows: list[dict[str, str]], stock_code: str) -> dict[str, str] | None:
    normalized = "".join(character for character in str(stock_code) if character.isdigit())[:4]
    return next((row for row in rows if row["stock_code"] == normalized), None)
