# Questions for the owner

Written 2026-10-19 overnight. Answer inline (or just reply in chat with the numbers).
Recommended answers are marked; "ok" accepts the recommendation.

## Phase 1 — needed to finish (blocking)

1. **Broker files.** No broker exports exist on this machine yet (searched Desktop,
   Downloads, Documents by file name). Please download, per broker/bank:
   - transaction history (Hesap ekstresi / İşlem geçmişi), Excel or CSV if offered,
     from account opening, or from a cut-off date (then I add opening balances);
   - a holdings statement (Portföy / Varlık) for one date.

   Put them in `data/private/broker/` (gitignored; already created).

2. **Which brokers / banks** are they, and what do you hold there? (BIST shares, US shares,
   TEFAS funds, FX deposits, crypto, Eurobonds, gold…) This decides the converters I write
   and the Phase 2 data sources.

3. **Start of history:** full history from account opening, or opening balances on a
   cut-off date? *Recommended: a cut-off date (e.g. 2024-12-31) with opening balances,
   unless you want realized P&L for earlier years.*

4. **Are any of these in your history?** They are not supported yet and would need work:
   mergers with a cash part, buying/selling rights (rüçhan hakkı) rather than subscribing,
   cash-in-lieu for fractional shares, a merger into a different currency, shares in transit
   between brokers across a split/bonus/dividend ex-date.

## Phase 1 — decisions (non-blocking)

5. **Correcting a same-day trade.** If you reverse a trade and re-enter it, untimed trades
   on that day are ordered by recording time, so the re-entered trade moves after trades
   imported earlier, which can change realized P&L. Options:
   - (a) keep the rule; give `trade_time` to trades on busy days (now in the runbook).
     *Recommended.*
   - (b) change the rule (needs an ADR 0005 amendment): a corrected trade inherits the
     original's position in the day.

6. **Negative cash.** It's now a warning, not an error, because margin or overdraft can be
   real. *Recommended: keep as a warning.* Should it be an error for some accounts?

## Phase 2 — status after your "go" (2026-10-19)

Built and running; see docs/ROADMAP.md, ADRs 0007-0009 and docs/runbooks/data.md. The
recommended defaults are in effect for Q10, Q11 and Q12 (change any of them by answering
below). Q7, Q8 and Q14 are not built until you answer. Q9 blocks the FRED and EVDS runs.

7. **Yahoo Finance (yfinance)** for BIST and US end-of-day prices: unofficial; Yahoo's terms
   are not confirmed to allow it. Use it as a best-effort, personal-use source? Or keep
   prices manual / find another source?

8. **TEFAS fund prices** via `tefas-crawler`: no documented API or terms found. Use it,
   politely rate-limited? Only matters if you hold funds.

9. **API keys** (blocks FRED and EVDS). Register and put them in `.env`, never in chat or git:
   - TCMB EVDS (TÜFE for real returns, deposit rates): evds3.tcmb.gov.tr → Benim Sayfam →
     Profilim → API Key Kopyala → `QP_SOURCES__EVDS__API_KEY=...`
   - FRED (US macro): fredaccount.stlouisfed.org → API Keys → `QP_SOURCES__FRED__API_KEY=...`

   Then run `uv run qp data fetch fred` and `uv run qp data evds-series TP.TUKFIY2025.GENEL`.
   Or say "keys are in" and I'll do the first runs and confirm the TÜFE code.

10. **FX convention for valuation.** TCMB publishes buying and selling rates. *Recommended:
    the döviz alış (ForexBuying) rate, which is also the tax convention.* Alternative: the
    mid rate.

11. **Backfilled history and `known_time`.** A price for 2023 fetched today was published in
    2023, but we only saw it today. *Recommended:* store the documented publication time,
    labelled `schedule` (in effect now), and keep the fetch time in `fetched_at`. Details:
    ADR 0007.

12. **Manual vs. fetched prices.** *Recommended: your manual values win, and every row shows
    its source.*

13. **KAP, EDGAR and RSS** are deferred to Phases 4–5, where they're used. ok?

14. **Crypto.** Do you hold any? If not, ccxt is skipped in Phase 2.

15. **Docker Desktop stopped twice today** (the database went down mid-session). I
    restarted it with `open -a Docker` each time. Do you quit it, or does it crash or sleep?
    If you want the database always on, enable "Start Docker Desktop when you sign in" and
    turn off Resource Saver in Docker Desktop settings.

## Phase 3 — performance and risk (built 2026-10-25)

Recommended defaults are in effect (ADR 0010); change any by answering.

16. **Proposed reference cases** for `tests/reference/` (owner-owned; add them if you agree,
    I will wire the loader). Each is hand-calculated in the test docstrings:
    - *g, TWR with a transfer out of the scope*: account A deposits 1000, buys 10 @ 50; price
      60, sends 5 shares to B; price 70 when B receives them. Scope {A}: daily values 1000,
      1000, 1100, 1100, 850 with flow -350 on the last day; TWR = 0.2
      (`tests/unit/performance/test_history_metrics.py`).
    - *h, attribution*: the 6-10 Jan 2025 example in
      `tests/unit/performance/test_attribution_benchmarks.py`: P&L 1083 = THYAO 745 + AAPL 228
      (price 148, FX 80) + USD cash 120 + TRY cash 0 + fees -10.
    - *i, historical VaR*: losses 1..100 → VaR95 95, ES95 97.5, VaR99 99, ES99 99.5
      (`tests/unit/stats/test_descriptive.py`).
    - *j, the example ledger end to end*: TWR 28.42%, real 26.02%, attribution 11389.50
      (`tests/integration/test_cli_perf_risk.py`).

