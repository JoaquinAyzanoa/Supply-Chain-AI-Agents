# Migrations

Plain SQL files applied in order by `sc_core.infra.migrate`
(`python -m sc_core.infra.migrate`, or a service startup hook).

- Name: `NNN_snake_case.sql`, versions strictly increasing, never reuse or edit
  an applied file; add a new one.
- Each file runs in one transaction with its bookkeeping row in
  `schema_migrations`.
- Keep files idempotent where cheap (`CREATE TABLE IF NOT EXISTS`) so a
  hand-applied change does not break the runner.

Planned: `001_mail_token_cache.sql` (phase 2), `002_mail_sync.sql` (phase 4),
`003_event_result.sql` (phase 5), `004_cases.sql` (phase 6),
`005_planning.sql` (phase 7), `006_ui.sql` (phase 8), `007_performance.sql` (phase 9).
