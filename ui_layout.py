"""端末に応じた表示モードを決めるための小さな共通処理。"""
from __future__ import annotations

from collections.abc import Mapping


DISPLAY_AUTO = "自動"
DISPLAY_MOBILE = "スマホ"
DISPLAY_DESKTOP = "PC"
DISPLAY_OPTIONS = [DISPLAY_AUTO, DISPLAY_MOBILE, DISPLAY_DESKTOP]


def user_agent_from_headers(headers: Mapping[str, object] | None) -> str:
    """大文字・小文字の違いを吸収してUser-Agentを取り出す。"""
    if not headers:
        return ""
    for key, value in headers.items():
        if str(key).lower() == "user-agent":
            return str(value or "")
    return ""


def is_mobile_user_agent(user_agent: str | None) -> bool:
    """iPhone・Androidなど、タッチ端末向けUAかを判定する。"""
    value = str(user_agent or "").lower()
    if not value:
        return False
    mobile_markers = (
        "iphone",
        "ipod",
        "ipad",
        "android",
        "mobile/",
        "windows phone",
        "opera mini",
        "opera mobi",
    )
    return any(marker in value for marker in mobile_markers)


def resolve_display_mode(preference: str | None, user_agent: str | None) -> str:
    """手動指定を優先し、自動時だけUser-Agentから表示モードを決める。"""
    if preference == DISPLAY_MOBILE:
        return DISPLAY_MOBILE
    if preference == DISPLAY_DESKTOP:
        return DISPLAY_DESKTOP
    return DISPLAY_MOBILE if is_mobile_user_agent(user_agent) else DISPLAY_DESKTOP
