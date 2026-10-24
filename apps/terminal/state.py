"""Shared loading for terminal pages: settings and the ledger/market context, cached briefly
so switching pages does not reload the ledger. Read-only."""

from __future__ import annotations

import streamlit as st

from qp.core.errors import QPError
from qp.performance.service import Context, load_context
from qp.platform import Settings, init_platform


def settings_or_stop() -> Settings:
    try:
        return init_platform()
    except QPError as exc:
        st.error(f"Configuration error: {exc}")
        st.stop()


@st.cache_resource(ttl=300, show_spinner="Loading ledger and market data…")
def _context() -> Context:
    return load_context(init_platform())


def context_or_stop() -> Context:
    settings_or_stop()
    try:
        return _context()
    except Exception as exc:  # show the reason instead of a stack trace
        st.error(f"Could not load the ledger: {type(exc).__name__}: {str(exc).splitlines()[0]}")
        st.caption("Is PostgreSQL running (`make db-up`) and migrated (`make migrate`)?")
        st.stop()


def reload_button() -> None:
    if st.sidebar.button("Reload data", help="Re-read the ledger and market data"):
        _context.clear()
        st.rerun()
