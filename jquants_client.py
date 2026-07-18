"""J-Quants API V2 の薄いHTTPクライアント。APIキーは保持・保存しない。"""
from __future__ import annotations

from typing import Any

import requests

BASE_URL = "https://api.jquants.com/v2"


class JQuantsApiError(Exception):
    """画面へそのまま表示できる日本語のAPIエラー。"""


class JQuantsClient:
    def __init__(self, api_key: str, session: Any = requests, timeout: int = 20) -> None:
        if not api_key:
            raise JQuantsApiError("J-Quants APIキーが設定されていません。")
        if not api_key.isascii():
            raise JQuantsApiError("APIキーに日本語などの使用できない文字が含まれています。検索語ではなくJ-QuantsのAPIキーを設定してください。")
        self.api_key = api_key
        self.session = session
        self.timeout = timeout

    def get_all(self, path: str, params: dict[str, Any] | None = None) -> list[dict]:
        rows: list[dict] = []
        query = dict(params or {})
        while True:
            try:
                # 会社名などの検索語をヘッダーへ渡さない。マスター取得はparamsなしで行う。
                response = self.session.get(f"{BASE_URL}{path}", params=query,
                                            headers={"x-api-key": self.api_key, "Accept": "application/json"}, timeout=self.timeout)
            except requests.Timeout as exc:
                raise JQuantsApiError("J-Quants APIへの通信がタイムアウトしました。時間をおいて再試行してください。") from exc
            except requests.RequestException as exc:
                raise JQuantsApiError("J-Quants APIへ接続できませんでした。ネットワーク接続を確認してください。") from exc
            except UnicodeError as exc:
                raise JQuantsApiError("API通信時の文字コードエラーです。検索語はローカルで照合するため、APIキー設定を確認してください。") from exc
            self._raise_for_status(response)
            body = response.json()
            rows.extend(body.get("data", []))
            pagination_key = body.get("pagination_key")
            if not pagination_key:
                return rows
            query["pagination_key"] = pagination_key

    @staticmethod
    def _raise_for_status(response: Any) -> None:
        messages = {401: "APIキーを確認してください（認証エラー）。", 404: "該当するデータが見つかりません。",
                    429: "APIの利用回数上限に達しました。時間をおいて再試行してください。"}
        if response.status_code in messages:
            raise JQuantsApiError(messages[response.status_code])
        if response.status_code >= 400:
            raise JQuantsApiError(f"J-Quants APIでエラーが発生しました（HTTP {response.status_code}）。")

    def equities_master(self) -> list[dict]:
        return self.get_all("/equities/master")

    def daily_bars(self, code: str) -> list[dict]:
        return self.get_all("/equities/bars/daily", {"code": code})

    def financial_summary(self, code: str) -> list[dict]:
        return self.get_all("/fins/summary", {"code": code})
