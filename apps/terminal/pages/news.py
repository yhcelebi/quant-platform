"""News page: official feeds with entity links and tags, filterable; holdings first.
Read-only (`uv run qp news fetch` or the workers job fills it)."""

from __future__ import annotations

from datetime import datetime, time, timedelta

import streamlit as st

from apps.terminal.state import settings_or_stop
from qp.core import SystemClock, assume_istanbul, istanbul_date, to_istanbul
from qp.news.store import query
from qp.platform import Database

st.title("News")
settings = settings_or_stop()
now = SystemClock().now()
feeds = {f.code: f.name for f in settings.sources.news.feeds}

c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
days = c1.slider("Days", 1, 90, 14)
sources = c2.multiselect(
    "Sources", list(feeds), default=list(feeds), format_func=lambda c: feeds[c]
)
tag = c3.text_input("Tag", placeholder="e.g. interest_rates")
entity = c4.text_input("Entity", placeholder="e.g. THYAO.BIST, USD, TCMB")
since = assume_istanbul(datetime.combine(istanbul_date(now) - timedelta(days=days), time()))
try:
    with Database.from_settings(settings.db) as db:
        items = query(
            db,
            as_of=now,
            since=since,
            sources=sources or None,
            tag_filter=tag.strip() or None,
            entity_keys=[e.strip() for e in entity.split(",") if e.strip()] or None,
        )
except Exception as exc:  # show the reason instead of a stack trace
    st.error(f"News needs PostgreSQL: {type(exc).__name__}")
    st.caption("Run `make db-up && make migrate`, then `uv run qp news fetch`.")
    st.stop()

if not items:
    st.info("No items for these filters. Fetch with `uv run qp news fetch`.")
    st.stop()
st.caption(f"{len(items)} items. Titles and links only; read the full text at the source.")
st.dataframe(
    [
        {
            "published (Istanbul)": f"{to_istanbul(i.published_at):%Y-%m-%d %H:%M}",
            "source": feeds.get(i.source, i.source),
            "title": i.title,
            "entities": ", ".join(sorted({e.key for e in i.entities})),
            "tags": ", ".join(i.tags),
            "link": i.url,
        }
        for i in items
    ],
    hide_index=True,
    width="stretch",
    column_config={"link": st.column_config.LinkColumn("link", display_text="open")},
)
