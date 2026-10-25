"""Streamlit terminal entry point: ``make terminal`` (streamlit run apps/terminal/app.py)."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

PAGES_DIR = Path(__file__).parent / "pages"

# (file, title, icon)
PAGES: list[tuple[str, str, str]] = [
    ("overview.py", "Overview", ":material/dashboard:"),
    ("portfolio.py", "Portfolio", ":material/account_balance_wallet:"),
    ("performance.py", "Performance", ":material/trending_up:"),
    ("risk.py", "Risk", ":material/shield:"),
    ("scenarios.py", "Scenarios", ":material/thunderstorm:"),
    ("optimization.py", "Optimization", ":material/tune:"),
    ("macro.py", "Macro", ":material/public:"),
    ("data.py", "Data", ":material/database:"),
    ("news.py", "News", ":material/newspaper:"),
    ("research.py", "Research", ":material/science:"),
    ("backtests.py", "Backtests", ":material/history:"),
    ("trading.py", "Trading", ":material/swap_horiz:"),
    ("journal.py", "Journal", ":material/edit_note:"),
    ("assistant.py", "Assistant", ":material/chat:"),
]

st.set_page_config(page_title="qp terminal", layout="wide")
navigation = st.navigation(
    [
        st.Page(PAGES_DIR / file, title=title, icon=icon, default=(i == 0))
        for i, (file, title, icon) in enumerate(PAGES)
    ]
)
navigation.run()
