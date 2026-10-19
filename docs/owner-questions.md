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
