"""Journal page: theses with invalidation criteria and review dates, reviews, notes and
watchlists (ADR 0013, 13). One of the terminal's two allowed writes: everything appended,
nothing edited or deleted."""

from __future__ import annotations

from datetime import timedelta

import streamlit as st

from apps.terminal.state import settings_or_stop
from qp.core import SystemClock, istanbul_date, to_istanbul
from qp.core.errors import QPError
from qp.decisions import journal
from qp.platform import Database

st.title("Journal")
settings = settings_or_stop()
today = istanbul_date(SystemClock().now())


def render(db: Database) -> None:
    theses = journal.theses(db)

    due = [t for t in theses if t.status == "open" and t.next_review and t.next_review <= today]
    if due:
        st.warning(f"{len(due)} review(s) due: " + ", ".join(f"#{t.id} {t.title}" for t in due))

    show_closed = st.toggle("Show closed and invalidated", value=False)
    st.dataframe(
        [
            {
                "id": t.id,
                "title": t.title,
                "instrument": t.instrument or "",
                "direction": t.direction,
                "status": t.status,
                "next review": str(t.next_review or ""),
                "invalidation": t.invalidation,
            }
            for t in theses
            if show_closed or t.status == "open"
        ],
        hide_index=True,
        width="stretch",
    )

    new, rev, note = st.tabs(["New thesis", "Review", "Note"])
    with new, st.form("thesis", clear_on_submit=True):
        title = st.text_input("Title")
        thesis = st.text_area("Thesis: what you believe and why")
        invalidation = st.text_area("Invalidation: what would prove it wrong")
        c1, c2, c3 = st.columns(3)
        direction = c1.selectbox("Direction", ["long", "short", "neutral", "macro"])
        instrument = c2.text_input("Instrument (SYMBOL.VENUE, optional)")
        first = c3.date_input("First review", value=today + timedelta(days=30))
        if st.form_submit_button("Record thesis"):
            try:
                tid = journal.add_thesis(
                    db,
                    title=title,
                    thesis=thesis,
                    invalidation=invalidation,
                    first_review=first,
                    direction=direction,
                    instrument=instrument.strip() or None,
                )
                st.success(f"Thesis #{tid} recorded.")
            except Exception as exc:  # validation, or an unknown instrument id (foreign key)
                st.error(f"Not recorded: {exc}")

    open_ids = [t.id for t in theses if t.status == "open"]
    with rev, st.form("review", clear_on_submit=True):
        if not open_ids:
            st.caption("No open theses.")
        tid = st.selectbox(
            "Thesis",
            open_ids,
            format_func=lambda i: next(f"#{t.id} {t.title}" for t in theses if t.id == i),
        )
        verdict = st.radio("Verdict", ["keep", "close", "invalidated"], horizontal=True)
        nxt = st.date_input("Next review (keep only)", value=today + timedelta(days=30))
        text = st.text_area("What you checked against the invalidation criteria")
        if st.form_submit_button("Record review") and tid is not None:
            try:
                journal.review(
                    db,
                    tid,
                    verdict=verdict,
                    note=text,
                    next_review=nxt if verdict == "keep" else None,
                )
                st.success("Review recorded.")
            except QPError as exc:
                st.error(f"Not recorded: {exc}")

    with note, st.form("note", clear_on_submit=True):
        body = st.text_area("Note or decision")
        decision = st.checkbox("This is a decision")
        attach = st.selectbox("Attach to thesis (optional)", [None, *open_ids])
        if st.form_submit_button("Record"):
            try:
                journal.add_entry(
                    db, body, kind="decision" if decision else "note", thesis_id=attach
                )
                st.success("Recorded.")
            except QPError as exc:
                st.error(f"Not recorded: {exc}")

    st.subheader("Recent notes")
    for e in journal.entries(db, limit=20):
        st.markdown(f"- {to_istanbul(e.created_at):%Y-%m-%d %H:%M} · {e.kind} · {e.text}")
    lists = journal.watchlists(db)
    if lists:
        st.subheader("Watchlists")
        for name, members in lists.items():
            st.markdown(f"**{name}**: {', '.join(members)}")


with Database.from_settings(settings.db) as database:
    try:
        journal.theses(database)
    except Exception as exc:  # show the reason instead of a stack trace
        st.error(f"The journal needs PostgreSQL: {type(exc).__name__}")
        st.caption("Run `make db-up && make migrate`.")
        st.stop()
    render(database)
