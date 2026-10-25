# Runbook: optimization, scenarios, assistant (phase 9)

Conventions: ADR 0016. All three only read: no command here places an order, writes to
the ledger or fetches data.

## Optimization

```bash
uv run qp optimize run --method hrp                  # today's holdings, TRY cash excluded
uv run qp optimize run --method min_cvar --cap 0.4   # at most 40% per asset
uv run qp optimize run --method risk_parity --add USD --add EUR --add XAUUSD.SPOT
```

Methods: `min_variance`, `max_sharpe` (needs expected returns: historical means, noisy;
`optimization.risk_free` sets the annual TRY rate), `risk_parity`, `hrp`, `min_cvar`.
Defaults in `configs/base.yaml` → `optimization` (500 days lookback, Ledoit-Wolf
covariance, 95% CVaR, cap 1.0).

Reading the output: *current* and *model* weights cover the optimized assets only;
*risk share* is each asset's share of the model portfolio's variance; *trade* is the TRY
amount that would move today's value to the model weights (shown only for held assets).
Assets with fewer than 60 fresh daily returns are left out and listed.

## Scenarios

```bash
uv run qp scenario list
uv run qp scenario run                       # all shocks, replays and worst windows
uv run qp scenario run --name "TRY -20%" --detail
```

Edit `configs/scenarios.yaml` to change shock sizes or add windows. Shocks apply
`(1 + price change)(1 + FX change) - 1` per holding (price change: instrument, then venue,
then asset class); rate shocks need a modified duration per instrument. Replays use each
holding's TRY level at both ends of the window from data known now; a holding without a
level within 7 days of either end is left out and listed. The lake's TCMB history starts
2025-01-02, so older windows show as unavailable until earlier data is backfilled
(`uv run qp data fetch tcmb --since 2018-01-01`).

## Assistant

1. Put a key in `.env`: `QP_ASSISTANT__API_KEY=...` (or `ANTHROPIC_API_KEY`). Without one
   the assistant is off. With one, questions and the figures the tools return (holdings,
   values, P&L) are sent to Anthropic's API.
2. Ask: `uv run qp ask "How did my portfolio do in USD this year?"`, or use the Assistant
   page in the terminal (conversations continue there).
3. Under each answer: the tools it used, and in red any number that does not appear in the
   tool results (rule 12). Treat flagged numbers as unreliable.
4. Transcripts: `data/assistant/transcripts.jsonl` (local, gitignored). Delete it whenever
   you like.

Model and effort: `assistant.model` (default `claude-opus-5-5`) and `assistant.effort`
(default `medium`) in `configs/base.yaml`. A server-side refusal fallback is on.
