# Aura Wave 1.2 — Event and Progression Intelligence Design

**Issue:** #30  
**Parent epic:** #28  
**Status:** Design activated  
**Scope:** Event envelope, taxonomy, migration plan, PostgreSQL CI, one emission service, and Reported Concerns as the first migrated domain

## 1. Delivery order

Wave 1.2 must proceed in this exact order:

1. define the canonical `VehicleEvent` contract;
2. define event taxonomy and actor/visibility rules;
3. design additive Alembic revisions;
4. add PostgreSQL migration CI;
5. build one event-emission service with idempotency;
6. migrate Reported Concerns as the first domain;
7. verify timeline reconstruction and evidence-backed progression;
8. only then consider consultations, assessments, treatment plans, DTCs, drivers, stewardship, health signals, and communication.

No pull request in this wave may move multiple domains onto the event layer at once.

## 2. Locked architecture decision

Aura will extend the existing `VehicleEvent` table. A parallel progression table is prohibited.

Domain models remain authoritative for current state. `VehicleEvent` becomes the append-oriented record of meaningful changes.

Existing event rows and legacy readers must remain compatible throughout Wave 1.2.

## 3. Canonical VehicleEvent contract

The target envelope must support:

- immutable event identity;
- vehicle scope;
- ownership scope where known;
- schema version;
- occurrence time and recording time;
- subject type and subject ID;
- event type;
- actor type, optional actor user ID, and actor authority;
- source and provenance;
- previous state and new state;
- progression direction;
- client/advisor visibility;
- correlation and causation IDs;
- evidence references;
- correction-of-event linkage;
- idempotency key and duplicate suppression;
- legacy compatibility fields during transition.

### Required fields for new canonical events

- `schema_version`
- `occurred_at`
- `subject_type`
- `event_type`
- `actor_type`
- `actor_authority`
- `visibility`
- `fingerprint`

### Conditionally required fields

- `subject_id` when the subject is a persisted domain row;
- `actor_user_id` for authenticated human actors;
- `previous_state` and `new_state` for transitions;
- `correction_of_event_id` for corrections;
- `ownership_id` when an active ownership relation exists.

### Legacy compatibility

The first migrations must preserve:

- `event_date`;
- `title`;
- `description`;
- `mileage`;
- `source`;
- `data`;
- `created_by`;
- `is_deleted`;
- `created_at`;
- `resolved_at`.

`mileage` must become nullable before non-service events are emitted. `created_by` remains temporarily for existing readers, but new system events must not invent a fake human actor.

## 4. Event taxonomy V1

Taxonomy must be controlled in code and versioned. Free-form event names are prohibited for canonical emitters.

### Reported Concern events

- `concern.reported`
- `concern.review_started`
- `concern.monitoring_started`
- `concern.resolved`
- `concern.reopened`
- `concern.corrected`

### Reserved future families

- `consultation.*`
- `assessment.*`
- `treatment_plan.*`
- `treatment_action.*`
- `dtc.*`
- `maintenance.*`
- `recall.*`
- `driver_observation.*`
- `stewardship.*`
- `health_signal.*`
- `conversation_summary.*`

Reserved families must not be emitted in the first domain implementation.

## 5. Actor authority rules

Allowed actor types:

- `user`
- `system`
- `provider`

Allowed actor authorities for V1:

- `owner`
- `driver`
- `advisor`
- `administrator`
- `system`
- `provider`

Authority must be derived from one canonical vehicle-access policy, not inferred independently by routes.

A user with a global `user` role is not automatically an owner for an arbitrary vehicle. Active ownership or assignment must be proven.

## 6. Visibility rules

Allowed visibility values:

- `client`
- `advisor`
- `internal`

Rules:

- client-visible events must contain calm, non-diagnostic language;
- advisor notes and internal reasoning must never be copied into client-visible payloads;
- raw prompts, secrets, provider tokens, and unrestricted request bodies are prohibited in event payloads;
- visibility must be explicit for every new canonical event.

## 7. Progression direction V1

Allowed values:

- `improving`
- `stable`
- `deteriorating`
- `recurring`
- `resolved`
- `insufficient_evidence`
- `not_applicable`

For the first Reported Concern emitter:

- creation defaults to `insufficient_evidence`;
- transition to monitoring is `stable` unless evidence proves otherwise;
- transition to resolved is `resolved`;
- a new concern linked to a prior resolved equivalent may be `recurring` only when the recurrence rule has deterministic evidence;
- no event may claim improvement or deterioration from text sentiment alone.

## 8. Additive Alembic migration set

