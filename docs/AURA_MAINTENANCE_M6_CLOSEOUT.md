# Aura Maintenance Intelligence — M6 Production Closeout

Parent: #144  
Issue: #155

## Production authority after M6

Maintenance timing is authoritative only when it is produced by the Maintenance Intelligence chain:

```text
verified vehicle identity
+ verified current odometer
+ advisor-verified maintenance knowledge
+ advisor-normalized matching service history
→ deterministic upcoming / due / overdue / unknown
→ governed maintenance_monitoring care signal
```

The former generic `SERVICE_INTERVAL_KM = 12_000` health shortcut is removed from production semantics. A service row at an old mileage can no longer lower the health score or create overdue maintenance language merely because more than 12,000 km has elapsed.

No universal interval replaces it. Missing evidence remains an abstention/`unknown` condition.

## Runtime fail-safe

Environment variable:

```text
AURA_MAINTENANCE_INTELLIGENCE_ENABLED
```

Normal production behavior is enabled when the variable is unset or explicitly set to a supported true value (`1`, `true`, `yes`, `on`, `enabled`).

To stop new Maintenance Intelligence behavior during an incident, set:

```text
AURA_MAINTENANCE_INTELLIGENCE_ENABLED=false
```

An invalid explicit value also fails closed.

When disabled:

- no maintenance-state reevaluation is performed by the reevaluation hooks;
- no automatic `maintenance_monitoring` signal is raised or resolved;
- owner/advisor Maintenance Intelligence surfaces show a bounded unavailable state rather than stale/guessed maintenance timing;
- verified knowledge, odometer evidence, service history, classifications, existing `VehicleHealthAlert` rows and canonical `care_signal.*` history remain intact;
- the legacy 12,000 km shortcut does **not** reactivate.

This is an operational kill switch, not a data rollback. Existing active maintenance signals remain durable records for advisor handling rather than being silently rewritten when the runtime is disabled.

To restore normal behavior, set the variable to `true` (or remove it) and redeploy. The next accepted evidence change/manual evaluation can then use the same durable verified facts to recompute typed state.

## Database and deploy contract

M6 adds no new database schema. Production must remain upgradeable from a fresh PostgreSQL database to the current Alembic head and downgrade rehearsal must continue to pass according to the existing migration safety contract.

Normal Railway startup remains:

```text
flask db upgrade && gunicorn app:app
```

Health check remains:

```text
/healthz
```

## Closeout invariants

Before closing #144, verify:

- Maintenance Intelligence CI passes, including M2–M6 knowledge/state/classification/runtime tests;
- PostgreSQL migration CI passes;
- Care Signals PostgreSQL CI passes with lifecycle idempotency intact;
- Security CI and role/authority tests pass;
- service-history and mileage suites pass without generic interval assumptions;
- cross-vehicle isolation remains covered by the deterministic state-engine suite;
- Railway production deploy reports the merged commit, PostgreSQL runtime, successful Gunicorn boot and successful `/healthz` check;
- no synthetic OEM schedule or production service history is created for smoke testing.

## Rollback rule

If a production defect appears in maintenance evaluation or presentation, prefer the runtime fail-safe above. Do not restore the generic 12,000 km path as a rollback. A code rollback may revert application code to the last known-good commit only after confirming that doing so will not reintroduce generic maintenance timing as authoritative production behavior.
