from catalyst_research_service import classify_context, parse_news_rss
from company_profile_service import fetch_company_profile, fill_empty_profile


class Response:
    content = (
        '<meta name="description" content="サイバーセキュリティ教育とコンサルティングを提供します。">'
    ).encode("utf-8")
    text = content.decode("utf-8")

    def raise_for_status(self):
        return None


class Session:
    def get(self, url, **kwargs):
        return Response()


def test_official_description_fills_only_empty_project_fields():
    candidates = fetch_company_profile(
        "https://example.test/ir", "テスト社", "情報・通信", session=Session()
    )
    updated = fill_empty_profile({"business_description": "", "strengths": "手入力済み"}, candidates)
    assert "サイバーセキュリティ教育" in updated["business_description"]
    assert updated["strengths"] == "手入力済み"
    assert updated.get("catalysts", "") == ""


def test_profile_has_safe_fallback_when_official_page_is_unavailable():
    candidates = fetch_company_profile("", "テスト製造", "機械")
    assert candidates["business_description"] == ""
    assert candidates["strengths"] == ""


def test_news_rss_keeps_source_url_and_classifies_rule_change():
    xml = '''<rss><channel><item><title>業界ガイドラインを改正</title>
      <link>https://example.go.jp/news</link><pubDate>Sun, 19 Jul 2026</pubDate>
      <source>官公庁</source></item></channel></rss>'''.encode("utf-8")
    rows = parse_news_rss(xml)
    assert rows[0]["url"] == "https://example.go.jp/news"
    assert rows[0]["category"] == "法令・ルール変更"
    assert classify_context("AI技術を導入") == "技術進化"
