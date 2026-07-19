"""EDINET API Version 2。キーは呼び出し時だけ使用し保存しない。"""
from __future__ import annotations

from datetime import date
from typing import Any

import requests

BASE_URL = "https://api.edinet-fsa.go.jp/api/v2"


class EDINETError(Exception):
    pass


class EDINETClient:
    def __init__(self, api_key: str | None = None, session: Any = requests, timeout: int = 30) -> None:
        self.api_key, self.session, self.timeout = api_key, session, timeout

    @property
    def headers(self) -> dict[str, str]:
        return {"Ocp-Apim-Subscription-Key": self.api_key} if self.api_key else {}

    def documents(self, target_date: date) -> list[dict]:
        params = {"date": target_date.isoformat(), "type": 2}
        if self.api_key:
            params["Subscription-Key"] = self.api_key
        try:
            response = self.session.get(
                f"{BASE_URL}/documents.json", params=params,
                headers={"User-Agent": "StockPresentationApp/1.0"}, timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise EDINETError("EDINET API通信がタイムアウトしました。") from exc
        except requests.RequestException as exc:
            raise EDINETError("EDINET APIへ接続できませんでした。") from exc
        if response.status_code in (401, 403):
            raise EDINETError("EDINET APIキーを確認してください（認証エラー）。")
        if response.status_code >= 400:
            raise EDINETError(f"EDINET APIエラー（HTTP {response.status_code}）。")
        return response.json().get("results", [])

    def xbrl_zip(self, doc_id: str) -> bytes:
        params: dict[str, Any] = {"type": 1}
        if self.api_key:
            params["Subscription-Key"] = self.api_key
        try:
            response = self.session.get(
                f"{BASE_URL}/documents/{doc_id}", params=params,
                headers={"User-Agent": "StockPresentationApp/1.0"}, timeout=self.timeout,
            )
        except requests.Timeout as exc:
            raise EDINETError("EDINET XBRL取得がタイムアウトしました。") from exc
        except requests.RequestException as exc:
            raise EDINETError("EDINET XBRLを取得できませんでした。") from exc
        if response.status_code >= 400:
            raise EDINETError("EDINET XBRLを取得できませんでした。")
        return response.content


def annual_reports(documents: list[dict], edinet_code: str) -> list[dict]:
    """有価証券報告書（120）を対象企業だけに絞る。"""
    return [row for row in documents if row.get("edinetCode") == edinet_code and str(row.get("docTypeCode")) == "120"]
