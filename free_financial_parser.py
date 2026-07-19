"""無料のPDF表解析。資料に明示された数値だけをPL・セグメントへ変換する。"""
from __future__ import annotations

import re
import unicodedata
from io import BytesIO
from typing import Any

from financial_parser import detect_unit, normalize_japanese_number

try:
    import pdfplumber
except ImportError:  # 未導入でも既存のpypdf解析は継続できる
    pdfplumber = None


PL_LABELS: dict[str, tuple[str, ...]] = {
    "sales": ("売上高", "売上収益", "営業収益", "収益", "revenue"),
    "cost_of_sales": ("売上原価", "営業原価", "costofsales"),
    "gross_profit": ("売上総利益", "売上総損失", "grossprofit"),
    "sga_expenses": ("販売費及び一般管理費", "販管費", "sellinggeneraladministrativeexpenses"),
    "operating_profit": ("営業利益", "営業損失", "事業利益", "operatingprofit"),
    "non_operating_income": ("営業外収益",),
    "non_operating_expenses": ("営業外費用",),
    "ordinary_profit": ("経常利益", "経常損失", "ordinaryprofit"),
    "extraordinary_income": ("特別利益",),
    "extraordinary_loss": ("特別損失",),
    "pretax_profit": (
        "税引前利益", "税引前当期純利益", "税金等調整前当期純利益",
        "税金等調整前当期純損失", "profitbeforetax",
    ),
    "income_taxes": ("法人税等合計", "法人所得税費用", "法人税等", "incometaxexpense"),
    "net_income": (
        "親会社株主に帰属する当期純利益", "親会社の所有者に帰属する当期利益",
        "当期純利益", "当期利益", "profitattributabletoownersofparent",
    ),
    "reported_eps": ("1株当たり当期純利益", "基本的1株当たり利益", "基本的1株当たり当期利益", "eps"),
    "average_shares": ("期中平均株式数", "普通株式の期中平均株式数", "加重平均株式数"),
}

KPI_HINTS = (
    "契約", "顧客", "店舗", "拠点", "会員", "ユーザー", "利用者", "アカウント",
    "件数", "社数", "単価", "台数", "人員", "従業員", "席数", "室数", "面積",
)
KPI_EXCLUDED_WORDS = (
    "増減額", "売上債権", "契約負債", "その主な", "千円等", "百万円等",
    "増収効果", "人件費", "前払費用", "投資有価証券", "買掛金",
)
SEGMENT_HINTS = ("事業", "セグメント", "部門", "地域", "サービス")
TOTAL_LABELS = ("合計", "計", "全社", "調整額", "消去", "その他")
NUMBER_RE = re.compile(r"[△▲▲(（-]?\s*[\d０-９][\d０-９,，]*(?:[.．]\d+)?\s*[)）]?")


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(unicodedata.normalize("NFKC", str(value)).replace("\u3000", " ").split())


def _compact(value: Any) -> str:
    return re.sub(r"[\s:：・･()（）\[\]【】/／]", "", _clean(value)).lower()


def _label_key(label: str) -> str | None:
    compact = _compact(label)
    matches: list[tuple[int, str]] = []
    for key, aliases in PL_LABELS.items():
        for alias in aliases:
            normalized = _compact(alias)
            if compact == normalized or compact.startswith(normalized):
                matches.append((len(normalized), key))
    return max(matches, default=(0, ""))[1] or None


def _year(value: Any) -> str | None:
    text = _clean(value)
    matches = re.findall(r"(20\d{2})\s*(?:年|[./-])\s*(\d{1,2})", text)
    if matches:
        # 会計期間の「自～至」が同じセルにある場合は、期末側を採用する。
        year, month = matches[-1]
        return f"{int(year):04d}.{int(month)}"
    match = re.search(r"\b(20\d{2})\b", text)
    return match.group(1) if match else None


