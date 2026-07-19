import json

import pytest

from ai_financial_parser import AIFinancialParserError, OpenAIFinancialParser


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


def test_ai_pdf_request_uses_structured_output_and_normalizes_segments():
    structured = {
        "pl_records": [],
        "segment_records": [{
            "fiscal_year": "2025-03-31", "result_type": "実績",
            "segment_name": "サイバーセキュリティ事業", "sales": 8801,
            "operating_profit": 1200, "source_page": 8,
        }],
        "segment_metrics": [{
            "fiscal_year": "2025-03-31", "result_type": "実績",
            "row_label": "契約会社数", "value": 1443, "unit": "社",
            "display_order": 1, "source_page": 9,
        }],
        "warnings": [],
    }
    response = FakeResponse(200, {
        "output": [{"content": [{"type": "output_text", "text": json.dumps(structured)}]}]
    })
    session = FakeSession(response)
    parser = OpenAIFinancialParser("secret-key", model="test-model", session=session)

    result = parser.parse_pdf(b"%PDF-1.7 sample", "決算短信.pdf", "https://example.com/a.pdf")

    assert session.request["url"] == "https://api.openai.com/v1/responses"
    assert session.request["headers"]["Authorization"] == "Bearer secret-key"
    body = session.request["json"]
    assert body["model"] == "test-model"
    assert body["input"][0]["content"][1]["type"] == "input_file"
    assert body["text"]["format"]["type"] == "json_schema"
    assert result["segment_records"][0]["sales"] == 8801
    assert result["segment_records"][0]["source"].endswith("p.8")
    assert result["segment_metrics"][0]["unit"] == "社"


def test_ai_auth_error_is_japanese_and_does_not_return_fabricated_data():
    parser = OpenAIFinancialParser(
        "bad-key", session=FakeSession(FakeResponse(401, {"error": "unauthorized"}))
    )
    with pytest.raises(AIFinancialParserError, match="APIキー"):
        parser.parse_pdf(b"%PDF-1.7 sample", "決算短信.pdf")
