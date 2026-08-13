# Aura Wave 1.2 — Canonical Event Audit and Observability Plan

**Issue:** #30  
**Scope:** Canonical `VehicleEvent` emission, Reported Concern integration, progression reconstruction, production migration and operational review

## 1. Purpose

Aura's progression layer must be explainable not only to clients and advisors, but also operationally. Every durable progression statement should be traceable to canonical events without exposing raw prompts, secrets, unrestricted request bodies, or private advisor reasoning in logs.

The observability model therefore treats `VehicleEvent` as the append-oriented canonical audit ledger for progression, while application logs and database checks monitor the health of that ledger.

## 2. Canonical audit ownership

### Domain state

Domain models remain authoritative for current state. For Wave 1.2 the first migrated domain is `CarFault` (client-facing: Reported Concern).

### Progression history

`VehicleEvent` is authoritative for meaningful progression history and provenance.

Canonical events are additive. Existing canonical rows must not be edited or deleted to "fix" history. A correction is represented by a later `concern.corrected` event with `correction_of_event_id` pointing to the original event.

### Legacy `EventAuditLog`

`EventAuditLog` remains a compatibility audit record for legacy event edit/delete flows. It is **not** the canonical progression writer and should not be expanded into a parallel event architecture.

For canonical Wave 1.2 events, creation and correction provenance is carried by the event ledger itself:

- immutable event ID;
- `recorded_at` append chronology;
- `occurred_at` factual occurrence time;
- actor ID and authority;
- source/provenance;
- subject identity;
- previous/new state;
- visibility;
- evidence references;
- correction linkage;
- deterministic fingerprint/idempotency identity.

## 3. Structured application logging

Canonical event logs may contain only operational metadata necessary to trace one event write:

- event ID;
- vehicle ID;
- event type;
- subject type and subject ID;
- schema version;
- actor authority;
- visibility;
- correlation ID when present;
- causation ID when present;
- source identifier;
- operation outcome.

Logs must not contain:

- event `description`;
- arbitrary `data` payload contents;
- raw evidence contents;
- advisor note text;
- conversation text;
- model prompts or completions;
- passwords, API keys, tokens, authorization headers, cookies, session IDs or encrypted profile values.

## 4. Operational outcomes to distinguish

The event layer should make these outcomes distinguishable in logs/metrics:

1. `event_emitted` — a new canonical row was flushed successfully;
2. `idempotent_replay` — a semantically identical event already existed and was returned;
3. `idempotency_conflict` — the same deterministic key was reused with different semantics;
4. `authority_rejected` — the actor had no proven authority for the vehicle;
5. `payload_rejected` — payload privacy/size validation failed;
6. `transition_rejected` — a domain transition could not be represented by the approved taxonomy;
7. `event_write_failed` — a database failure prevented canonical emission;
8. `progression_abstained` — evidence was insufficient or domain/timeline state did not reconcile;
9. `correction_recorded` — an additive correction was linked to a prior event.

Wave 1.2 does not require a third-party metrics platform before release. Structured application logs and PostgreSQL verification are the baseline; the outcome names above form the contract for later counters/dashboards.

## 5. Database invariants to monitor

### Canonical vocabulary

Production PostgreSQL CHECK constraints enforce approved non-NULL values for:

- `actor_type`;
- `actor_authority`;
- `visibility`;
- `progression_direction`;
- positive `schema_version`.

Legacy rows remain compatible because those envelope fields may remain NULL where historical truth cannot be derived safely.

### Idempotency

`uq_vehicle_event_fingerprint` remains the database concurrency boundary for duplicate canonical event creation.

### Timeline query path

`ix_vehicle_events_timeline_scope` supports concern timeline reconstruction across:

- vehicle;
- subject type;
- subject ID;
- recorded chronology;
- deterministic event ID tie-break.

### Ownership ambiguity

The canonical writer refuses to emit when a vehicle has zero or multiple active ownership rows. This is intentionally fail-closed because ownership scope is part of event provenance.

## 6. Production preflight before schema hardening

Revision `f24c8d1e6a90` performs its own fail-closed vocabulary preflight before creating PostgreSQL CHECK constraints. It does not rewrite invalid values.

Operationally, the following should also be reviewed during deployment or incident investigation:

```sql
SELECT fingerprint, COUNT(*)
FROM vehicle_events
GROUP BY fingerprint
HAVING COUNT(*) > 1;
```

Expected: zero rows because the existing unique constraint must already prevent duplicates.

```sql
SELECT car_id, COUNT(*)
FROM car_ownership
WHERE is_active = TRUE
GROUP BY car_id
HAVING COUNT(*) > 1;
```

Expected for canonical emission readiness: zero rows for vehicles that will emit new events. Existing ambiguous stewardship must be corrected before canonical writes are enabled for that vehicle.

```sql
SELECT id, event_type, subject_type, subject_id
FROM vehicle_events
WHERE event_type LIKE 'concern.%'
  AND (
    schema_version IS NULL
    OR occurred_at IS NULL
    OR subject_type IS NULL
    OR subject_id IS NULL
    OR actor_type IS NULL
    OR actor_authority IS NULL
    OR visibility IS NULL
    OR progression_direction IS NULL
  );
```

Expected for newly emitted Reported Concern canonical events: zero rows. Legacy non-canonical event families are intentionally outside this assertion.

## 7. Progression reconciliation monitoring

The progression service compares the latest effective canonical `new_state` with authoritative `CarFault.status`.

A mismatch is not automatically repaired and must not be hidden. Aura returns `insufficient_evidence` and records an abstention condition rather than fabricating missing history.

Repeated mismatches for the same vehicle/subject should be treated as a data-integrity signal requiring advisor/engineering review.

## 8. Visibility monitoring

Client-safe reconstruction must only consume `visibility = 'client'` events.

Advisor reconstruction may consume `client`, `advisor`, and `internal` events.

A correction that is not visible to a client must not expose its contents or existence through the client timeline payload. The client-safe response never serializes raw event payloads.

Any test or production observation where advisor/internal event content appears in a client-safe timeline is a release-blocking privacy defect.

## 9. Incident response boundaries

### Duplicate/conflicting event

- do not delete the canonical row;
- identify the idempotency key/fingerprint source;
- correct the emitting integration;
- use an additive correction only when the recorded event itself requires correction.

### Domain state changed without event

- do not invent historical occurrence time or actor authority;
- preserve the authoritative domain state;
- progression service should abstain;
- investigate the failed transaction/integration path;
- add only facts that can be supported by durable evidence.

### Incorrect client visibility

- treat as privacy-sensitive;
- stop the affected summary path if necessary;
- do not copy advisor/internal payloads into replacement client events;
- correct visibility/history additively with reviewed provenance.

## 10. Release gate for Issue #30

Issue #30 can close only when all of the following are true:

- design/ADR is merged;
- canonical taxonomy/versioning policy is merged;
- additive envelope migration and deterministic backfill are merged;
- PostgreSQL constraints/indexes pass fresh and production-shaped upgrade CI;
- canonical event emission passes replay/concurrency/transaction tests;
- Reported Concern integration passes atomicity tests;
- timeline reconstruction passes cross-vehicle and visibility tests;
- recurrence requires explicit evidence and otherwise abstains;
- client-safe and advisor summary policies are tested;
- audit/observability policy is documented;
- no second domain is migrated as part of this closure gate.

Only after this gate should Aura consider the next event-source domain.
