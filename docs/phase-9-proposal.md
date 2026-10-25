# Phase 9 proposal — optimization, scenarios, assistant

Status: accepted 2026-10-25 under the owner's standing instruction ("go with your
recommendation and continue on other phases"); ADR 0016. Phase 8 (live) is skipped until the
owner approves it explicitly; phase 9 does not depend on it.

## Goal

Answer three questions about the current portfolio with the data already in the platform:
what allocation a stated model would choose, what a stated shock or a past episode would do to
today's holdings, and let the owner ask questions in plain language that are answered only
with numbers computed by `qp`.

## Exit criteria

1. `qp.optimization`: minimum variance, maximum Sharpe (mean-variance), risk parity, HRP and
   minimum CVaR on daily TRY returns, long-only with a per-asset cap; each result carries
   ex-ante volatility, risk contributions, historical CVaR and the TRY trades from the current
   weights. Tested against closed forms and invariants.
2. `qp.scenarios`: shock scenarios (FX vs TRY, asset class, instrument, rate shock with a
   stated duration) and historical replays of named windows on today's holdings, plus the
   worst observed k-day loss. Holdings without data are listed, never filled.
3. `qp.assistant`: read-only tool-using assistant over `qp` functions (Claude API). Every
   number in an answer is checked against the tool results; unverified numbers are flagged.
   Works without a key (disabled with a clear message); tested with a fake client.
4. CLI `qp optimize`, `qp scenario`, `qp ask`; terminal pages Optimization, Scenarios,
   Assistant.
5. `make check` green; docs: ADR 0016, runbook, ROADMAP, AGENTS commands, owner questions.

## Not in scope

Orders of any kind (the optimizer proposes weights; nothing reaches the trading gate),
Black-Litterman views, factor-model covariance, intraday scenarios, an assistant that writes
(journal entries, deployments, data fetches).
