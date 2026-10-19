# ADR 0008 — Data sources, terms of use and valuation defaults

- Status: accepted for the sources below (owner "go", 2026-10-19). Items marked *default*
  are the proposal's recommendations pending the owner's answers (docs/owner-questions.md).
- Date: 2026-10-19

## Context

Rule 11: respect providers' terms and rate limits, cache responses, do not scrape sites that
forbid it. Phase 2 needs FX for valuation, and macro series for later phases.

## Decision

| Source | Access | Terms / limits | Status |
|---|---|---|---|
| TCMB indicative FX (`tcmb.gov.tr/kurlar/YYYYMM/DDMMYYYY.xml`) | public archive, no key | public; 1 request/s, archive pages cached in `raw/` | built, backfilled 2025-01-01.. on the owner's machine |
| TCMB EVDS 3 (`evds3.tcmb.gov.tr/igmevdsms-dis/`) | API key in the `key` header | TCMB terms (docId 21): cite the source, TCMB may limit queries; 1 request / 2 s, one series-year per request | built; first keyed run pending (owner key) |
| FRED / ALFRED (`api.stlouisfed.org`) | API key (query parameter, redacted everywhere) | FRED terms of use; 1 request/s | built; first keyed run pending (owner key) |
| Yahoo Finance (yfinance) | unofficial | Yahoo's terms not confirmed to allow automated use | **not built**: owner decision (Q7) |
| TEFAS (`tefas-crawler`) | undocumented endpoints | no terms found | **not built**: owner decision (Q8) |
| ccxt (Binance public) | official exchange API | exchange terms | not built: only if the owner holds crypto (Q14) |
| KAP, SEC EDGAR, RSS | — | — | deferred to phases 4–5, where they are used (Q13) |

1. **Shared HTTP client**: a fixed User-Agent (`qp-personal/...`), per-source minimum
   interval, retries only on timeouts, 429 and 5xx with exponential backoff and a numeric
   `Retry-After` honoured, no redirects followed. Only redacted URLs are logged or stored.
2. **Keys** come only from env/.env (`QP_SOURCES__EVDS__API_KEY`,
   `QP_SOURCES__FRED__API_KEY`); a key in YAML is a ConfigError; all secrets are scrubbed
   from logs and job-run records.
3. **TCMB publication calendar**: XIST full trading days; no bulletin on the half days before
   religious holidays (checked against the archive for 28 Oct 2025, 19 Mar and 26 May 2026).
4. **FX for valuation** (*default*, Q10): the TCMB *döviz alış* (ForexBuying) rate,
   configurable as `sources.tcmb.rate_kind`. Rates are stored as published (value per
   unit, e.g. JPY per 100) and divided at read time.
5. **Manual values vs fetched data** (*default*, Q12): valuation layers the owner's manual
   table over the lake; the point with the newest valid date wins and, on the same date,
   the manual value wins. The source is shown with every value.
6. **Import FX stays explicit.** Ledger rows still need `fx_rate_try` (ADR 0006); the lake is
   not used to fill it silently.
7. **EVDS series** are configured only after confirming codes with a key
   (`qp data evds-series CODE`): TCMB archived `TP.FG.J0` after TÜİK's 2025=100 rebase.

## Consequences

- Phase 2 values FX positions and cash from fetched TCMB rates; prices stay manual until the
  owner picks a price source, so exit criterion 3 (holdings valued from the lake) is met for
  FX only.
- Adding Yahoo or TEFAS later is a new connector module plus a row in this table.