def _result_type(value: Any) -> str:
    text = _compact(value)
    if "会社予想" in text or "会社計画" in text:
        return "会社予想"
    if "自分予想" in text:
        return "自分予想"
    if "予想" in text or "計画" in text:
        return "会社予想"
    return "実績"


def _number(value: Any) -> float | None:
    text = _clean(value)
    if not text or text in {"-", "―", "–", "—", "－", "N/A", "n/a"}:
        return None
    match = NUMBER_RE.search(text)
    if not match:
        return None
    token = match.group(0).replace("，", ",").replace("．", ".")
    token = token.replace("▲", "△")
    negative = "△" in token or token.lstrip().startswith("-") or (
        token.strip().startswith(("(", "（")) and token.strip().endswith((")", "）"))
    )
    token = re.sub(r"[^\d.,]", "", token)
    value_number = normalize_japanese_number(token)
    if value_number is None:
        return None
    return -abs(value_number) if negative else value_number


def _is_numeric_cell(value: Any) -> bool:
    text = _clean(value).replace("%", "").replace("％", "")
    return bool(re.fullmatch(r"[△▲(（-]?\s*[\d０-９][\d０-９,，]*(?:[.．]\d+)?\s*[)）]?", text))


def _valid_kpi_label(label: str) -> bool:
    text = _clean(label)
    if not text or len(text) > 35 or "。" in text:
        return False
    if any(word in text for word in KPI_EXCLUDED_WORDS):
        return False
    return any(hint in text for hint in KPI_HINTS)


def _to_million(value: Any, unit: str, shares: bool = False) -> float | None:
    number = _number(value)
    if number is None:
        return None
    if shares:
        if "百万株" in unit:
            return number
        if "千株" in unit:
            return number / 1_000
        return number / 1_000_000
    if "億円" in unit:
        return number * 100
    if "百万円" in unit:
        return number
    if "千円" in unit:
        return number / 1_000
    if unit == "円" or "（円）" in unit:
        return number / 1_000_000
    return number


def extract_pdf_layout(content: bytes, max_pages: int = 60) -> list[dict[str, Any]]:
    """ページテキストと罫線・文字位置に基づく表を同時に抽出する。"""
    if pdfplumber is None:
        return []
    pages: list[dict[str, Any]] = []
    with pdfplumber.open(BytesIO(content)) as document:
        for number, page in enumerate(document.pages[:max_pages], start=1):
            text = page.extract_text(layout=True, x_tolerance=2, y_tolerance=3) or ""
            settings = {
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "snap_tolerance": 4,
                "join_tolerance": 4,
                "intersection_tolerance": 6,
            }
            tables = page.extract_tables(settings) or []
            if not tables:
                tables = page.extract_tables({
                    "vertical_strategy": "text",
                    "horizontal_strategy": "text",
                    "min_words_vertical": 2,
                    "min_words_horizontal": 1,
                    "text_tolerance": 3,
                }) or []
            pages.append({
                "page": number,
                "text": text,
                "tables": [[[_clean(cell) for cell in row] for row in table if row] for table in tables],
                "words": page.extract_words(x_tolerance=2, y_tolerance=3) or [],
                "width": float(page.width),
                "height": float(page.height),
            })
    return pages


def _column_metadata(table: list[list[str]], page_text: str) -> dict[int, tuple[str, str]]:
    width = max((len(row) for row in table), default=0)
    metadata: dict[int, tuple[str, str]] = {}
    header_rows = table[: min(6, len(table))]
    for column in range(1, width):
        header = " ".join(row[column] for row in header_rows if column < len(row) and row[column])
        year = _year(header)
        if year:
            metadata[column] = (year, _result_type(header))
    if metadata:
        return metadata
    page_years: list[str] = []
    for match in re.finditer(r"(20\d{2})\s*(?:年|[./-])\s*(\d{1,2})", page_text):
        value = f"{int(match.group(1)):04d}.{int(match.group(2))}"
        if value not in page_years:
            page_years.append(value)
    if page_years and width > 1:
        columns = list(range(max(1, width - len(page_years)), width))
        for column, year in zip(columns, page_years[-len(columns):]):
            metadata[column] = (year, "実績")
    return metadata


