# ADR 0011 — OpenBB: not adopted (for now)

- Status: accepted (owner: "go with your recommendation", 2026-10-25)
- Date: 2026-10-25

## Context

The owner asked why the platform does not use OpenBB. OpenBB (PyPI `openbb` 5.0.0,
Apache-2.0) is a data-access layer over many providers, with a CLI, an MCP server and a
hosted UI (OpenBB Workspace). It could replace some connectors. Checked on 2026-10-25 in a
throwaway environment, never in the project:

1. `openbb==5.0.0` with our locked dependencies: **no solution** (`openbb-cli` needs
   `rich<15`; we lock `rich==15.0.0`).
2. `openbb-core 2.0.1` + `openbb-government-us 2.0.0` + `openbb-news 2.0.0` resolve, adding
   29 packages (FastAPI-based routers among them).
3. `openbb-government-us` covers fiscal data (debt to the penny, statements, auctions,
   spending); it has no Treasury yield-curve fetcher.
4. `openbb-news` is a registry of general RSS feeds (BBC, Axios, …) and can fetch full article
   bodies, which rule "metadata and own summaries only" (DOMAIN) forbids storing.
5. No provider covers TCMB, EVDS, KAP or TEFAS, and results carry no `known_time` (rule 5).

## Decision

Do not add OpenBB to the project. Phase 4 builds its own small connectors (RSS/Atom feeds of
official institutions, FRED release dates and Treasury constant maturities through the
existing FRED connector). Revisit for Phase 5 (SEC EDGAR fundamentals) if `openbb-sec`
saves real work; any use goes through a connector that stores raw payloads and
point-in-time columns like every other source, behind a new ADR.

## Consequences

- No dependency conflict and no extra web framework in the library.
- We write and maintain a few more connectors (each small, with fixture tests).
- `research/` notebooks may still use OpenBB in a separate environment for exploration.
