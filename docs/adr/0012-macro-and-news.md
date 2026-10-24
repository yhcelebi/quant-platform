# ADR 0012 — Macro registry, calendar and news conventions

- Status: accepted (owner "go" with the recommended defaults, 2026-10-25)
- Date: 2026-10-25

## Decision

### Macro

1. **Indicator registry** in `configs/indicators.yaml`: `id`, `name`, `series` (a series id
   as in `qp.data.series`: `fred:ID`, `evds:CODE`, or a manual id), `frequency`
   (`daily|weekly|monthly|quarterly|annual`), `unit`, `category`, `country`. Indicators whose
   source has no data yet are listed and shown as unavailable.
2. **Transformations** are pure functions of an ascending, point-in-time history (already
   limited to `known_time <= as_of`), so no transformed value can use later data:
   `level`, `change` (difference over k periods), `pct_change` (k periods; MoM = 1, YoY = 12
   monthly / 4 quarterly / 52 weekly), `annualized` (`(1 + r)^(periods per year / k) - 1`),
   `rolling_mean` (k), `rebased` (100 at a chosen date). A period with a missing
   predecessor yields no value. Money-like exactness is not needed: macro analytics are
   float64, converted at the qp.core boundary.
3. **Vintages**: a series as known at `t1` and `t2`; revisions are dates whose value differs
   (ALFRED vintages; EVDS snapshots, `known_time` = fetch time).
4. **Yield curves**: tenors map to series (US: FRED `DGS1MO, DGS3MO, DGS6MO, DGS1, DGS2,
   DGS3, DGS5, DGS7, DGS10, DGS20, DGS30`, percent). The curve on day `d` takes, per tenor,
   the latest value on or before `d` known at `as_of`, and flags tenors older than 5 days.
   Linear interpolation in years between known tenors, no extrapolation. Spreads
   (10y−2y, 10y−3m) in percentage points.
5. **Economic calendar** is a lake dataset `calendar_events` (point in time: schedules move;
   a changed date is a revision). Sources: FRED `fred/releases/dates` with
   `include_release_dates_with_no_data=true` (date only, US), and the owner's
   `data/manual/calendar.csv` (TÜİK, TCMB MPC; `known_time` required).

### News

6. **Sources**: official institutions only by default (owner default): TCMB press releases
   TR and EN (Atom), Federal Reserve monetary-policy press releases and ECB press releases
   (RSS 2.0). The TCMB MPC feed is not used: it stopped updating in Dec 2025 while the press
   feed carries the decisions. KAP is not built (no public API or terms; plain requests are
   refused). Polling at most every 30 minutes per feed, through the shared HTTP client.
7. **Stored**: metadata only: source, title, canonical link, published time, fetched time,
   categories, and the feed's own summary only when the source is a public institution
   and the summary is under 500 characters. Never article bodies.
   `known_time = max(published_at, fetched_at)` (DOMAIN). Raw feed payloads go to the lake
   `raw/news/`.
8. **Dates**: RFC 822 (RSS), ISO 8601 (Atom), TCMB's `22 Eki 2026 14:00:00` (Turkish month
   abbreviations) and `Oct 22, 2026, 2:00:00 PM`; times without an offset are Europe/Istanbul
   for TCMB. An unparseable date rejects the item, with the error recorded.
9. **Dedup**: one item per canonical URL (scheme and host lower-cased, `http` → `https`,
   duplicate slashes, fragments and tracking parameters removed); an item with the same
   normalized title from the same source within 48 hours is a duplicate.
10. **Entities**: instruments by symbol and registered aliases (whole-word, upper case), by
   name (Turkish-aware case folding: `İ→i`, `I→ı`), currencies by ISO code and common
   names, institutions (TCMB, Fed, ECB). Every link stores the matched text.
11. **Tags**: keyword rules in `configs/news_rules.yaml` (e.g. `interest_rates`, `inflation`,
   `fx`, `dividend`, `earnings`), matched on the folded title and summary.
12. **Holding news**: an item linked to an instrument held in any account on its published
   day raises a `holding_news` alert once per (item, instrument).
13. **LLM tagging**: not built (owner default; rule 12).
