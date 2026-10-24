# Runbook: research lab (phase 5)

Conventions: ADR 0013.

## Studies

```bash
cp -r research/templates/study research/studies/my_idea   # edit study.yaml and study.py
uv run qp research list
uv run qp research run my_idea                       # result + provenance, never overwritten
uv run qp research run my_idea --param lookback=60   # an override is recorded as such
uv run qp research show my_idea
```

- Write the hypothesis and what would prove it wrong *before* running.
- `as_of` in `study.yaml` is binding: `ctx.market`, `ctx.series` and `ctx.lake(...)` only
  return facts known by then; asking for a later view raises an error.
- Every run is kept under `data/lake/results/studies/<id>/`. Count them honestly: the number
  of variants you tried is the `trials` input of the deflated Sharpe ratio.
- Commit before a run you want to cite: a run from uncommitted code is flagged as not
  reproducible from its commit.

Statistics to use (all in `qp.stats`): `ols` (HAC errors), `stationarity` (ADF + KPSS),
`engle_granger`, `garch11` (check `.reliable`), `bootstrap`, `sharpe`,
`probabilistic_sharpe`, `deflated_sharpe`, `pbo`.

## Fundamentals and valuation (US companies, SEC EDGAR)

```bash
# once: a contact User-Agent in .env, as SEC asks for automated access
#   QP_SOURCES__SEC__USER_AGENT="qp-personal Your Name you@example.com"
uv run qp data fetch sec --series AAPL --series MSFT    # or sources.sec.tickers
uv run qp value fundamentals AAPL
uv run qp value fundamentals AAPL --known-at 2025-06-30T00:00:00+00:00   # as filed by then
uv run qp value multiples AAPL --price 230
uv run qp value dcf --fcff 110,121,133 --rate 0.09 --growth 0.025 --net-debt 50 --shares 10
uv run qp value dcf --fcff 110,121,133 --rate 0.09 --growth 0.025 --market-ev 2400 --base-fcff 100
```

TTM = last annual + newest year-to-date − the prior year's same year-to-date. BIST companies
need KAP or a vendor (not built; owner question 22).

## Decisions journal

```bash
uv run qp journal add --title "..." --thesis "..." --invalidation "..." --review-on 2026-12-01
uv run qp journal list
uv run qp journal review 1 --verdict keep --next-review 2027-03-01 --note "checked ..."
uv run qp journal review 1 --verdict invalidated --note "criterion met: ..."
uv run qp journal note "Bought 100 THYAO" --thesis 1 --decision
uv run qp journal watch banks AKBNK.BIST
```

Nothing in the journal can be edited or deleted (database triggers). Due reviews are sent
as alerts by the 18:30 workers job and shown on the Journal page.
