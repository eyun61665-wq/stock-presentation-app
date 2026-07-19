"""銘柄発表MVPのStreamlit起動入口。"""
from __future__ import annotations

import streamlit as st


def main() -> None:
    """ページ設定後にMVP画面を描画する。"""
    st.set_page_config(
        page_title="銘柄発表",
        page_icon=":material/analytics:",
        layout="wide",
        initial_sidebar_state="auto",
    )
    from mvp_ui import render_app

    render_app()


if __name__ == "__main__":
    main()
