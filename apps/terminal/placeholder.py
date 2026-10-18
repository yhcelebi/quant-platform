"""Shared rendering for pages that are not built yet."""

from __future__ import annotations

import streamlit as st


def placeholder(title: str, phase: int, summary: str) -> None:
    st.title(title)
    st.info(f"Placeholder. Built in phase {phase}.")
    st.markdown(summary)
