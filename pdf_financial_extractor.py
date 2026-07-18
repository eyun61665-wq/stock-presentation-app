"""決算短信PDFの確認用テキスト抽出。数値は候補としてのみ返す。"""
from __future__ import annotations

import re
from io import BytesIO

try:
    from pypdf import PdfReader
except ImportError:  # 実行環境に未導入でも手入力を止めない
    PdfReader = None


def extract_pdf_preview(content: bytes, max_pages: int = 8) -> dict:
    if PdfReader is None:
        return {"text": "PDF抽出機能が未導入です。手入力を利用してください。", "candidates": {}}
    reader = PdfReader(BytesIO(content))
    text = "\n".join((page.extract_text() or "") for page in reader.pages[:max_pages])
    labels = {"売上高": "sales", "営業利益": "operating_profit", "親会社株主に帰属する当期純利益": "net_income", "1株当たり当期純利益": "eps"}
    candidates = {}
    for label, key in labels.items():
        match = re.search(label + r"[^\d\n]{0,30}([\d,]+(?:\.\d+)?)", text)
        if match:
            candidates[key] = match.group(1)
    return {"text": text[:12000], "candidates": candidates}


def extract_pdf_pages(content: bytes, max_pages: int = 30) -> list[str]:
    """ページ番号を保った通常抽出用。画像PDFは空文字となり、推測値を返さない。"""
    if PdfReader is None:
        return []
    reader = PdfReader(BytesIO(content))
    return [(page.extract_text() or "") for page in reader.pages[:max_pages]]
