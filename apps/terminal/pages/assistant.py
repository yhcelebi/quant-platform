"""Assistant page: questions answered with qp's read-only tools (ADR 0016, 10-13). Numbers
that do not come from qp's results are flagged under each answer."""

from __future__ import annotations

import streamlit as st

from apps.terminal.state import settings_or_stop
from qp.assistant.agent import Answer, Assistant, anthropic_client, log_transcript
from qp.assistant.tools import Toolbox
from qp.core.errors import QPError

st.title("Assistant")
settings = settings_or_stop()
try:
    client = anthropic_client(settings.assistant)
except QPError as exc:
    st.info(str(exc))
    st.caption(
        "The assistant reads your holdings, performance, risk, scenarios, macro data and news "
        "through qp's read-only functions. It cannot trade, write or fetch data."
    )
    st.stop()

st.caption(
    f"Model {settings.assistant.model}. Questions and the figures the tools return are sent to "
    "Anthropic's API. Every number in an answer is checked against qp's results."
)
if "assistant_turns" not in st.session_state:
    st.session_state.assistant_turns = []
    st.session_state.assistant_history = []
if st.sidebar.button("New conversation"):
    st.session_state.assistant_turns = []
    st.session_state.assistant_history = []


def show(answer: Answer) -> None:
    st.markdown(answer.text or "_(no answer)_")
    if answer.unverified:
        st.error(
            "Not from qp data: " + ", ".join(answer.unverified) + ". Treat these as unreliable."
        )
    calls = ", ".join(f"{c.name}{'' if c.ok else ' (failed)'}" for c in answer.tool_calls)
    st.caption(f"Tools: {calls or 'none'}")
    for n in answer.notes:
        st.caption(f"Note: {n}")


for question, answer in st.session_state.assistant_turns:
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"):
        show(answer)

if question := st.chat_input("Ask about your portfolio, risk, scenarios, macro or news"):
    with st.chat_message("user"):
        st.markdown(question)
    with st.chat_message("assistant"), st.spinner("Reading qp data…"):
        toolbox = Toolbox(settings)
        try:
            bot = Assistant(client, toolbox.tools(), settings.assistant)
            answer = bot.ask(question, st.session_state.assistant_history)
        finally:
            toolbox.close()
        show(answer)
    st.session_state.assistant_turns.append((question, answer))
    st.session_state.assistant_history = bot.history
    log_transcript(settings.assistant.transcripts, answer)