17. **Flow timing.** Flows count at the end of their day (the day's market move belongs to
    the capital before the flow). *Recommended: keep.* Alternative: start of day.

18. **Inflation interpolation.** A monthly TÜFE level is treated as the end-of-month level
    and interpolated geometrically inside the month. *Recommended: keep.* Alternative: step
    (use the last published month as is).

19. **Benchmarks.** Configured: USD/TRY, EUR/TRY, BIST 100 (`XU100.BIST`), S&P 500 TR
    (`SPXTR.INDEX`), gold (`XAUUSD.SPOT`, USD per ounce), TRY deposit rate (series
    `TRY_DEPOSIT`). Only USD/TRY and EUR/TRY have data today (TCMB). Which do you want, and
    which deposit rate (EVDS code) should stand for "TRY deposit"?

20. **Alert thresholds**: daily loss 3%, drawdown 10%, VaR 99%, stale price 7 days. Telegram
    needs `QP_TELEGRAM__BOT_TOKEN` and `QP_TELEGRAM__CHAT_ID` in `.env`; until then alerts go
    to the log and `qp alerts list`.

## Phase 4 — macro and news (built 2026-10-25, recommended defaults)

21. **News sources**: official only (TCMB TR/EN, Fed monetary policy, ECB). Add commercial
    feeds only if their terms allow personal automated use; tell me which.
22. **KAP**: not built (no public API or terms; plain requests are refused). Options: skip,
    manual entry, a licensed vendor, or ask KAP (kapdestek@mkk.com.tr) about access.
23. **TÜİK and MPC dates**: please fill `data/manual/calendar.csv` from TÜİK's 2026 calendar
    and TCMB's MPC schedule (no machine-readable source found), or ask me to draft rows for
    you to check against the official pages.
24. **EVDS codes** in `configs/indicators.yaml` (TÜFE, USD/TRY) are marked "to confirm" and
    need your key.

## Phase 5 — research lab (built 2026-10-25, recommended defaults)

25. **SEC User-Agent**: SEC asks automated clients for a name and contact email. Put yours
    in `.env` as `QP_SOURCES__SEC__USER_AGENT="qp-personal <name> <email>"` and list tickers
    in `sources.sec.tickers`. I have not used your email anywhere.
26. **First study result** (`usdtry_volatility`): GARCH(1,1) does not fit TCMB USD/TRY daily
    changes (fit at a parameter boundary, flagged unreliable); returns are stationary with
    about 3% annualized volatility and ~19% annualized drift over 2025-01..2026-10. Worth a
    different model (e.g. regime or jump) if you care about TRY volatility.

## Phase 6 — backtesting (built 2026-10-25, recommended defaults)

27. **Cost schedule**: `backtest.costs` in `configs/base.yaml` holds placeholder rates
    (BIST: 0.15% commission + 5% BSMV on it + 0.05% half spread + 0.05% slippage). Please
    replace with your broker's actual commission schedule (with dates if it changed).
28. **Dependencies**: NautilusTrader is now installed by default (`trading` group is a
    default uv group, ~156 MB) so CI checks the backtest code. ok?
29. **First real results** (TCMB USD/TRY, 2025-01..2026-10): SMA(10/50) +34.9% vs buy and
    hold +39.7%; walk-forward PBO 54% (parameter choice adds nothing out of sample). TCMB
    indicative rates are not tradable prices; treat these as plumbing checks, not evidence.

## Phase 7 — paper trading (built 2026-10-25, recommended defaults)

30. **Paper broker instead of a Nautilus TradingNode** for daily strategies (ADR 0015): no
    Nautilus adapter exists for TCMB/BIST daily data. Same signal, sizing and costs as the
    backtest. ok? A TradingNode comes with a live broker adapter (phase 8).
31. **Gate limits** (defaults): stale data 5 days, order notional 105% of equity, daily loss
    5% halts. Change in code review if you want different ones.
32. **Starting the 4-week clock**: needs `make workers` running on weekdays (or a VPS,
    phase 8). Which strategy do you want on paper first? I deployed none in your database.

## Phase 9 — optimization, scenarios, assistant (built 2026-10-25, recommended defaults)

Phase 8 (live) was skipped: it needs your explicit approval. Phase 9 does not depend on it.

33. **Assistant and your data**: with a key set, questions and the figures the tools return
    (holdings, values, P&L) go to Anthropic's API. Off until you add
    `QP_ASSISTANT__API_KEY` to `.env`. Default model `claude-opus-5-5`, effort `medium`.
    ok?
34. **Scenario sizes** in `configs/scenarios.yaml` are my assumptions (TRY -20%/-40%,
    BIST -25%, global risk-off, rates +500bp; five replay windows 2018-2025). Please
    adjust, and add modified durations for any bonds you hold.
35. **Replay history**: the lake's TCMB rates start 2025-01-02, so the 2018-2023 replays
    are unavailable. Backfill with `uv run qp data fetch tcmb --since 2018-01-01`? (TCMB
    asks for at most one request per second; about 2,000 requests, ~35 minutes.)
36. **Optimization defaults**: 500-day lookback, Ledoit-Wolf covariance, no per-asset cap,
    risk-free 0 for max Sharpe. A TRY deposit rate as `optimization.risk_free` makes
    max Sharpe more meaningful in TRY terms.
