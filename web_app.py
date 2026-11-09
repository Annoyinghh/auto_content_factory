"""Broadcast Web Studio entry point (supports both direct Uvicorn launch and Streamlit container)."""

from __future__ import annotations

import sys
from pathlib import Path

from studio_web import ensure_studio_server, main as studio_main


def _running_in_streamlit() -> bool:
    try:
        from streamlit.runtime import exists
        return bool(exists())
    except Exception:
        return False


def render_streamlit_host() -> None:
    import streamlit as st
    import streamlit.components.v1 as components

    st.set_page_config(
        page_title="Auto Content Factory // Studio Workbench",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    st.markdown(
        """
        <style>
        #MainMenu, header, footer { visibility: hidden; }
        .block-container { padding: 0 !important; max-width: 100vw !important; }
        iframe { border: none !important; width: 100vw !important; height: 100vh !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )
    url = ensure_studio_server()
    components.iframe(url, height=920, scrolling=False)


if __name__ == "__main__":
    if _running_in_streamlit():
        render_streamlit_host()
    else:
        raise SystemExit(studio_main())
elif _running_in_streamlit():
    render_streamlit_host()
