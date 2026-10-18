# ADR 0002 — Tooling and dependency management

- Status: accepted
- Date: 2026-10-18

## Context

Phase 0 needs reproducible installs, fast checks, and machine enforcement of the library
boundaries in rule 7 for code that import-linter cannot analyse (`apps/`, `research/`,
notebooks, `scripts/`).

## Decision

1. **uv, one lockfile.** `pyproject.toml` declares the runtime dependencies and three groups:
   `dev` (default), `research` (vectorbt, cvxpy, riskfolio-lib, jupyterlab) and `trading`
   (nautilus_trader, ccxt). All groups resolve into one `uv.lock`. On 2026-10-18 the joint
   resolution succeeded on Python 3.12 with pandas 3.0.6, numpy 2.5.4, nautilus_trader
   1.231.0, vectorbt 1.1.2, riskfolio-lib 7.4.0 and cvxpy 1.9.3. Runtime compatibility is
   checked by `make deps-smoke` (`scripts/deps_smoke.py`), which runs in CI as its own job.
   If a future upgrade makes the groups conflict, use uv's `conflicts` setting and record a
   new ADR; do not silently drop a group.
2. **Packaging.** hatchling builds one wheel containing `qp` (src layout) and `apps`, so the
   `qp` console script (`apps.cli.main:main`) works from an editable install.
3. **Checks (`make check`).** `uv lock --check`, `ruff check`, `ruff format --check`, `mypy`,
   `lint-imports`, `pytest`. CI runs the same target with a PostgreSQL 16 service and
   `QP_REQUIRE_DB=1`, so integration tests fail rather than skip there.
4. **mypy.** Checks `src/qp`, `apps`, `tests`, `db/migrations`. Strict on `qp.core`,
   `qp.platform`, `qp.ledger` and `qp.risk` (the latter two pre-configured for later phases).
5. **ruff rules.** Includes `DTZ` (naive datetimes, rule 4), `S` (security), `T20` (no
   print in the library) and `TID251` (banned imports).
6. **Library boundaries outside `qp`.** ruff's `flake8-tidy-imports.banned-api`. A nested
   `ruff.toml` *replaces* (does not merge) the parent's banned list, verified with probes,
   so each directory states its full list:

   | Directory   | Banned                                         |
   |-------------|------------------------------------------------|
   | root (scripts/, db/, …) | nautilus_trader, vectorbt, streamlit, typer |
   | `src/`      | vectorbt, streamlit, typer (nautilus scoped by import-linter) |
   | `apps/`     | nautilus_trader, vectorbt                      |
   | `research/` | nautilus_trader, streamlit, typer (notebooks linted too) |
   | `tests/`    | vectorbt (revisit for phase 6 parity tests, by ADR) |

   `scripts/deps_smoke.py` is exempt from TID251 by an explicit per-file ignore: it checks
   installation, it is not application code.
7. **pre-commit.** File hygiene, private-key detection, ruff, `uv lock --check`, and a hook
   that refuses commits touching `tests/reference/` unless the owner sets `QP_OWNER_EDIT=1`.
8. **Local PostgreSQL on port 5433.** The owner's machine already runs a Homebrew
   PostgreSQL 15 on 5432. qp's PostgreSQL 16 container binds `127.0.0.1:5433`; CI uses the
   same port so configuration is identical.

## Consequences

- One `uv sync --all-groups` gives a full research + trading environment.
- Heavy trading/research packages are not needed for `make check`; CI keeps them in a
  separate job so the main check stays fast.
- Directory-level ban lists must be kept in sync by hand when a new boundary is added.