def _source_row(source_name: str, source_url: str, page: int, retrieved_at: str) -> dict[str, Any]:
    return {
        "source": "決算資料PDF（無料解析）",
        "basis_date": "",
        "source_name": source_name,
        "source_url": source_url,
        "page": page,
        "retrieved_at": retrieved_at,
    }


def _table_labels(table: list[list[str]]) -> set[str]:
    """表の先頭列にあるPL科目を返す。注記の単独行はPL表と扱わない。"""
    return {key for row in table if row and (key := _label_key(row[0]))}


def _page_fiscal_year(page_text: str) -> str | None:
    text = unicodedata.normalize("NFKC", page_text)
    ends = re.findall(r"至\s*(20\d{2})\s*年\s*(\d{1,2})\s*月", text)
    if ends:
        year, month = ends[0]
        return f"{int(year):04d}.{int(month)}"
    return _year(text)


def _parse_segment_matrix(
    table: list[list[str]], page_text: str, page_number: int,
    source_name: str, source_url: str, retrieved_at: str,
) -> list[dict[str, Any]]:
    """報告セグメント表から、外部売上高とセグメント利益だけを抽出する。"""
    joined = " ".join(" ".join(row) for row in table)
    if "報告セグメント" not in joined or "セグメント利益" not in joined:
        return []
    fiscal_year = _page_fiscal_year(page_text)
    if not fiscal_year:
        return []
    unit = detect_unit(page_text)
    name_row: list[str] | None = None
    for row in table[:4]:
        candidates = [cell for cell in row if any(hint in cell for hint in SEGMENT_HINTS)]
        if len(candidates) >= 2:
            name_row = row
    if not name_row:
        return []

    total_labels = {_compact(item) for item in TOTAL_LABELS}
    segment_columns: list[tuple[int, str]] = []
    for column, raw_name in enumerate(name_row):
        name = _clean(raw_name)
        if not name or _compact(name) in total_labels:
            continue
        if any(word in name for word in ("報告セグメント", "調整額", "連結財務諸表")):
            continue
        if any(hint in name for hint in SEGMENT_HINTS):
            segment_columns.append((column, name))
    if not segment_columns:
        return []

    values: dict[str, dict[str, float]] = {name: {} for _, name in segment_columns}
    for row in table:
        label = _clean(row[0] if row else "")
        if "外部顧客への売上高" in label:
            field, first_number = "sales", True
        elif _compact(label) == _compact("セグメント利益"):
            field, first_number = "operating_profit", False
        else:
            continue
        for column, name in segment_columns:
            if column >= len(row):
                continue
            raw = row[column]
            if first_number:
                match = NUMBER_RE.search(unicodedata.normalize("NFKC", _clean(raw)))
                raw = match.group(0) if match else raw
            value = _to_million(raw, unit)
            if value is not None:
                values[name][field] = value

    return [
        {
            "fiscal_year": fiscal_year,
            "result_type": "実績",
            "segment_name": name,
            "sales": fields.get("sales"),
            "operating_profit": fields.get("operating_profit"),
            "source": "決算資料PDF（無料解析）",
            "basis_date": "",
            "note": f"{source_name} p.{page_number} {source_url}",
            "retrieved_at": retrieved_at,
        }
        for name, fields in values.items()
        if fields.get("sales") is not None or fields.get("operating_profit") is not None
    ]


def _business_segment_names(page_text: str) -> list[str]:
    """事業別グラフの凡例から、重複しない事業名を表示順で返す。"""
    names: list[str] = []
    for line in page_text.splitlines():
        for part in re.split(r"\s{2,}", line.strip()):
            name = _clean(part)
            if not name.endswith("事業") or len(name) > 30:
                continue
            if any(word in name for word in ("事業別", "事業内容", "主な", "進捗")):
                continue
            if name not in names:
                names.append(name)
    return names


