# ADR 0015 — Paper trading: daily paper broker, risk gate, kill switch, promotions

- Status: accepted (owner standing instruction, 2026-10-25). *Default* items can change.
  Deviates from ARCHITECTURE's "Nautilus TradingNode" picture for paper mode, for the
  reason below; the layer map is unchanged.
- Date: 2026-10-25

## Context

The platform's strategies trade daily closes (TCMB rates now; BIST/US closes once a price
source exists). NautilusTrader 1.231 has no data or execution adapter for TCMB or for BIST
end-of-day data, and its sandbox execution still needs a live data client. A `TradingNode`
for paper would need a custom data adapter for data that arrives once a day.

## Decision

1. **Daily paper broker** (`qp.execution.paper`): after the evening data jobs, for each
   deployed strategy in `paper` status, one idempotent cycle per new bar:
   (a) the pending target from the previous bar is executed at the new bar's close with
   `order_delta` and the dated cost model, i.e. the functions `QPStrategy` and the reference
   use, so paper and backtest agree by construction; (b) the pure signal computes the next
   target from the closes known now, stored as pending. A rerun of the same day is a no-op.
2. **Order path**: target → `qp.risk` pre-trade gate → paper fill. The gate rejects on: kill
   switch (global or the strategy's), strategy not in a status allowed for the mode, stale
   data (latest close older than 5 calendar days *(default)*), a target outside [0, 1], an
   order notional above 105% of equity *(default)*, and halts the strategy when its equity
   falls more than 5% in one bar *(default daily loss limit)*. Every decision is stored.
3. **Kill switch**: `trading.trading_state` events (scope `global` or a strategy name),
   append-only; the latest event per scope wins. Halt = no new orders and pending targets
   dropped. **Flatten** is a separate explicit command that sets a zero target.
4. **Promotions**: `research → backtested → paper → approved_live → retired`, append-only
   `trading.promotions` with evidence. `backtested` needs a stored, valid (costed)
   walk-forward result; `paper` needs `backtested`. **`approved_live` can only be recorded
   by the owner**: the CLI requires an interactive terminal and a typed confirmation, and the
   library refuses it without that flag. The agent never runs it (AGENTS.md).
5. **Ledger bridge**: each paper fill is turned into ledger transactions with the canonical
   builders (buy/sell; FX as `fx_conversion`) and replayed in memory with the ledger engine
   for reconciliation against the paper broker's positions. In paper mode nothing is
   written to the owner's ledger (paper money must not change real net worth); live mode
   (phase 8) writes to trading accounts.
6. **Reconciliation** after every cycle: broker position vs bridged-ledger position vs the
   reference simulation over the same bars. Any mismatch halts the strategy and alerts.
7. **Heartbeat**: each cycle writes `trading.heartbeats`; the workers alert when the last
   paper heartbeat is older than 30 hours on a weekday *(default)*.
8. **Paper-vs-backtest report**: the reference backtest over the bars the strategy has been
   paper-trading, compared day by day; differences can only come from data revised after the
   fact, which the report lists.
9. Tables in schema `trading` (migration 0007); ORM models live in `qp.risk.models` because
   the gate (below `qp.strategies` and `qp.execution`) reads them.
