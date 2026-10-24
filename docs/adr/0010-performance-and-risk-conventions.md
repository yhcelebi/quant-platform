# ADR 0010 — Performance and risk conventions

- Status: accepted (owner "go" on Phase 3, 2026-10-25). Items marked *default* are the
  recommended choices; the owner can change them (docs/owner-questions.md, Phase 3).
- Date: 2026-10-25

## Context

`docs/DOMAIN.md` fixes the return formulas (TWR, XIRR, Fisher real return, annualization,
FX split). Applying them to a real ledger needs more decisions: which days are valued, when
a flow counts, what is external, how a monthly price index applies to a daily series, and
how VaR is read off a sample. Without written rules two correct-looking implementations
give different numbers.

## Decision

### Daily history (`qp.performance.history`)

1. **Scope** is a set of accounts (default: all). The scope value `V_d` in TRY is the sum of
   its positions (quantity × latest price on or before `d` × latest TCMB rate on or before
   `d`), its cash balances (× rate) and assets in transit from one of its accounts (they
   belong to the sending account until received). Prices and rates are those known at
   `known_at` (default: everything recorded).
2. **Valuation days** *(default)*: Monday to Friday, plus any other day with an external
   flow. Holidays are valued at the previous prices (a zero-return day).
3. **External flows** `F_d`: deposits, withdrawals, opening balances (their market value on
   the day) and transfers with exactly one side in the scope (market value on the
   `transfer_in` date). Foreign-currency flows are converted at the day's TCMB rate.
   Everything else (trades, fees, taxes, interest, dividends, FX conversions, corporate
   actions, transfers inside the scope) is internal and part of the return.
4. **Flows at end of day** *(default)*: `1 + r_d = (V_d − F_d) / V_prev`, where `V_prev` is
   the previous valuation day's value. Chaining daily returns is the DOMAIN TWR (flow-free
   days telescope). A day whose `V_prev ≤ 0` has no return (no capital at risk) and starts a
   new sub-period. Reference case e is reproduced exactly by this rule.
5. **Never estimated**: if any holding in the scope lacks a price or rate on a day, that day
   is incomplete and every figure over a window containing it is unavailable, with the
   missing items named. Prices older than `stale_after_days` are used but counted as stale.

### Views and metrics (`qp.performance.metrics`)

6. **USD view**: `V_d / U_d` and `F_d / U_d` with `U_d` the TCMB USD/TRY rate of the day.
7. **Real TRY view**: values and flows are deflated by `I(s) / I(d)`; the real TWR then equals
   `(1 + TWR) / (I(e) / I(s)) − 1` exactly (Fisher, DOMAIN). `I(d)` *(default)*: a monthly
   index level `L_m` is the level at the end of month `m`; inside month `m`,
   `I(d) = L_{m−1} × (L_m / L_{m−1})^(day / days_in_month)` (geometric interpolation). A
   month whose level is not known at `known_at` makes `I(d)` unavailable for that month, so
   real figures run to the end of the last published month.
8. **XIRR over [s, e]**: `−V_s` at `s`, `−F_d` for flows after `s`, `+V_e` at `e`.
9. **Annualization**: DOMAIN; windows under 365 days are not annualized. Volatility is the
   sample standard deviation (ddof 1) of daily returns × √252.
10. **Drawdowns** come from the TWR wealth index `W_d = ∏(1 + r)`, never from raw values
   (flows would look like gains and losses). Drawdown `W_d / max_{t ≤ d} W_t − 1`; recovery
   is the first day the previous peak is reached again.
11. **Benchmarks** are growth indices over the same valuation days: `fx` (holding a foreign
   currency; TCMB rate), `price` (an instrument's price × its currency's rate) and `rate`
   (a deposit rate: `B_d = B_prev × (1 + r × days / 365)`, `r` the latest rate known on the
   previous day, simple annual, as a fraction). A benchmark without data is unavailable.

### Attribution (`qp.performance.attribution`)

12. P&L over (s, e] is split into buckets: one per instrument, one per cash currency and one
   for fees, interest and taxes not tied to an instrument.
   `P&L_b = V_e,b − V_s,b − inflow_b`. Every movement is booked as an inflow to one bucket
   and an equal outflow from another (a buy moves its cash amount from cash to the
   instrument; a dividend from the instrument to cash; a fee from "fees, interest, tax" to
   cash; a merger moves the new shares' value from the old instrument to the new one). Cash
   legs in the transaction's main currency use its trade-time rate, other legs the day's
   rate. External flows are inflows to the receiving bucket. So the buckets sum *exactly*
   (Decimal) to `V_e − V_s − ΣF`; a test enforces it.
13. Instrument FX/price split over the window: local P&L = the same formula in the
   instrument currency; price effect = local P&L × `X_e`; FX effect = TRY P&L − price
   effect. For a single purchase this is the DOMAIN split.

### Risk (`qp.risk`)

14. **VaR/ES**: one-day horizon, loss positive, levels 95% and 99%. Historical:
   `VaR_α` = the ⌈α·n⌉-th smallest of the `n` losses (empirical quantile, no interpolation);
   `ES_α` = the mean of the losses `≥ VaR_α`. Parametric (normal):
   `VaR = (z_α σ − μ) V`, `ES = (σ φ(z_α) / (1 − α) − μ) V`, with `μ`, `σ` the mean and
   sample standard deviation.
15. **Two VaR sources**: the portfolio's own daily TWR returns × today's value, and a
   position-level historical simulation (today's holdings × each instrument's daily TRY
   returns, price × rate; foreign cash uses rate returns). Lookback 250 valuation days
   *(default)*. Scenario days are the valuation days; on a day with a fresh observation
   (a price, and for foreign assets a rate, dated that day) an asset's return is its move
   since its previous fresh observation, on other days zero (no new information, the move
   lands on the next fresh day). An asset with fewer than 60 fresh returns *(default)* in
   the lookback is reported and left out: sparse (e.g. monthly) prices would put a month's
   move into a one-day scenario. Holdings left out are named with their weight.
16. **Exposure** is measured as weights of the scope value: by currency (instrument currency
   for positions), asset class (instrument master; `unknown` if missing), account and
   instrument, plus the Herfindahl index `Σ w²` for concentration.

### Alerts (`qp.alerts`)

17. Rules run after the evening TCMB fetch on the latest valuation day: daily loss beyond a
   threshold, drawdown beyond a threshold, a daily loss larger than the previous day's 99%
   historical VaR, and stale or missing prices. Thresholds are config. Each event is stored
   once per (rule, subject, day) in `ops.alert_events`, then sent (Telegram, or the log).

## Consequences

- Every figure can be recomputed from the ledger, the lake and this ADR; a figure that
  cannot be computed says why.
- Weekday valuation slightly lowers measured volatility on holidays (zero-return days).
- Attribution uses the day's rate for cash movements without a trade rate, so its cash
  buckets show the gap between broker and TCMB rates as FX P&L.
- Real figures lag by about a month (TÜFE publication).
