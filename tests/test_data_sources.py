from data_sources import cache_path, download_pdf, fetch_ir_documents, fetch_ir_pdf_links


class Response:
    def __init__(self, text="", content=b"", status=200):
        self.text, self.content, self.status_code = text, content, status
    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError("error")


class Session:
    def __init__(self): self.calls = []
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith("ir"):
            return Response('<a href="/files/earnings.pdf">資料</a>')
        return Response(content=b"%PDF-1.4 test")


def test_ir_pdf_links_use_official_page_and_user_agent():
    session = Session()
    links = fetch_ir_pdf_links("https://example.test/ir", session=session)
    assert links == ["https://example.test/files/earnings.pdf"]
    assert "User-Agent" in session.calls[0][1]["headers"]


def test_pdf_cache_read_write(monkeypatch, tmp_path):
    import data_sources
    monkeypatch.setattr(data_sources, "CACHE_DIR", tmp_path)
    session = Session()
    first, metadata = download_pdf("https://example.test/files/earnings.pdf", session=session)
    second, cached = download_pdf("https://example.test/files/earnings.pdf", session=session)
    assert first == second
    assert len(session.calls) == 1
    assert cached["source_url"] == metadata["source_url"]


def test_eir_code_can_be_loaded_from_external_script(monkeypatch, tmp_path):
    import data_sources

    monkeypatch.setattr(data_sources, "CACHE_DIR", tmp_path)

    class EirSession:
        def get(self, url, **kwargs):
            if url == "https://example.test/ir/":
                return Response(
                    '<a href="https://ssl4.eir-parts.net/doc/3355/new_release/2.zip">IR</a>'
                    '<script src="/assets/js/eir.js"></script>'
                )
            if url == "https://example.test/assets/js/eir.js":
                return Response('var eirCode = "3355";')
            if "announcement_2.js" in url:
                return Response(
                    'callback({"item":[{"type":"pdf","link":"https://example.test/fy.pdf",'
                    '"title":"2025年12月期決算短信","news_type":"tanshin"}]});'
                )
            return Response('callback({"item":[]});')

    documents = fetch_ir_documents(
        "https://example.test/ir/", refresh=True, session=EirSession()
    )
    assert documents == [
        {"url": "https://example.test/fy.pdf", "title": "2025年12月期決算短信"}
    ]
