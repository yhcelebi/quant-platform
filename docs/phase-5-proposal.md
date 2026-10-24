# Phase 5 proposal — research lab

Status: accepted 2026-10-25 under the owner's standing instruction ("go with your
recommendation and continue on other phases"); defaults in ADR 0013. Phases 3 and 4 still
wait for a CI run (nothing pushed yet).

## Goal

Make research reproducible and honest: tested statistics (including overfitting checks),
point-in-time fundamentals, valuation tools, factor regressions, and a decisions journal
that holds theses to their invalidation criteria.

## Exit criteria

1. `qp.stats`: OLS with HAC standard errors, ADF/KPSS, Engle-Granger cointegration,
   GARCH(1,1), stationary bootstrap, Sharpe, probabilistic and deflated Sharpe ratio, PBO
   (CSCV); each with hand-calculated or closed-form tests and a seed where random.
2. Study registry: `research/studies/<id>/study.yaml` + `study.py`; `qp research run ID`
   writes `data/lake/results/studies/<id>/<run>/` with results and provenance (git commit
   and dirty flag, config hash, lake snapshot, parameters, seed, package versions); a run
   from a dirty tree is labelled as such; a template in `research/templates/`.
3. SEC EDGAR XBRL company facts, point in time by filing date (fixture tests; a real run
   once the owner sets a contact User-Agent); trailing-twelve-month values; KAP not built.
4. Valuation: DCF (FCFF, Gordon terminal value), WACC (CAPM), reverse DCF (implied growth),
   multiples from point-in-time fundamentals and prices; hand-calculated tests.
5. Factors: time-series factor regression (alpha, betas, HAC t-stats); quantile long-short
   factor construction from a characteristic cross-section.
6. Decisions journal: theses (with invalidation criteria and review dates), notes, reviews,
   watchlists in the `journal` schema (migration 0006, append-only); review-due alerts.
7. CLI (`qp research`, `qp journal`, `qp fundamentals`, `qp value`) and Research and Journal
   pages; `make check` green in CI.

## Risks

- SEC needs a contact User-Agent (the owner's choice of email); no fundamentals for BIST
  without KAP or a vendor.
- Overfitting statistics are only as good as the honesty of the trial count passed to them;
  the study runner records every run so the count can be audited.
