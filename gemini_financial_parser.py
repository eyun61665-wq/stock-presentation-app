"""Gemini API無料枠を利用した決算短信PDFの補助解析。"""
from __future__ import annotations

import base64
import json
from typing import Any

import requests

from ai_financial_parser import FINANCIAL_SCHEMA, PROMPT, normalize_ai_result


class GeminiFinancialParserError(RuntimeError):
    """Gemini解析を継続できない場合の日本語表示用例外。"""


def _response_text(payload: dict[str, Any]) -> str:
    for candidate in payload.get("candidates", []):
        for part in candidate.get("content", {}).get("parts", []):
            if isinstance(part.get("text"), str):
                return part["text"]
    raise GeminiFinancialParserError("Geminiの応答本文を読み取れませんでした。")


class GeminiFinancialParser:
    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.5-flash",
        session: requests.Session | None = None,
        timeout: float = 180.0,
    ) -> None:
        if not api_key.strip():
            raise GeminiFinancialParserError("GEMINI_API_KEYが設定されていません。")
        self.api_key = api_key.strip()
        self.model = model.strip() or "gemini-2.5-flash"
        self.session = session or requests.Session()
        self.timeout = timeout

    def parse_pdf(self, content: bytes, filename: str, source_url: str = "") -> dict[str, Any]:
        if not content.startswith(b"%PDF"):
            raise GeminiFinancialParserError("PDF形式を確認できませんでした。")
        if len(content) > 20 * 1024 * 1024:
            raise GeminiFinancialParserError("PDFが20MBを超えるためGemini解析できません。")
        encoded = base64.b64encode(content).decode("ascii")
        request_body = {
            "contents": [{
                "role": "user",
                "parts": [
                    {"inlineData": {"mimeType": "application/pdf", "data": encoded}},
                    {"text": PROMPT},
                ],
            }],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseJsonSchema": FINANCIAL_SCHEMA,
                "temperature": 0,
            },
        }
        try:
            response = self.session.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent",
                headers={"x-goog-api-key": self.api_key, "Content-Type": "application/json"},
                json=request_body,
                timeout=(10, self.timeout),
            )
        except requests.Timeout as exc:
            raise GeminiFinancialParserError("Gemini解析がタイムアウトしました。") from exc
        except requests.RequestException as exc:
            raise GeminiFinancialParserError("Gemini解析の通信に失敗しました。") from exc
        if response.status_code in (401, 403):
            raise GeminiFinancialParserError("Gemini APIキーまたは利用地域の設定を確認してください。")
        if response.status_code == 429:
            raise GeminiFinancialParserError("Gemini無料枠の利用上限に達しました。時間をおいて再実行してください。")
        if not response.ok:
            raise GeminiFinancialParserError(f"Gemini解析に失敗しました（HTTP {response.status_code}）。")
        try:
            structured = json.loads(_response_text(response.json()))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            raise GeminiFinancialParserError("Geminiの構造化結果を読み取れませんでした。") from exc
        return normalize_ai_result(structured, filename, source_url, source_prefix="Gemini解析")