### Revision A — Event envelope

Add nullable or server-defaulted columns:

- `schema_version`
- `occurred_at`
- `recorded_at`
- `subject_type`
- `subject_id`
- `actor_type`
- `actor_user_id`
- `actor_authority`
- `visibility`
- `previous_state`
- `new_state`
- `progression_direction`
- `correlation_id`
- `causation_id`
- `evidence_refs`
- `correction_of_event_id`

Relax `mileage` to nullable.

All JSON fields must use generic `sa.JSON()` or a deliberate PostgreSQL `JSONB` strategy covered by PostgreSQL tests. No new `sqlite.JSON()` declarations.

### Revision B — Deterministic legacy backfill

Backfill only facts that can be derived without guessing:

- `schema_version = 1`;
- `occurred_at` from `event_date`, otherwise `created_at`;
- `actor_user_id = created_by`;
- `actor_type = 'user'` when `created_by` exists;
- reviewed legacy subject classification;
- conservative visibility default.

Do not fabricate state transitions, evidence, authority, correlation, or progression.

### Revision C — Indexes and constraints

After successful dual-read/write operation:

- vehicle and occurrence-time index;
- subject index;
- correlation index;
- correction relationship index;
- controlled check constraints for actor type, authority, visibility, and progression;
- non-null enforcement only after production preflight passes.

## 9. PostgreSQL migration CI

Wave 1.2 implementation cannot merge with SQLite-only migration testing.

Required CI jobs:

1. fresh PostgreSQL database upgraded from base to head;
2. production-shaped PostgreSQL fixture stamped at `d42e7a1c9b50` and upgraded to the proposed head;
3. downgrade rehearsal limited to development verification only, with production policy still prohibiting destructive downgrade;
4. event constraints, partial indexes, JSON behaviour, and concurrent duplicate suppression tested on PostgreSQL.

## 10. Canonical event-emission service

One service must own event creation.

Responsibilities:

- validate taxonomy and schema version;
- resolve actor authority;
- require explicit visibility;
- generate deterministic fingerprints;
- suppress duplicates safely under concurrency;
- preserve source/provenance;
- validate payload against event family rules;
- write the event inside the caller's transaction;
- never commit independently unless explicitly designed as an outbox boundary;
- return the existing event on idempotent replay;
- emit privacy-safe structured logs;
- avoid swallowing database integrity errors.

Routes must not construct canonical `VehicleEvent` records directly after their domain is migrated.

## 11. First domain — Reported Concerns

The first implementation will cover only `CarFault`, Aura's Reported Concern model.

### Initial transitions

- create concern → `concern.reported`;
- begin advisor review → `concern.review_started`;
- begin monitoring → `concern.monitoring_started`;
- resolve concern → `concern.resolved`;
- reopen a resolved concern → `concern.reopened`;
- correct an event → additive `concern.corrected` referencing the original event.

### Required evidence

Each event must link to:

- `subject_type = 'reported_concern'`;
- `subject_id = CarFault.id`;
- vehicle ID;
- active ownership when applicable;
- actor and authority;
- previous and new state for transitions;
- source route/channel;
- explicit visibility.

### Transaction rule

The domain mutation and its canonical event must succeed or fail together. No concern state may change successfully while its event emission silently fails.

### Compatibility rule

Existing concern routes and UI remain functional. The first implementation adds event emission without renaming `CarFault` or changing its client-facing Reported Concern language.

## 12. Initial tests

- valid concern event creation;
- duplicate suppression under replay and concurrency;
- concern transition ordering;
- cross-vehicle isolation;
- owner/driver/advisor authority validation;
- client/advisor/internal visibility boundaries;
- recurrence abstains without sufficient evidence;
- correction is additive;
- domain mutation rolls back when event creation fails;
- timeline reconstruction returns ordered evidence;
- PostgreSQL upgrade from `d42e7a1c9b50` succeeds.

## 13. Non-goals for the first implementation

- predictive failures;
- machine-learning scoring;
- repair recommendations;
- emotional inference;
- voice or WhatsApp conversational expansion;
- migration of consultations, assessments, DTCs, drivers, stewardship, or health signals;
- broad UI redesign.

## 14. Pull-request boundaries

Recommended PR sequence:

1. design and ADR;
2. PostgreSQL migration CI;
3. additive event-envelope migration and model compatibility;
4. event-emission service and tests;
5. Reported Concern emitter integration;
6. timeline/progression reconstruction and minimum advisor review surface.

Every PR must be independently reviewable and reversible through compatible code rollback or feature-gate disablement.
