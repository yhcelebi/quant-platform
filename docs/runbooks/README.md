# Runbooks

Operational procedures: `ledger-import.md`, `data.md`, `performance-and-risk.md`, `macro-and-news.md`, `research.md`. Phase 7 adds: starting/stopping the trader, kill switch (halt vs
flatten), heartbeat alerts, reconciliation mismatches. Phase 8 adds: VPS deploy, backups
and restore test.

## Local database (phase 0)

```bash
make db-up      # start PostgreSQL 16 (127.0.0.1:5433), waits for healthy
make migrate    # apply migrations
make doctor     # verify
make db-down    # stop; the qp-pgdata volume keeps the data
```

A Homebrew PostgreSQL on 5432 does not conflict: qp uses 5433.
