# ADR 0003 — Configuration precedence and secrets

- Status: accepted
- Date: 2026-10-18

## Context

The founding prompt lists the layering as "YAML base -> env -> mode". Read literally as a
precedence order, the mode YAML would override environment variables, so a secret or a
deliberate override in the environment could be silently replaced by a checked-in file.

## Decision

1. Precedence, lowest to highest: `configs/base.yaml` < `configs/<mode>/*.yaml` <
   `.env` < process environment. Environment variables use the `QP_` prefix and `__` for
   nesting (`QP_DB__HOST`).
2. The mode is chosen by: explicit argument, then `QP_MODE` in the process environment,
   then `mode` in base.yaml, else `paper`. A mode overlay may not change the mode, and a
   `QP_MODE` in `.env` that disagrees with the selected mode is an error.
3. Secret fields (`SecretStr`: DB password, Telegram bot token) are rejected if present in
   any YAML file. They come only from env or `.env` (rule 10). They are excluded from the
   settings fingerprint (the config hash used for result provenance) and are scrubbed from
   logs by key, by type and by value.
4. `QP_LIVE_TRADING` is not a settings field. It is read only from the process environment
   by `qp.platform.live_trading_env_acknowledged()`; finding it in `.env` is a ConfigError.
   It is one of three conditions for a live order path and never sufficient alone.
5. Unknown keys in YAML or `.env` are errors (`extra="forbid"`), so typos fail loudly.

## Consequences

- An operator can always override any non-secret setting from the environment.
- Turning on live trading requires a deliberate act in the shell of the trader process,
  not an edit to a file that persists across sessions.
