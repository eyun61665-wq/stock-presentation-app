"""決算資料のPL項目を安全に正規化する。推測値は作らない。"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, asdict
from typing import Iterable, Any


PL_ITEMS = ("sales", "gross_profit", "operating_profit", "ordinary_profit", "pretax_profit", "net_income", "eps")
LABELS = {
    "sales": ("売上高", "売上収益", "営業収益", "Revenue"),
    "gross_profit": ("売上総利益", "Gross profit"),
    "operating_profit": ("営業利益", "事業利益", "Operating profit"),
    "ordinary_profit": ("経常利益", "Ordinary profit"),
    "pretax_profit": ("税引前利益", "税引前当期利益", "Profit before tax"),
    "net_income": ("親会社株主に帰属する当期純利益", "親会社の所有者に帰属する当期利益", "当期利益", "Profit attributable"),
    "eps": ("1株当たり当期純利益", "1株当たり利益", "Earnings per share"),
}
UNIT_MULTIPLIERS = {"円": 1 / 1_000_000, "千円": 1 / 1_000, "百万円": 1, "億円": 100}


def normalize_japanese_number(value: str | int | float | None) -> float | None:
    if value is None:
        return None
    text = unicodedata.normalize("NFKC", str(value)).strip()
    if not text or text in {"-", "―", "－", "N/A"}:
        return None
    negative = text.startswith("(") and text.endswith(")")
    text = text.strip("()").replace(",", "").replace("△", "-")
    try:
        number = float(text)
    except ValueError:
        return None
    return -abs(number) if negative else number


def convert_to_million(value: str | int | float | None, unit: str) -> float | None:
    number = normalize_japanese_number(value)
    return None if number is None else number * UNIT_MULTIPLIERS.get(unit, 1)


def detect_unit(text: str) -> str:
    for unit in ("百万円", "億円", "千円", "円"):
        if unit in text:
            return unit
    return "百万円"


def normalize_label(label: str) -> str | None:
    normalized = unicodedata.normalize("NFKC", label).lower().replace(" ", "")
    for key, alternatives in LABELS.items():
        if any(unicodedata.normalize("NFKC", item).lower().replace(" ", "") in normalized for item in alternatives):
            return key
    return None


def infer_year(text: str) -> str | None:
    match = re.search(r"(20\d{2})[年/.-]?(?:\s*3月期|\s*12月期|\s*\d{1,2}月期)?", text)
    return match.group(1) if match else None


def statement_scope(text: str) -> str:
    compact = unicodedata.normalize("NFKC", text).replace(" ", "")
    if "連結" in compact or "Consolidated" in compact:
        return "連結"
    if "個別" in compact or "Non-consolidated" in compact or "単体" in compact:
        return "単体"
    return "要確認"


@dataclass
class PLRecord:
    fiscal_year: str
    period: str
    scope: str
    unit: str
    source_name: str
    source_url: str
    page: int
    retrieved_at: str
    values: dict[str, float | None]

    def as_dict(self) -> dict:
        return {**asdict(self), **self.values}


def parse_pl_text(pages: Iterable[str], source_name: str, source_url: str, retrieved_at: str) -> list[PLRecord]:
    """各ページからラベル直後の数値だけを候補化。連結優先、四半期単独は除外する。"""
    records: list[PLRecord] = []
    for page_number, text in enumerate(pages, start=1):
        if not text or ("四半期" in text and "累計" not in text):
            continue
        scope = statement_scope(text)
        year = infer_year(text)
        if not year:
            continue
        unit = detect_unit(text)
        values = {item: None for item in PL_ITEMS}
        for line in text.splitlines():
            item = normalize_label(line)
            if not item:
                continue
            numbers = re.findall(r"[△(]?[\d,]+(?:\.\d+)?\)?", line)
            if numbers:
                values[item] = normalize_japanese_number(numbers[0]) if item == "eps" else convert_to_million(numbers[0], unit)
        if any(value is not None for value in values.values()):
            records.append(PLRecord(year, year, scope, unit, source_name, source_url, page_number, retrieved_at, values))
    consolidated = [record for record in records if record.scope == "連結"]
    return consolidated or records


# 決算短信の連結損益計算書で使用される主要行。順序は画面のPL表と合わせる。
DETAILED_PL_LABELS = (
    ("sales", ("売上高",)),
    ("cost_of_sales", ("売上原価",)),
    ("gross_profit", ("売上総利益",)),
    ("sga_expenses", ("販売費及び一般管理費合計", "販売費及び一般管理費")),
    ("operating_profit", ("営業利益",)),
    ("non_operating_income", ("営業外収益合計",)),
    ("non_operating_expenses", ("営業外費用合計",)),
    ("ordinary_profit", ("経常利益",)),
    ("extraordinary_income", ("特別利益合計",)),
    ("extraordinary_loss", ("特別損失合計",)),
    ("pretax_profit", ("税金等調整前当期純利益", "税引前利益")),
    ("income_taxes", ("法人税等合計", "法人所得税費用")),
    ("net_income", ("親会社株主に帰属する当期純利益", "親会社の所有者に帰属する当期利益")),
)

_VALUE_TOKEN = re.compile(r"△?\s*[\d,]+(?:\.\d+)?|[－―—-]")
_PERIOD_END = re.compile(r"至\s*(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日")


def _iso_period_ends(text: str) -> list[str]:
    periods: list[str] = []
    for year, month, day in _PERIOD_END.findall(text):
        value = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        if value not in periods:
            periods.append(value)
    return periods


def _period_end_occurrences(text: str) -> list[str]:
    return [
        f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
        for year, month, day in _PERIOD_END.findall(text)
    ]


def _statement_value(token: str, unit: str, dash_as_zero: bool = True) -> float | None:
    compact = token.replace(" ", "")
    if compact in {"－", "―", "—", "-"}:
        return 0.0 if dash_as_zero else None
    return convert_to_million(compact, unit)


def _row_values(text: str, label: str, unit: str, expected: int) -> list[float | None] | None:
    """行頭が一致する合計行だけを読み、比較年度の数値を返す。"""
    for raw_line in text.splitlines():
        line = " ".join(raw_line.replace("　", " ").split())
        if not line.startswith(label):
            continue
        remainder = line[len(label):].strip()
        tokens = _VALUE_TOKEN.findall(remainder)
        if len(tokens) < expected:
            continue
        return [_statement_value(token, unit) for token in tokens[:expected]]
    return None


def _row_values_any(text: str, labels: tuple[str, ...], unit: str, expected: int) -> list[float | None] | None:
    for label in labels:
        values = _row_values(text, label, unit, expected)
        if values is not None:
            return values
    return None


def _plain_row_numbers(text: str, label: str, expected: int) -> list[float | None] | None:
    for raw_line in text.splitlines():
        line = " ".join(raw_line.replace("　", " ").split())
        if not line.startswith(label):
            continue
        tokens = _VALUE_TOKEN.findall(line[len(label):])
        if len(tokens) < expected:
            continue
        values: list[float | None] = []
        for token in tokens[:expected]:
            compact = token.replace(" ", "")
            values.append(None if compact in {"－", "―", "—", "-"} else normalize_japanese_number(compact))
        return values
    return None


def _yen_sen_row_values(text: str, label: str, expected: int) -> list[float] | None:
    """「318円91銭」を318.91円として読む。"""
    for raw_line in text.splitlines():
        line = " ".join(raw_line.replace("　", " ").split())
        if not line.startswith(label):
            continue
        pairs = re.findall(r"([\d,]+)円\s*(\d{1,2})銭", line)
        if len(pairs) < expected:
            continue
        return [float(yen.replace(",", "")) + int(sen) / 100 for yen, sen in pairs[:expected]]
    return None


def _disclosed_date(pages: list[str]) -> str:
    if not pages:
        return ""
    match = re.search(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日", pages[0])
    return "" if not match else f"{int(match.group(1)):04d}-{int(match.group(2)):02d}-{int(match.group(3)):02d}"


def parse_detailed_pl(
    pages: Iterable[str], source_name: str, source_url: str, retrieved_at: str,
) -> list[dict[str, Any]]:
    """本決算短信の連結PL比較表を年度別レコードへ変換する。"""
    page_list = list(pages)
    records: dict[str, dict[str, Any]] = {}
    disclosed_date = _disclosed_date(page_list)
    for page_number, text in enumerate(page_list, start=1):
        if "連結損益計算書" not in text or "売上原価" not in text or "販売費及び一般管理費" not in text:
            continue
        periods = _iso_period_ends(text)
        if not periods:
            continue
        unit = detect_unit(text)
        page_rows = {key: _row_values_any(text, labels, unit, len(periods)) for key, labels in DETAILED_PL_LABELS}
        for index, period in enumerate(periods):
            row = records.setdefault(period, {
                "fiscal_year": period,
                "result_type": "実績",
                "source": "決算短信PDF",
                "basis_date": disclosed_date,
                "source_name": source_name,
                "source_url": source_url,
                "page": page_number,
            })
            for key, values in page_rows.items():
                if values is not None and index < len(values):
                    row[key] = values[index]

    # EPS注記から平均株式数を取得。金額は千円、株式数は株なので百万単位への換算だけ行う。
    for page_number, text in enumerate(page_list, start=1):
        if "期中平均株式数" not in text:
            continue
        # セグメント注記の年度がページ上部に残ることがあるため、EPS算定表直前の最後2期間を使う。
        occurrences = _period_end_occurrences(text)
        periods = occurrences[-2:] if len(occurrences) >= 2 else occurrences
        if not periods:
            continue
        average_shares = _plain_row_numbers(text, "期中平均株式数（株）", len(periods))
        shares_divisor = 1_000_000
        if average_shares is None:
            average_shares = _plain_row_numbers(text, "普通株式の期中平均株式数", len(periods))
            shares_divisor = 1_000 if average_shares is not None and "千株" in text else 1_000_000
        reported_eps = _yen_sen_row_values(text, "１株当たり当期純利益", len(periods))
        if reported_eps is None:
            reported_eps = _plain_row_numbers(text, "１株当たり当期純利益", len(periods))
        for index, period in enumerate(periods):
            if period not in records:
                continue
            if average_shares and average_shares[index] is not None:
                records[period]["average_shares"] = average_shares[index] / shares_divisor
                records[period]["shares_outstanding"] = average_shares[index] / shares_divisor
            if reported_eps and reported_eps[index] is not None:
                records[period]["reported_eps"] = reported_eps[index]
            records[period]["eps_page"] = page_number

    # EPS注記がない短信では、開示EPSが確認できた場合だけ平均株式数を逆算する。
    for row in records.values():
        if row.get("average_shares") is None and row.get("net_income") is not None and row.get("reported_eps") not in (None, 0):
            row["average_shares"] = row["net_income"] / row["reported_eps"]
            row["shares_outstanding"] = row["average_shares"]
            row["shares_note"] = "開示純利益と開示EPSから逆算"
    return [records[period] for period in sorted(records)]


def _segment_names(page_list: list[str]) -> list[str]:
    names: list[str] = []
    for text in page_list:
        for name in re.findall(r"「([^」]{1,40}(?:事業|セグメント))」", text):
            compact = " ".join(name.split())
            if compact not in names:
                names.append(compact)
    return names


def _segment_header_names(text: str) -> list[str]:
    """引用符のない決算短信の表頭から報告セグメント名を復元する。"""
    if "報告セグメント" not in text or "外部顧客への売上高" not in text:
        return []
    header = text.rsplit("報告セグメント", 1)[-1].split("外部顧客への売上高", 1)[0]
    lines = [" ".join(line.replace("　", " ").split()) for line in header.splitlines()]
    names: list[str] = []
    prefix = ""
    for line in lines:
        line = re.sub(r"^[（(]注[)）]?\s*\d*", "", line).strip()
        if not line or any(word in line for word in ("調整額", "連結財務諸", "表計上額", "売上高", "単位")):
            continue
        line = re.sub(r"\s*計\s*$", "", line).strip()
        if "事業" in line and len(line) <= 50:
            name = f"{prefix}{line}" if prefix and not line.startswith(prefix) else line
            if name and name not in names:
                names.append(name)
            prefix = ""
        elif len(line) <= 30 and not re.search(r"\d", line):
            prefix = line
    return names


def parse_segment_statements(
    pages: Iterable[str], source_name: str, source_url: str, retrieved_at: str,
) -> list[dict[str, Any]]:
    """セグメント注記から外部顧客売上高とセグメント利益を抽出する。"""
    page_list = list(pages)
    global_names = _segment_names(page_list)
    disclosed_date = _disclosed_date(page_list)
    records: list[dict[str, Any]] = []
    for page_number, text in enumerate(page_list, start=1):
        if "報告セグメント" not in text or "外部顧客への売上高" not in text or "セグメント利益" not in text:
            continue
        blocks = re.split(r"(?=(?:前|当)連結会計年度\s*[（(]自)", text)
        for block in blocks:
            if "外部顧客への売上高" not in block or "セグメント利益" not in block:
                continue
            periods = _iso_period_ends(block)
            if not periods:
                continue
            period = periods[0]
            names = _segment_names([block]) or _segment_header_names(block) or global_names
            if not names:
                continue
            unit = detect_unit(block)
            sales = _row_values(block, "外部顧客への売上高", unit, len(names))
            profits = _row_values(block, "セグメント利益", unit, len(names))
            if sales is None and profits is None:
                continue
            for index, name in enumerate(names):
                records.append({
                    "fiscal_year": period,
                    "result_type": "実績",
                    "segment_name": name,
                    "sales": sales[index] if sales and index < len(sales) else None,
                    "operating_profit": profits[index] if profits and index < len(profits) else None,
                    "source": "決算短信PDF",
                    "basis_date": disclosed_date,
                    "note": f"{source_name} p.{page_number} {source_url}",
                    "retrieved_at": retrieved_at,
                })
    return records
