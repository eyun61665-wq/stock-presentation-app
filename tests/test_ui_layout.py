from ui_layout import (
    DISPLAY_DESKTOP,
    DISPLAY_MOBILE,
    is_mobile_user_agent,
    resolve_display_mode,
    user_agent_from_headers,
)


IPHONE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) "
    "AppleWebKit/605.1.15 Version/18.5 Mobile/15E148 Safari/604.1"
)
WINDOWS_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 Chrome/138.0.0.0 Safari/537.36"
)


def test_iphone_uses_mobile_layout() -> None:
    assert is_mobile_user_agent(IPHONE_UA)
    assert resolve_display_mode("自動", IPHONE_UA) == DISPLAY_MOBILE


def test_windows_uses_desktop_layout() -> None:
    assert not is_mobile_user_agent(WINDOWS_UA)
    assert resolve_display_mode("自動", WINDOWS_UA) == DISPLAY_DESKTOP


def test_manual_override_has_priority() -> None:
    assert resolve_display_mode("PC", IPHONE_UA) == DISPLAY_DESKTOP
    assert resolve_display_mode("スマホ", WINDOWS_UA) == DISPLAY_MOBILE


def test_user_agent_header_is_case_insensitive() -> None:
    assert user_agent_from_headers({"user-agent": IPHONE_UA}) == IPHONE_UA
