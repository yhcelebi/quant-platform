# ADR 0016 — Optimization, stress scenarios and the read-only assistant

- Status: accepted (owner standing instruction, 2026-10-25). *Default* items can change.
- Date: 2026-10-25

## Context

The ROADMAP's phase 9 adds portfolio optimization (mean-variance, risk parity, HRP, CVaR),
stress scenarios (rate shocks, TRY depreciation, historical replay) and a read-only LLM
assistant (rule 12: LLM features never compute numbers). `cvxpy` and `riskfolio-lib` are in the
`research` group only; the library must work with the default dependencies.

## Decision

### Optimization (`qp.optimization`)

1. **Returns**: daily TRY returns of each asset (price × TCMB rate; foreign cash: the rate)
   from `qp.risk.var.asset_returns`, on valuation days over a lookback *(default 500)*. Only
   days on which every asset has a fresh return are used; an asset with fewer than
   `min_observations` fresh returns is left out and listed. TRY cash, if included, has zero
   return and zero variance.
2. **Covariance**: Ledoit-Wolf shrinkage *(default; `sample` available)*, annualized ×252.
3. **Methods** (long-only, fully invested, per-asset cap *(default 1.0)*):
   minimum variance and maximum Sharpe (SLSQP, scipy); risk parity (equal risk contribution,
   SLSQP on the log-barrier form); HRP (López de Prado 2016: single-linkage on
   √(½(1−ρ)), quasi-diagonal order, recursive bisection); minimum CVaR (Rockafellar–Uryasev
   linear program, scipy HiGHS) at 95% *(default)*.
4. **Expected returns** are needed only for maximum Sharpe: the historical mean, annualized,
   with a risk-free rate the owner states *(default 0)*. The result says that the mean is
   estimated with large error; the other four methods do not use it.
5. **Output**: weights, ex-ante annualized volatility, each asset's share of risk, the
   historical 95% CVaR of the weights over the same days, and the TRY trade per asset that
   would move today's holdings to the weights (Decimal, rounded to kuruş). It is a model
   output: nothing is sent to the trading gate or a broker.

### Scenarios (`qp.scenarios`)

6. **Shock scenarios** in `configs/scenarios.yaml`: `fx` (currency → % change of its TRY
   rate), `asset_class` and `instrument` (% price change in the instrument's currency; the
   instrument entry wins), `rate_bp` with `durations` (instrument → modified duration; the
   price change is −D × Δy, applied only to listed instruments, others listed as "no
   duration"). A holding's TRY change is (1 + price change)(1 + FX change) − 1. Default
   scenarios are definitions, not data; the owner edits them.
7. **Historical replay**: named windows (start, end). Each holding's change is its TRY level
   at the window's end over its level at the start, from data known now. Holdings without a
   level at both ends are left out and listed with their value share.
8. **Worst observed k-day loss** *(default k = 1, 5, 20)*: today's holdings over every k-day
   window in the available history.
9. Results are Decimal TRY per holding and in total, with the share of value covered.

### Assistant (`qp.assistant`)

10. **Claude API with tool use**, manual loop (no beta dependency for the loop); model
    `claude-opus-5-5` *(default, config `assistant.model`)*, effort `medium` *(default)*,
    at most 8 tool rounds *(default)*. Server-side refusal fallback on by default.
11. **Read-only tools only**: holdings, performance, risk, exposures, scenarios, optimization,
    macro indicators, news search, journal entries, backtest runs, paper status. There is no
    tool that writes, fetches data, trades, deploys or promotes.
12. **Rule 12 guard**: the system prompt forbids arithmetic; after the answer, every number in
    it is compared with the numbers in the tool results and the question (allowing display
    rounding and percent/fraction forms). Numbers not found are listed under the answer as
    "not from qp data". Every answer lists the tool calls it used and their `as_of`.
13. **Privacy**: questions and tool results (portfolio values) go to Anthropic's API. The
    assistant is off unless `ANTHROPIC_API_KEY` (or `QP_ASSISTANT__API_KEY`) is set; the
    key in env/.env is the opt-in. Transcripts are appended to
    `data/assistant/transcripts.jsonl` (local, not committed).

## Consequences

- No new heavy dependency: scipy, scikit-learn (Ledoit-Wolf) and the `anthropic` SDK only.
- Optimization on TCMB-only data covers FX and cash; equities join when a price source exists.
- The assistant can be wrong in words but cannot introduce a number silently.
