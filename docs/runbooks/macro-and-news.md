# Runbook: macro and news (phase 4)

Conventions: ADR 0012 (and ADR 0011 for why OpenBB is not used).

```bash
make db-up && make migrate                       # migration 0005 adds the news tables

# News: official feeds only (TCMB TR/EN, Fed monetary policy, ECB); metadata and links
uv run qp news fetch                             # then holding-news alerts (Telegram or log)
uv run qp news list --days 14
uv run qp news list --tag interest_rates
uv run qp news list --entity THYAO.BIST --entity TCMB
uv run qp news relink                            # after editing configs/news_rules.yaml

# Macro (FRED needs QP_SOURCES__FRED__API_KEY; EVDS needs its key and confirmed codes)
uv run qp data fetch fred                        # indicators and the Treasury curve series
uv run qp data fetch fred-calendar               # release dates, incl. future ones
uv run qp macro indicators                       # registry with the latest value of each
uv run qp macro show us_cpi --transform yoy
uv run qp macro show us_cpi --transform yoy --known-at 2026-03-01T00:00:00+00:00
uv run qp macro revisions us_payrolls --earlier 2026-09-01T00:00:00+00:00
uv run qp macro curve
uv run qp macro calendar --days 14 --country US --country TR
```

Workers (`make workers`): news every 30 minutes (then holding-news alerts); FRED release
dates daily at 07:10 when keyed. Terminal: Macro and News pages.

## Adding things

- **An indicator**: a row in `configs/indicators.yaml` (`series` as `fred:ID`, `evds:CODE` or
  a `data/manual/series.csv` id). For FRED also add the id to `sources.fred.series` in
  `configs/base.yaml`, then `qp data fetch fred`.
- **A feed**: an entry under `sources.news.feeds` (code, name, url, `naive_tz` if its dates
  have no offset, `public_institution`, `institution`). Only feeds whose terms allow it;
  never article bodies.
- **Turkish releases and MPC dates**: `data/manual/calendar.csv`:

  ```csv
  day,event,title,time,country,category,source,known_time
  2026-11-03,tr_cpi,TÜFE Ekim 2026,10:00,TR,inflation,TÜİK 2026 takvimi,2026-01-02T10:00:00+03:00
  ```

  `known_time` is when the date was announced (the calendar is point in time).

## When something fails

- `qp data status` / Data page: last `fetch:news` and `fetch:fred_calendar` runs.
- A feed that cannot be parsed keeps its payload in `data/lake/raw/news/<feed>/`; the
  error names the file. One bad item (e.g. an unparseable date) is skipped and reported.
- TCMB's MPC feed is deliberately not used: it stopped updating in Dec 2025 (decisions are
  in the press-release feed).