def _chart_year(word: str) -> str | None:
    text = _clean(word)
    match = re.fullmatch(r"(\d{2,4})[./年](\d{1,2})月?期", text)
    if not match:
        return None
    year = int(match.group(1))
    if year < 100:
        year += 2000
    return f"{year:04d}.{int(match.group(2))}"


def _parse_business_sales_chart(
    page: dict[str, Any], source_name: str, source_url: str, retrieved_at: str,
) -> list[dict[str, Any]]:
    """決算説明資料の積み上げグラフから、明示された事業別売上を抽出する。"""
    page_text = str(page.get("text", ""))
    if "事業別売上高" not in page_text or "単位：百万円" not in page_text:
        return []
    names = _business_segment_names(page_text)
    if len(names) < 2:
        return []
    width = float(page.get("width") or 0)
    words = page.get("words") or []
    if width <= 0 or not words:
        return []
    # 左半分が売上高、右半分が売上総利益などの比較グラフを想定する。
    sales_words = [word for word in words if float(word.get("x0", 0)) < width * 0.52]
    year_anchors: list[tuple[float, float, str]] = []
    for word in sales_words:
        fiscal_year = _chart_year(str(word.get("text", "")))
        if fiscal_year:
            year_anchors.append((float(word.get("x0", 0)), float(word.get("top", 0)), fiscal_year))
    if not year_anchors:
        return []

    result_type = _result_type(page_text)
    output: list[dict[str, Any]] = []
    for x_anchor, top_anchor, fiscal_year in year_anchors:
        candidates: list[tuple[float, float]] = []
        for word in sales_words:
            raw = _clean(word.get("text", ""))
            if not re.fullmatch(r"\d[\d,]*", raw):
                continue
            x0, top = float(word.get("x0", 0)), float(word.get("top", 0))
            if abs(x0 - x_anchor) > max(34.0, width * 0.045) or top >= top_anchor - 8:
                continue
            value = _to_million(raw, "百万円")
            if value is not None:
                candidates.append((top, value))
        # 合計値が最上段、その下に凡例順の構成値が並ぶ積み上げグラフ。
        candidates = sorted(set(candidates))
        if len(candidates) < len(names) + 1:
            continue
        segment_values = candidates[-len(names):]
        for name, (_top, value) in zip(names, segment_values):
            output.append({
                "fiscal_year": fiscal_year,
                "result_type": result_type,
                "segment_name": name,
                "sales": value,
                "operating_profit": None,
                "source": "決算資料PDF（無料解析）",
                "basis_date": "",
                "note": f"{source_name} p.{page.get('page', 0)} {source_url}",
                "retrieved_at": retrieved_at,
            })
    return output


