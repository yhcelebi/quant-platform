"""Plotly figures for the terminal. Display only: values arrive as floats converted from exact
Decimals at the page boundary.

Palette: the validated categorical order (blue, orange, aqua, yellow, magenta, green,
violet), assigned to entities in a fixed order and never cycled; light and dark steps chosen
from the Streamlit theme. Diverging blue <-> red around a gray midpoint for signed values.
One y-axis per chart; thin 2px lines; legends for two or more series; hover on every mark.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

import plotly.graph_objects as go
import streamlit as st

LIGHT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6"]
DARK = ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9"]
INK = {
    "light": ("#52514e", "#898781", "#e1e0d9", "#c3c2b7"),
    "dark": ("#c3c2b7", "#898781", "#2c2c2a", "#383835"),
}
MID = {"light": "#f0efec", "dark": "#383835"}
BLUE, RED = "#2a78d6", "#e34948"
CRITICAL = "#d03b3b"


def mode() -> str:
    theme = getattr(st.context, "theme", None)
    return "dark" if theme is not None and theme.type == "dark" else "light"


def series_colors() -> list[str]:
    return DARK if mode() == "dark" else LIGHT


def _layout(fig: go.Figure, *, title: str, y_format: str = "", height: int = 340) -> go.Figure:
    secondary, muted, grid, axis = INK[mode()]
    fig.update_layout(
        title={"text": title, "font": {"size": 14}},
        height=height,
        margin={"l": 8, "r": 8, "t": 40, "b": 8},
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.15, "font": {"color": secondary}},
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
    )
    fig.update_xaxes(showgrid=False, linecolor=axis, tickfont={"color": muted})
    fig.update_yaxes(gridcolor=grid, zeroline=False, tickformat=y_format, tickfont={"color": muted})
    return fig


def show(fig: go.Figure) -> None:
    st.plotly_chart(fig, theme=None, config={"displayModeBar": False})


def lines(
    title: str,
    series: Sequence[tuple[str, Sequence[date], Sequence[float]]],
    *,
    y_format: str = "",
) -> go.Figure:
    """One line per entity (colors follow the entity's position in ``series``)."""
    colors = series_colors()
    fig = go.Figure()
    for i, (name, xs, ys) in enumerate(series[: len(colors)]):
        fig.add_trace(
            go.Scatter(
                x=list(xs),
                y=list(ys),
                name=name,
                mode="lines",
                line={"width": 2, "color": colors[i]},
                hovertemplate=f"{name}: %{{y:{y_format or ',.2f'}}}<extra></extra>",
            )
        )
    fig.update_layout(showlegend=len(series) > 1)
    return _layout(fig, title=title, y_format=y_format)


def drawdown(title: str, xs: Sequence[date], ys: Sequence[float]) -> go.Figure:
    fig = go.Figure(
        go.Scatter(
            x=list(xs),
            y=list(ys),
            mode="lines",
            fill="tozeroy",
            line={"width": 2, "color": RED},
            fillcolor="rgba(227,73,72,0.18)",
            name="drawdown",
            hovertemplate="%{y:.2%}<extra></extra>",
        )
    )
    fig.update_layout(showlegend=False)
    return _layout(fig, title=title, y_format=".0%", height=260)


def diverging_heatmap(
    title: str,
    rows: Sequence[str],
    cols: Sequence[str],
    values: Sequence[Sequence[float | None]],
    *,
    limit: float,
    fmt: str,
) -> go.Figure:
    """Signed values: red below zero, blue above, gray midpoint; cells labeled."""
    text = [["" if v is None else format(v, fmt) for v in row] for row in values]
    fig = go.Figure(
        go.Heatmap(
            z=[list(r) for r in values],
            x=list(cols),
            y=list(rows),
            zmin=-limit,
            zmax=limit,
            colorscale=[[0, RED], [0.5, MID[mode()]], [1, BLUE]],
            text=text,
            texttemplate="%{text}",
            xgap=2,
            ygap=2,
            hovertemplate="%{y} %{x}: %{text}<extra></extra>",
            showscale=False,
        )
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(hovermode="closest")
    return _layout(fig, title=title, height=max(160, 44 * len(rows) + 60))


def hbars(title: str, names: Sequence[str], weights: Sequence[float]) -> go.Figure:
    """Shares of a whole, largest first, one hue (magnitude, not identity)."""
    fig = go.Figure(
        go.Bar(
            x=list(weights),
            y=list(names),
            orientation="h",
            marker={"color": series_colors()[0]},
            text=[f"{w:.1%}" for w in weights],
            textposition="outside",
            hovertemplate="%{y}: %{x:.2%}<extra></extra>",
        )
    )
    fig.update_yaxes(autorange="reversed")
    fig.update_layout(showlegend=False, bargap=0.35, hovermode="closest")
    return _layout(fig, title=title, y_format="", height=max(160, 34 * len(names) + 70))


def histogram(title: str, values: Sequence[float], marker: float | None, label: str) -> go.Figure:
    """Distribution of scenario P&L with the VaR threshold marked."""
    fig = go.Figure(
        go.Histogram(
            x=list(values),
            marker={"color": series_colors()[0], "line": {"width": 2, "color": "rgba(0,0,0,0)"}},
            hovertemplate="P&L %{x:,.0f}: %{y} days<extra></extra>",
            name="days",
        )
    )
    if marker is not None:
        fig.add_vline(x=-marker, line={"color": CRITICAL, "width": 2, "dash": "dash"})
        fig.add_annotation(
            x=-marker, y=1, yref="paper", text=label, showarrow=False, xanchor="right"
        )
    fig.update_layout(showlegend=False, hovermode="closest", bargap=0.08)
    return _layout(fig, title=title, height=280)
