# qp — personal quant platform

One place for wealth accounting, market/macro/news intelligence, quantitative research and
trading. Single user. Python 3.12 modular monolith.

Start here: [AGENTS.md](AGENTS.md) · [Architecture](docs/ARCHITECTURE.md) ·
[Domain conventions](docs/DOMAIN.md) · [Roadmap & status](docs/ROADMAP.md) · [ADRs](docs/adr/)

## Quick start

Requires [uv](https://docs.astral.sh/uv/) and Docker.

```bash
make setup        # deps, pre-commit hooks, .env from .env.example (edit QP_DB__PASSWORD)
make db-up        # PostgreSQL 16 on 127.0.0.1:5433
make migrate      # Alembic: create schemas
make doctor       # health check
make check        # lint, types, import contracts, tests
make terminal     # Streamlit UI
```

Other targets: `make help`. Importing your ledger: [runbook](docs/runbooks/ledger-import.md)
and the [CSV format](docs/ledger-csv-format.md). Market and macro data (TCMB FX, EVDS, FRED):
[data runbook](docs/runbooks/data.md).

## Safety

Default mode is paper. A live order path needs config `mode: live`, the process env
`QP_LIVE_TRADING=I_UNDERSTAND_THE_RISK` and an `approved_live` promotion record — and every
order still passes the pre-trade risk gate and the kill switch. Secrets live only in env or
`.env`. `tests/reference/` is owner-owned.