def parse_layout_tables(
    layouts: list[dict[str, Any]], source_name: str, source_url: str, retrieved_at: str,
) -> dict[str, list[dict[str, Any]]]:
    """抽出済み表を年度別PL、セグメント、会社固有KPIへ変換する。"""
    pl_by_key: dict[tuple[str, str], dict[str, Any]] = {}
    segments: dict[tuple[str, str, str], dict[str, Any]] = {}
    metrics: dict[tuple[str, str, str], dict[str, Any]] = {}

    for page in layouts:
        page_number = int(page.get("page", 0))
        page_text = str(page.get("text", ""))
        unit = detect_unit(page_text)
        for item in _parse_business_sales_chart(page, source_name, source_url, retrieved_at):
            key = (item["fiscal_year"], item["result_type"], item["segment_name"])
            segments.setdefault(key, item)
        for table in page.get("tables", []):
            if not table or max((len(row) for row in table), default=0) < 2:
                continue
            matrix_segments = _parse_segment_matrix(
                table, page_text, page_number, source_name, source_url, retrieved_at
            )
            for item in matrix_segments:
                key = (item["fiscal_year"], item["result_type"], item["segment_name"])
                segments[key] = item
            columns = _column_metadata(table, page_text)
            if not columns:
                continue
            # 少なくとも2科目を含む損益表だけをPL候補にする。
            table_labels = _table_labels(table)
            is_pl_table = len(table_labels) >= 2 and not matrix_segments
            is_simple_segment_table = (
                not matrix_segments
                and "sales" in table_labels
                and any(
                    re.match(r"^\d{1,2}\s+", _clean(item[0]))
                    for item in table if item and item[0]
                )
            )
            kpi_label_count = sum(
                1 for item in table for cell in item[:1]
                if any(hint in _clean(cell) for hint in KPI_HINTS)
            )
            for order, row in enumerate(table):
                label_cells: list[str] = []
                for cell in row:
                    if _is_numeric_cell(cell):
                        break
                    if cell:
                        label_cells.append(cell)
                    if len(label_cells) >= 2:
                        break
                label = _clean(" ".join(label_cells))
                if not label:
                    continue
                pl_key = _label_key(label)
                row_unit = detect_unit(f"{label} {page_text[:1000]}")
                if pl_key and is_pl_table:
                    for column, (fiscal_year, result_type) in columns.items():
                        if column >= len(row):
                            continue
                        raw = row[column]
                        value = _number(raw) if pl_key == "reported_eps" else _to_million(
                            raw, unit, shares=pl_key == "average_shares"
                        )
                        if value is None:
                            continue
                        record = pl_by_key.setdefault(
                            (fiscal_year, result_type),
                            {"fiscal_year": fiscal_year, "result_type": result_type,
                             **_source_row(source_name, source_url, page_number, retrieved_at)},
                        )
                        record.setdefault(pl_key, value)
                    continue

                compact_label = _compact(label)
                clean_label = re.sub(r"^[①-⑳\d]+[.:：)）・\s]*", "", label).strip()
                is_total = any(compact_label == _compact(item) for item in TOTAL_LABELS)
                is_segment = (
                    not is_total
                    and any(hint in clean_label for hint in SEGMENT_HINTS)
                    and not any(word in clean_label for word in ("売上", "利益", "構成比", "セグメント情報"))
                )
                numbered_segment = bool(re.match(r"^\d{1,2}\s+", label))
                if is_simple_segment_table and numbered_segment and is_segment:
                    for column, (fiscal_year, result_type) in columns.items():
                        if column >= len(row):
                            continue
                        value = _to_million(row[column], unit)
                        if value is None:
                            continue
                        key = (fiscal_year, result_type, clean_label)
                        segments.setdefault(key, {
                            "fiscal_year": fiscal_year,
                            "result_type": result_type,
                            "segment_name": clean_label,
                            "sales": value,
                            "operating_profit": None,
                            "source": "決算資料PDF（無料解析）",
                            "basis_date": "",
                            "note": f"{source_name} p.{page_number} {source_url}",
                            "retrieved_at": retrieved_at,
                        })
                    continue

                if _valid_kpi_label(clean_label) and (
                    is_simple_segment_table or kpi_label_count >= 2
                ):
                    unit_match = re.search(r"[（(]([^）)]+)[）)]", clean_label)
                    metric_unit = unit_match.group(1) if unit_match else (
                        "社" if "社数" in clean_label else "件" if "件数" in clean_label else ""
                    )
                    for column, (fiscal_year, result_type) in columns.items():
                        if column >= len(row):
                            continue
                        value = _number(row[column])
                        if value is None:
                            continue
                        key = (fiscal_year, result_type, clean_label)
                        metrics.setdefault(key, {
                            "fiscal_year": fiscal_year,
                            "result_type": result_type,
                            "row_label": clean_label,
                            "value": value,
                            "unit": metric_unit,
                            "display_order": order,
                            "source": f"決算資料PDF（無料解析） p.{page_number}",
                            "note": source_url,
                        })

    return {
        "pl_records": [pl_by_key[key] for key in sorted(pl_by_key)],
        "segment_records": [segments[key] for key in sorted(segments)],
        "segment_metrics": [metrics[key] for key in sorted(metrics)],
    }
