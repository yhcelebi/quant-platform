# Phase 4 proposal — macro and news center

Status: accepted 2026-10-25 (owner: "go with your recommendation": official sources only, no
KAP connector, no LLM tagging, OpenBB evaluated first: ADR 0011). Conventions: ADR 0012. Phase 3 is built but not
closed: its CI run and the owner reference cases (owner questions Q16) are still open.

## Goal

One place to see the macro picture (indicators, how they were revised, yield curves, what
is released when) and the news that matters to the portfolio, all point in time, with
holding-related news raising alerts.

## Proposed exit criteria

1. **Indicator registry** (`configs/indicators.yaml`): id, name, source series
   (`fred:`/`evds:`/manual), frequency, unit, category. Transformations (MoM, YoY, diff,
   annualized, rolling mean, rebased index) with hand-calculated tests; every query takes
   `as_of` and a property test shows that no transformed value uses data unknown at `as_of`.
2. **Vintages view**: a series as known at two dates and the revisions between them (ALFRED
   vintages; EVDS snapshots), tested on fixtures.
3. **Yield curves**: US Treasury curve and spreads (10y-2y, 10y-3m) from FRED constant
   maturities; a TR curve once EVDS codes are confirmed; linear interpolation, tested.
4. **Economic calendar**: FRED release dates (`fred/releases/dates` with
   `include_release_dates_with_no_data=true` for future dates), TCMB MPC dates and TÜİK
   releases from an owner-maintained `data/manual/calendar.csv` (no machine-readable TÜİK
   calendar found); stored point in time, because schedules move.
5. **News**: RSS/Atom ingestion from configured feeds (fixture tests, no network in
   `make check`, one real run), `known_time = max(published_at, fetched_at)`, metadata only
   (title, link, times, source, categories), dedup by canonical URL and normalized title,
   entity linking to instruments and currencies (Turkish-aware case folding), tags from
   keyword rules in config.
6. **Holding-related alerts**: a news item linked to an instrument held today becomes an
   alert through the Phase 3 alert store (once per item).
7. `qp macro ...`, `qp news fetch / list`; Macro and News pages; workers jobs.
8. `make check` green in CI.

## Sources checked (2026-10-25)

| Source | Finding |
|---|---|
| TCMB press releases, MPC decisions (Atom) | Respond with 200 and Atom XML; dates are Turkish-locale strings (`22 Eki 2026 14:00:00`), so they need their own parser; feed `updated` is stale (2024) while entries are current |
| FRED `fred/releases/dates` | Documented; future dates only with `include_release_dates_with_no_data=true`; needs the FRED key |
| TÜİK release calendar | Published yearly (2026 on 2 Jan) and shown on e-Devlet; no download or API found |
| KAP | Plain HTTP requests get an error page (incl. robots.txt); no public API terms found. **Not built** without an owner decision |
| US Treasury daily yield CSV | Request timed out; not verified. FRED (keyed) is the fallback |

## Design

- `qp.macro`: `registry`, `transforms` (pure), `vintages`, `curves`, `calendar`. Macro data
  stays in the lake (existing FRED/EVDS datasets, plus a `calendar_events` clean dataset).
- `qp.news`: `feeds` (connector: shared HTTP client, raw payloads in `raw/news/`), `parse`
  (RSS 2.0 and Atom, Turkish dates), `dedup`, `entities`, `tags`; PostgreSQL tables
  `news.sources`, `news.items`, `news.item_entities`, `news.item_tags` (migration 0005).
- `qp.alerts`: a `holding_news` rule.
- LLM tagging: an interface that is off by default and unused (rule 12; needs a key and
  your decision).

## Open questions for the owner

1. **News sources.** Recommended to start with official sources only: TCMB press releases
   and MPC decisions (TR), Fed and ECB press releases. Commercial outlets (AA, Bloomberg HT,
   …) only where their RSS terms allow personal use; which ones do you want?
2. **KAP** disclosures are the most useful holding news, but there is no public API or terms.
   Options: skip; enter manually; a licensed data vendor; or check whether KAP offers access.
3. **API keys** (Q9) decide most of the macro side: without FRED/EVDS keys only the
   calendar file and TCMB feeds work.
4. **LLM tagging**: leave unbuilt (recommended) or build the off-by-default interface now?

## Risks

- Feeds change format or move (TCMB's `updated` is already stale); every parse failure keeps
  the raw payload and is reported in `ops.job_runs`.
- Entity linking by name gives false positives (e.g. "THY" in unrelated text); links show
  the matched text, and alerts use instrument symbols and registered aliases only.
