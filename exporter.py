"""PLのCSV/Excel出力。"""
from __future__ import annotations

from io import BytesIO
import pandas as pd


def pl_to_csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8-sig")


def pl_to_excel(frame: pd.DataFrame) -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name="PL")
    return output.getvalue()


def frame_to_csv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False).encode("utf-8-sig")


def frame_to_tsv(frame: pd.DataFrame) -> bytes:
    return frame.to_csv(index=False, sep="\t").encode("utf-8-sig")


def frame_to_excel(frame: pd.DataFrame, sheet_name: str = "データ") -> bytes:
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        frame.to_excel(writer, index=False, sheet_name=sheet_name[:31])
    return output.getvalue()
