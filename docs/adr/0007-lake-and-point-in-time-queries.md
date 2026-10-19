# ADR 0007 — Parquet lake and point-in-time queries

- Status: accepted (owner "go" on docs/phase-2-proposal.md, 2026-10-19). The backfill
  `known_time` rule (item 4) is the proposal's recommended default; the owner can still
  change it (docs/owner-questions.md, Q11).
- Date: 2026-10-19

## Context

Rule 5 requires every external fact to carry `source`, `valid_time` and `known_time`, and
every data function to take `as_of` and never return anything unknown at `as_of`. History
fetched today was mostly published long ago; if `known_time` were always the fetch time,
no past `as_of` could see any history, and if it were silently invented we would lie.

## Decision

1. **Zones** (`qp.data.lake`, paths from `qp.platform.lake`):
   - `raw/{source}/{dataset}/fetched_date=YYYY-MM-DD/` holds provider payloads exactly as
     received, plus a `.meta.json` sidecar (redacted URL, status, sha256, fetched_at). Files
     are created exclusively and made read-only; writing an existing name is an error.
   - `clean/{dataset}/part-*.parquet` holds typed rows, appended as new files (written to a
     temporary name, then renamed, so readers never see partial files). Nothing is
     rewritten.
   - `derived/` is for rebuildable outputs (adjusted series); `results/` for research.
2. **Point-in-time columns on every clean row**: `source`, the dataset's valid date,
   `known_time`, `known_time_basis`, `fetched_at`, `raw_ref`. Invariant:
   `known_time <= fetched_at`. Timestamps are tz-aware UTC (naive values are rejected).
   Money-like values are `decimal128(38, 18)`, read back as exact `Decimal`.
3. **Revisions are rows, not updates.** An append drops a row whose values equal what was
   known for that series and valid date at its `known_time`. A changed value from a
   schedule-based or plain fetch is a revision learned now: `known_time = fetched_at`,
   basis `fetched`. Two different values for one series, date and instant in one fetch are
   rejected (the "latest" would be undefined).
4. **`known_time_basis`** says how `known_time` was set:
   - `schedule`: the source's documented publication time, capped at `fetched_at`
     (TCMB indicative FX: 15:30 Istanbul on the bulletin date).
   - `source`: stated by the source (ALFRED vintage date; end of that day in St. Louis
     time, the conservative choice since only the date is given).
   - `fetched`: first seen by qp (EVDS has no vintages; every revision).
5. **One query function.** `CleanStore.query(as_of=...)` returns, per series and valid date,
   the row with the greatest `known_time <= as_of` (ties: latest `fetched_at`).
   `observed_only=True` also requires `fetched_at <= as_of` (only what qp itself had seen).
   `latest_on` picks the greatest valid date `<= on` from that. DuckDB reads the Parquet
   files; results come back through Arrow so datetimes stay tz-aware and decimals exact.
6. **Tests**: a hypothesis property compares `query` with a brute-force model on random
   facts, revisions and fetch delays (including `observed_only`); mutating the query
   (look-ahead or wrong ordering) makes it fail.

## Consequences

- Historical `as_of` views work from day one of a backfill, and every row says whether its
  `known_time` is an assumption, so studies can exclude assumed times (`observed_only`).
- The lake never deletes or rewrites; disk grows with each fetch (raw ~7 MB for a year and
  a half of TCMB bulletins).
- A source whose publication schedule we do not know is stored with basis `fetched` and is
  invisible to past `as_of` views until a schedule is documented and cited.
