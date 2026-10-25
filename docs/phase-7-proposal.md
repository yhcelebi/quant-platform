# Phase 7 proposal — paper trading

Status: accepted 2026-10-25 under the owner's standing instruction; ADR 0015.

## Goal

Run strategies forward on real data with real controls, before any real money: risk gate,
kill switch, promotions with evidence, heartbeat, reconciliation, a paper-vs-backtest report.

## Exit criteria

1. Trading tables (migration 0007), append-only where they record decisions.
2. Pre-trade gate and kill switch tested on every rejection reason.
3. Promotions with evidence; `approved_live` refused without the owner's interactive
   confirmation.
4. Daily paper cycle: idempotent, gate-checked, reconciled; workers schedule it.
5. `qp trader deploy / promote / run / status / halt / resume / flatten / report`; Trading
   page with the kill switch.
6. **4+ weeks of paper trading and a paper-vs-backtest report**: needs elapsed time; starts
   when the owner runs the workers. Not achievable inside one session.
