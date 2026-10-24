# ADR 0013 — Research lab: statistics, studies, fundamentals, valuation, journal

- Status: accepted (owner standing instruction, 2026-10-25). Items marked *default* can be
  changed by the owner.
- Date: 2026-10-25

## Decision

### Statistics (`qp.stats`, float64, statsmodels 0.15 / arch 8.0 / scipy 1.18)

1. **OLS** with Newey-West (HAC) standard errors, lag `floor(4 (n/100)^(2/9))` *(default)*.
2. **Stationarity**: ADF (constant, AIC lag choice) and KPSS (level); reported together
   because their nulls are opposite. **Cointegration**: Engle-Granger.
3. **GARCH(1,1)** with normal errors *(default)* on returns in percent (arch's scaling);
   reports omega, alpha, beta, persistence and the next-period volatility.
4. **Bootstrap**: stationary bootstrap (Politis-Romano) with mean block length
   `n^(1/3)` *(default)*, seeded; percentile confidence intervals.
5. **Sharpe ratio** per period, annualized by √(periods per year). **Probabilistic Sharpe
   ratio** (Bailey & López de Prado 2012):
   `PSR(SR*) = Φ((SR − SR*) √(n − 1) / √(1 − γ3·SR + (γ4 − 1)/4·SR²))`, per-period SR,
   skewness γ3, kurtosis γ4 (not excess). **Deflated Sharpe ratio** (2014):
   `SR* = √V · ((1 − γ) Φ⁻¹(1 − 1/N) + γ Φ⁻¹(1 − 1/(N e)))`, γ = 0.5772 (Euler-Mascheroni),
   N trials, V the variance of the trials' Sharpe ratios; `DSR = PSR(SR*)`.
6. **PBO** by CSCV (Bailey, Borwein, López de Prado, Zhu 2017): S = 16 blocks *(default)*,
   every split into S/2 in-sample blocks; the in-sample best configuration's out-of-sample
   relative rank ω, logit λ = ln(ω / (1 − ω)); PBO = share of splits with λ ≤ 0.

### Studies

7. A study is `research/studies/<id>/study.yaml` (title, hypothesis, as_of, parameters,
   seed) and `study.py` with `run(ctx) -> dict`. `qp research run ID` executes it and writes
   `data/lake/results/studies/<id>/<UTC timestamp>/result.json` and `provenance.json`
   (git commit, dirty flag, config fingerprint, each lake dataset's max `known_time`,
   parameters, seed, Python and package versions). Results are never overwritten. Every run
   is kept, so trial counts for the deflated Sharpe ratio can be audited.

### Fundamentals

8. **SEC EDGAR XBRL company facts** (`data.sec.gov/api/xbrl/companyfacts/CIK##########.json`):
   one lake row per fact, keys (cik, taxonomy, concept, unit, start), valid date = period
   end, values (val, accn, form, fy, fp). `known_time` = end of the `filed` day in
   America/New_York (the API gives only the date; never earlier than the filing), basis
   `source`. A later filing with another value is a revision. SEC fair access: a declared
   User-Agent with a contact (`QP_SOURCES__SEC__USER_AGENT`, owner-set; the connector refuses
   without it), at most 5 requests/s *(default; SEC allows 10)*.
9. **TTM** of a flow concept as of `t`: `FY + YTD_current − YTD_prior_year` when a 10-Q newer
   than the last 10-K is known, else the last FY value; instants (balance sheet) take the
   latest value. Only facts filed by `t` are used.
10. **KAP**: not built (ADR 0012).

### Valuation and factors

11. **DCF**: explicit FCFF forecasts discounted at WACC (end of year), Gordon terminal value
   `FCFF_{N+1} / (WACC − g)` with `g < WACC` enforced; equity = EV − net debt.
   **WACC** = E/V·(rf + β·ERP) + D/V·kd·(1 − t). **Reverse DCF**: the constant growth over N
   years that makes the DCF equal the market EV (brentq). **Multiples**: P/E, P/B,
   EV/EBITDA, EV/Sales, FCF yield from TTM / latest values; missing inputs → unavailable.
12. **Factor regression**: OLS of excess returns on factor returns with HAC t-statistics;
   **construction**: per date, sort by a characteristic into quantiles, factor return = top
   minus bottom equal-weighted return (characteristic known at the start of the period).

### Decisions journal

13. Schema `journal` (migration 0006), append-only: `theses` (title, instrument, thesis,
   invalidation criteria, created_at, first review date), `entries` (notes and decisions,
   optionally on a thesis), `reviews` (verdict keep / close / invalidated, next review date,
   note), `watchlists` (name, instrument, note, added_at). A thesis's status and next review
   date come from its latest review. Review-due alerts are composed in `qp.reporting`
   (decisions and alerts are siblings and must not import each other).
