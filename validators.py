"""入力のエラーと、保存を妨げない注意喚起。"""
from __future__ import annotations

import pandas as pd

EVIDENCE_CATEGORIES = ["会社開示", "官公庁・業界統計", "外部レポート", "競合企業", "逆算値", "自分の仮定"]


def validate_project(data: dict) -> list[str]:
    errors = []
    if not str(data.get("project_name", "")).strip():
        errors.append("プロジェクト名を入力してください。")
    if float(data.get("current_price", 0) or 0) < 0:
        errors.append("現在株価は0以上で入力してください。")
    if float(data.get("shares_outstanding", 0) or 0) < 0:
        errors.append("発行済株式数は0以上で入力してください。")
    return errors


def complete_pl_records(records: list[dict]) -> tuple[list[dict], int]:
    required = ["年度", "実績／会社予想／自分予想", "売上高（百万円）", "営業利益（百万円）", "純利益（百万円）", "平均株式数（百万株）"]
    complete, skipped = [], 0
    for row in records:
        if not any(value not in (None, "") for value in row.values()):
            continue
        if any(row.get(key) in (None, "") for key in required):
            skipped += 1
        else:
            complete.append(row)
    return complete, skipped


def validate_pl_entries(entries: list[dict]) -> list[str]:
    errors = []
    keys = [
        (str(row["年度"]).strip(), str(row["実績／会社予想／自分予想"]).strip())
        for row in entries
    ]
    if len(keys) != len(set(keys)):
        errors.append("PLの年度と区分の組み合わせは重複しないようにしてください。")
    if any(row["実績／会社予想／自分予想"] not in ("実績", "会社予想", "自分予想") for row in entries):
        errors.append("区分は実績・会社予想・自分予想から選択してください。")
    return errors


def pl_warnings(entries: list[dict], eps_threshold: float = 10_000) -> list[str]:
    warnings = []
    for row in entries:
        sales = float(row["売上高（百万円）"])
        operating = float(row["営業利益（百万円）"])
        net = float(row["純利益（百万円）"])
        shares = float(row["平均株式数（百万株）"])
        year = row["年度"]
        if operating > sales:
            warnings.append(f"{year}: 営業利益が売上高を超えています。")
        if net > sales:
            warnings.append(f"{year}: 純利益が売上高を超えています。")
        if shares <= 0:
            warnings.append(f"{year}: 平均株式数が0以下です。EPSは計算できません。")
        elif abs(net / shares) > eps_threshold:
            warnings.append(f"{year}: EPSが異常に大きい値です。単位を確認してください。")
    return warnings


def catalyst_warnings(catalyst: dict, existing_sales: float | None = None) -> list[str]:
    warnings = []
    if catalyst.get("calculation_method", "数量モデル") == "売上比率モデル":
        impact_rate = float(catalyst.get("impact_rate", 0) or 0)
        if not 0 <= impact_rate <= 100:
            warnings.append("売上影響率は0％から100％の範囲で入力してください。")
    else:
        for label, key in [("対象率", "target_rate"), ("自社獲得率", "capture_rate")]:
            value = float(catalyst.get(key, 0) or 0)
            if not 0 <= value <= 100:
                warnings.append(f"{label}は0％から100％の範囲で入力してください。")
    if catalyst.get("evidence_category") == "自分の仮定" and not (str(catalyst.get("source", "")).strip() or str(catalyst.get("note", "")).strip()):
        warnings.append("根拠区分が「自分の仮定」ですが、出典または補足メモがありません。")
    if existing_sales and float(catalyst.get("additional_sales", 0) or 0) > existing_sales * 3:
        warnings.append("カタリスト追加売上が既存売上の3倍を超えています。")
    return warnings


def frame_to_records(frame: pd.DataFrame) -> list[dict]:
    return frame.where(pd.notnull(frame), None).to_dict("records")
