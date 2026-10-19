# Phase 2 proposal — data lake and connectors

Status: **in progress** since the owner's "go" on 2026-10-19 (Phase 1's reconcile still open).
What was built and what is open: docs/ROADMAP.md and ADRs 0007-0009. Open questions:
`docs/owner-questions.md`.

## Goal

Replace the hand-maintained `data/manual/*.csv` with an automatically filled, point-in-time
Parquet lake, so that `qp portfolio show` values holdings from fetched prices and FX, and
later phases (performance, macro, research, backtests) have honest history.

## Proposed exit criteria

1. Lake round-trip and leak tests green: for random data with revisions, an `as_of` query
   never returns a row with `known_time > as_of` (hypothesis property test), and raw
   partitions can't be overwritten.
2. Every connector has fixture tests (no network in `make check`) and one real backfill run on
   the owner's machine, with results listed by `qp data status`.
3. `qp portfolio show --as-of D` values the owner's real holdings from the lake, with no
   missing or stale prices on a normal trading day, and the same numbers as the manual table
   where both have a value.
4. Quality checks run after each ingestion and their results are visible (`qp data quality`,
   Data page).
5. Adjusted prices are derived from our own `ref.corporate_actions`, known as of `t`; an
   action a provider reports that we don't have lands in a review queue, never silently in
   the data.

## Design

### Lake (`qp.data.lake`)

- **raw/** — provider payload bytes exactly as received, plus a JSON sidecar (`url` without
  secrets, `fetched_at` UTC, HTTP status, sha256, connector version). Write-once: an existing
  file is an error.
- **clean/** — typed Parquet rows. Every row: `source`, `valid_time`, `known_time`,
  `known_time_basis`, `ingested_at`, `raw_ref` (the raw file it came from) plus dataset columns.
  Appended as new files; never rewritten. Revisions are new rows with a later `known_time`.
- **derived/** — adjusted prices and returns, rebuilt from clean + corporate actions; disposable.
- **Queries** through DuckDB: `latest_known(dataset, keys, on, as_of)` = per key, the row with
  the greatest `valid_time <= on` among rows with `known_time <= as_of`, then the latest
  `known_time`. One function, used by every reader.

### The backfill `known_time` problem (needs a decision; ADR 0007)

A price for 2023-05-02 fetched today was really *published* on 2023-05-02 evening, but we only
*observed* it today. If `known_time` = fetch time, every historical `as_of` query returns
nothing and backtests can't run; if we invent a publication time silently, we lie.

Proposal: store both. `known_time` = the source's documented publication time for that
`valid_time` (e.g. TCMB indicative rates: 15:30 Istanbul on the bulletin date; BIST closes:
18:30 Istanbul + a configured lag), with `known_time_basis = 'assumed_publication'`; rows
fetched live get `known_time = max(published, fetched_at)` with basis `'observed'`. A later
fetch returning a *different* value for the same `valid_time` is kept as a revision with
`known_time = fetched_at`. Queries can be restricted to observed facts when needed.
Publication lags are dated config per source, cited in the ADR.

### Calendars (`qp.reference.calendars`)

Wrap `exchange-calendars` 4.13.2 (verified: XIST has 2026 Ramazan/Kurban holidays and the
half days 19 Mar, 26 May, 28 Oct). XIST for BIST and TEFAS, XNYS/XNAS for US, 24/7 for
crypto. Limitation: future holidays are those known to the installed library version (not
point-in-time); recorded in the ADR.

### Connectors (`qp.data.connectors`)

Shared: `httpx` client with per-source rate limit, retry with backoff, on-disk cache (the raw
zone *is* the cache), User-Agent `qp-personal`, secrets from settings only, never logged.
Each connector is `fetch(...) -> raw files` and a pure `parse(raw) -> clean rows`; tests run
`parse` on small hand-made fixtures shaped like the real responses (repo is private, but we
still don't commit provider data).

| Source | Data | Key | Status of terms | Proposed |
|---|---|---|---|---|
| TCMB `kurlar/*.xml` | daily indicative FX (USD, EUR, …) | none | public archive, verified reachable 2026-10-19 | yes — first FX source |
| TCMB EVDS | TÜFE, policy rate, deposit rates | owner's API key | EVDS 3 in beta since Jan 2026, API details unverified | yes, after key |
| FRED / ALFRED | US macro + vintages | owner's API key | official API | yes, after key |
| yfinance 1.7.0 | BIST and US EOD prices, splits/dividends | none | unofficial; Yahoo terms not confirmed | owner decision |
| TEFAS (`tefas-crawler` 0.6.0) | fund prices | none | no documented API or terms found | owner decision |
| ccxt (Binance public) | crypto OHLCV | none for public data | official exchange API | only if owner holds crypto |
| KAP, EDGAR, RSS | disclosures, fundamentals, news | — | — | deferred to phases 4–5 where they're used |

Yahoo caveat (correct numbers): Yahoo's `Close` is already split-adjusted, and Yahoo records
BIST bonus issues as splits. "Unadjusted" prices from Yahoo must be un-adjusted with Yahoo's
own split events and then checked against our `ref.corporate_actions`; mismatches go to the
review queue, never into the price.

### Corporate-action review queue

New table `ref.corporate_action_candidates` (source, reported terms, first seen, status).
`qp ref actions review` lists them; accepting one inserts a normal `ref.corporate_actions`
row (still append-only). Adjusted prices use only accepted actions known at `t`.

### Quality checks (`qp.data.quality`)

Per ingestion: missing sessions vs calendar, stale series, non-positive or NaN prices, day
moves beyond a threshold with no corporate action, duplicate keys, `known_time < valid_time`.
Results to `ops.data_quality_results`; warnings shown, never auto-fixed.

### Wiring

- Migration 0003: `ops.job_runs`, `ops.data_quality_results`, `ref.corporate_action_candidates`.
- Workers: one job per source after its publication time; `ops.job_runs` records each run.
- `LakeMarketData` implements the existing `MarketData` protocol; valuation uses
  manual rows first, then lake (owner-entered values win; the source is shown per row).
- CLI: `qp data fetch SOURCE [--since]`, `qp data status`, `qp data quality`,
  `qp ref actions review`. Terminal: a Data page (freshness and quality).
- ADRs: 0007 lake layout and point-in-time queries (incl. backfill `known_time`),
  0008 data sources and terms, 0009 calendars.

## Order of work (small commits)

1. Lake core + leak property tests. 2. Calendars. 3. TCMB FX connector + LakeMarketData →
first usable result: FX in `portfolio show` without manual entry. 4. Price source(s) chosen by
the owner. 5. Corporate-action candidates and adjusted prices. 6. EVDS / FRED (needs keys).
7. Quality checks, workers jobs, CLI, Data page. 8. Docs, ROADMAP, report.

## Risks

- yfinance and TEFAS can break or be blocked without notice; both are best-effort and every
  number they give carries its source.
- EVDS 3 is new; the connector will be built against its current documentation once the
  owner has a key, not against memory.
- Backfilled `known_time` is an assumption; it is labelled as one in every row.
