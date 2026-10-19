# Runbook: data lake and sources

Everything lands in `data/lake/` (gitignored). Raw payloads are kept exactly as fetched;
clean rows are point-in-time (ADR 0007). Each run is recorded in `ops.job_runs`.

```bash
make db-up && make migrate                       # migration 0003 adds ops tables

# TCMB indicative FX (no key). First time: backfill; later runs resume automatically.
uv run qp data fetch tcmb --since 2025-01-01      # ~1 request/s, ~8 min for 2025-2026
uv run qp data fetch tcmb                         # resume after the newest stored bulletin
uv run qp data quality                            # missing days, staleness, jumps, look-ahead
uv run qp data status                             # datasets and recent job runs

# FRED (needs QP_SOURCES__FRED__API_KEY in .env): every vintage of sources.fred.series
uv run qp data fetch fred
uv run qp data fetch fred --series UNRATE

# EVDS (needs QP_SOURCES__EVDS__API_KEY in .env). Confirm codes first, then add them to
# sources.evds.series in configs/base.yaml.
uv run qp data evds-series TP.TUKFIY2025.GENEL
uv run qp data fetch evds --since 2015-01-01

# Scheduled: TCMB Mon-Fri 15:45 and 18:15 Istanbul (+ quality), FRED 07:00, EVDS 17:30
make workers
```

## Valuation

`qp portfolio show` and the Portfolio page use your manual table (`data/manual/`) first and
the lake second: the newest date wins, and on the same date your manual value wins. FX uses
the TCMB rate kind in `sources.tcmb.rate_kind` (default `forex_buying`, döviz alış). Prices
are still manual until a price source is chosen (docs/owner-questions.md).

## Corporate actions reported by sources

```bash
uv run qp ref actions list                        # pending candidates
uv run qp ref actions accept 12 --note "checked against KAP disclosure"
uv run qp ref actions reject 13 --note "wrong ex-date"
```

Accepting inserts an append-only corporate action; nothing uses a candidate before that.

## When something fails

- `qp data status` shows the last runs; the detail column says what was missing or failed.
- A day with no TCMB bulletin (holiday, half day) is never requested. A business day that
  returns 404 is reported as missing and retried on the next run.
- A payload that cannot be parsed is still kept in `raw/` (the error message gives the path).
- EVDS 403: the key is missing or wrong. FRED 400 "api_key is not registered": the same.
