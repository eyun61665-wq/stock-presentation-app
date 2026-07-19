import json

import pytest

from gemini_financial_parser import GeminiFinancialParser, GeminiFinancialParserError


class FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.ok = 200 <= status_code < 300

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, response):
        self.response = response
        self.request = None

    def post(self, url, **kwargs):
        self.request = {"url": url, **kwargs}
        return self.response


class SequenceSession:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.urls = []

    def post(self, url, **kwargs):
        self.urls.append(url)
        return next(self.responses)


def test_gemini_pdf_request_uses_api_key_pdf_and_structured_output():
    structured = {
        "pl_records": [],
        "segment_records": [{
            "fiscal_year": "2025-03-31", "result_type": "実績",
            "segment_name": "セキュリティ事業", "sales": 8801,
            "operating_profit": 1200, "source_page": 8,
        }],
        "segment_metrics": [],
        "company_profile": {
            "business_description": "電力機器を製造する。", "strengths": "保守基盤",
            "investment_thesis": "更新需要", "catalysts": "スマートメーター",
            "risks": "設備投資負担", "source_page": 4,
        },
        "catalyst_candidates": [{
            "name": "設備更新", "rationale": "中期計画に記載", "company_impact": "売上拡大",
            "confidence": "高", "source_page": 9,
        }],
        "warnings": [],
    }
    response = FakeResponse(200, {
        "candidates": [{"content": {"parts": [{"text": json.dumps(structured)}]}}]
    })
    session = FakeSession(response)
    parser = GeminiFinancialParser("gemini-key", model="test-model", session=session)

    result = parser.parse_pdf(b"%PDF-1.7 sample", "決算短信.pdf", "https://example.com/a.pdf")

    assert session.request["url"].endswith("/models/test-model:generateContent")
    assert session.request["headers"]["x-goog-api-key"] == "gemini-key"
    body = session.request["json"]
    assert body["contents"][0]["parts"][0]["inlineData"]["mimeType"] == "application/pdf"
    assert body["generationConfig"]["responseMimeType"] == "application/json"
    assert body["generationConfig"]["responseJsonSchema"]["type"] == "object"
    assert result["segment_records"][0]["source"] == "Gemini解析：決算短信.pdf p.8"
    assert "電力機器" in result["company_profile"]["business_description"]
    assert "p.4" in result["company_profile"]["business_description"]
    assert result["catalyst_candidates"][0]["company_impact"] == "売上拡大"


def test_gemini_free_limit_error_is_japanese():
    parser = GeminiFinancialParser(
        "key", session=FakeSession(FakeResponse(429, {"error": "quota"}))
    )
    with pytest.raises(GeminiFinancialParserError, match="無料枠"):
        parser.parse_pdf(b"%PDF-1.7 sample", "決算短信.pdf")


def test_gemini_missing_key_does_not_start_parser():
    with pytest.raises(GeminiFinancialParserError, match="GEMINI_API_KEY"):
        GeminiFinancialParser("")


def test_gemini_retries_a_current_free_model_when_configured_model_is_missing():
    structured = {"pl_records": [], "segment_records": [], "segment_metrics": [], "warnings": []}
    session = SequenceSession([
        FakeResponse(404, {"error": {"message": "model not found"}}),
        FakeResponse(200, {"candidates": [{"content": {"parts": [{"text": json.dumps(structured)}]}}]}),
    ])
    parser = GeminiFinancialParser("key", model="old-model", session=session)

    parser.parse_pdf(b"%PDF-1.7 sample", "決算短信.pdf")

    assert session.urls[0].endswith("/models/old-model:generateContent")
    assert session.urls[1].endswith("/models/gemini-3.5-flash:generateContent")
