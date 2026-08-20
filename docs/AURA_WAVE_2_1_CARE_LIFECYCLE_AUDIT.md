# Aura Wave 2.1 — Care Lifecycle Architecture Audit

**Parent epic:** #74  
**Delivery issue:** #75  
**Related backlog:** #3  
**Canonical event foundation:** #30 / `docs/AURA_WAVE_1_2_EVENT_PROGRESSION_DESIGN.md`  
**Status:** Architecture/audit only — no runtime behaviour changes

## 1. Objective

Wave 2.1 maps Aura's existing care lifecycle before any new state-machine implementation is approved.

The audit answers four questions:

1. which models and routes currently own care state;
2. where state changes happen today;
3. which existing concepts must be preserved rather than rebuilt;
4. which transitions are safe to move behind explicit domain services and canonical `VehicleEvent` emission in Wave 2.2+.

This document records current behaviour. The companion state/event contract records the approved forward architecture.

## 2. Architectural baseline

Aura's master architecture assigns consultations, assessments, treatment plans, evidence, monitoring and priority queues to **Domain E — Advisor Operations**.

Wave 1.2 locked these rules:

- domain models remain authoritative for current state;
- `VehicleEvent` is the append-oriented record of meaningful changes;
- canonical event names are controlled and versioned;
- routes must not construct canonical events directly after a domain is migrated;
- domain mutation and event emission must succeed or fail together;
- consultation, assessment, treatment-plan, treatment-action and driver-observation families were intentionally reserved for later migration.

Wave 2.1 must extend those contracts. It must not create a parallel event ledger.

## 3. Current model inventory

### 3.1 `Consultation`

Current persisted fields relevant to lifecycle:

- `car_id`
- `ownership_id`
- `advisor_id`
- `client_id`
- `status`
- `scheduled_for`
- `started_at`
- `completed_at`
- internal `summary`
- `client_visible_summary`
- `notes`
- `created_at`

Current model status comment and active UI use:

- `scheduled`
- `in_progress`
- `completed`

Current helper semantics:

- `is_active()` means `status == "in_progress"`;
- `is_completed()` means `status == "completed"`.

### 3.2 `VehicleAssessment`

Current lifecycle fields:

- one assessment per consultation (`consultation_id` unique);
- `car_id`;
- `advisor_id`;
- `finalized_by`;
- `status`;
- `is_finalized`;
- `created_at`;
- `finalized_at`.

Current statuses:

- `draft`
- `finalized`

Assessment content includes frozen vehicle identity/mileage context, system-status fields, risk rows, treatment options, cost/consequence analysis and professional recommendation.

### 3.3 `TreatmentPlan`

Current fields:

- `car_id`
- optional `consultation_id`
- optional `assessment_id`
- `advisor_id`
- `title`
- `internal_instructions`
- `client_summary`
- `status`
- `created_at`
- `updated_at`

Current model default:

- `status="approved"`

Observed active route values:

- `approved`
- `in_progress`
- `completed`
- `deferred`

There is no observed first-class treatment-action model in `models.py`.

### 3.4 `DriverCheckIn`

Current durable observations:

- `car_id`
- `driver_id`
- tyre warning
- low fuel
- dashboard light
- vibration
- unusual sound
- free-text notes
- `created_at`

The row is operational evidence but is not currently represented as a canonical event family.

### 3.5 Priority/care pathway

Current commercial/care-pathway state is stored on `CarOwnership`:

- legacy `priority_access` boolean;
- `care_plan` string with current helper values:
  - `active_monitoring`
  - `preventive_coverage`
  - `priority_access`.

No dedicated priority-request lifecycle model was observed in `models.py`.

## 4. Current route and service catalogue

## 4.1 Client consultation request

`cars.routes.book_consultation`

Current behaviour:

- owner-only vehicle scope is enforced through active `CarOwnership` lookup;
- GET creates a `BookingIntent` when no unfinished intent exists;
- POST creates a `Consultation` immediately with `status="scheduled"`;
- `advisor_id` is left `None` for later assignment;
- the route commits the consultation before notification side effects;
- the route separately updates `BookingIntent` and commits again;
- the unfinished-intent completion block appears twice;
- WhatsApp confirmation/admin notification runs after the consultation commit and failures are swallowed to a console print.

Architecture finding:

`scheduled` currently represents both **client request** and **advisor-scheduled appointment**. Requested versus advisor-confirmed state is therefore not distinguishable from the persisted consultation row.

## 4.2 Advisor scheduling

`admin.routes.admin_schedule_consultation`

Current behaviour:

- advisor/admin route;
- requires active ownership;
- creates a `Consultation` directly as `scheduled`;
- assigns `advisor_id=current_user.id`;
- commits in the route.

Architecture finding:

Client request and advisor-created schedule converge on the same state even though they represent different authority decisions.

## 4.3 Start consultation

`admin.routes.admin_start_consultation`

Current behaviour:

- only `scheduled` may start;
- directly mutates `status` to `in_progress`;
- sets `started_at`;
- route owns transaction/rollback.

No canonical `consultation.*` event is emitted.

## 4.4 Complete consultation

`admin.routes.admin_complete_consultation`

Current behaviour:

- requires `in_progress`;
- requires a linked `VehicleAssessment`;
- requires assessment finalization (`is_finalized` or status `finalized`);
- directly sets consultation `completed` and `completed_at`;
- writes internal and client-visible summaries;
- route owns transaction/rollback.

No canonical `consultation.completed` event is emitted.

## 4.5 Consultation queue and guard

`admin.routes.admin_consultations` groups rows by:

- `scheduled`
- `in_progress`
- `completed`

Unknown status values are currently quarantined into the `scheduled` UI group.

`services.consultation_guard.get_active_consultation` and `require_active_consultation` treat `in_progress` as the sole active-care unlock.

Architecture finding:

The guard is a useful canonical policy primitive and should remain. State mutation itself should move to a lifecycle service.

## 4.6 Start/edit assessment

`admin.routes.admin_start_assessment`

- requires consultation `in_progress`;
- returns existing draft if present;
- refuses to recreate a finalized assessment;
- creates one new `draft` assessment otherwise;
- commits directly in the route.

`admin.routes.admin_edit_assessment`

- hard-fails unless status is `draft`;
- rewrites child risk and treatment-option rows on save;
- updates system-status fields and professional recommendation;
- commits directly in the route.

Architecture finding:

The current code already establishes a strong de-facto rule: finalized assessments are not edited through the ordinary edit route.

## 4.7 Finalize assessment

`admin.routes.admin_finalize_assessment`

Current behaviour:

- requires `draft`;
- validates the five system-status fields;
- sets:
  - `status="finalized"`
  - `is_finalized=True`
  - `finalized_at`
  - `finalized_by`;
- creates a `TreatmentPlan` in the same SQLAlchemy transaction;
- the new plan is created directly with `status="approved"`;
- commits once after both assessment mutation and plan insert.

Architecture finding:

Assessment finalization and treatment-plan creation are already transactionally coupled, which is valuable. The problem is that the route owns both domain decisions and the treatment plan bypasses a proposal/approval boundary.

## 4.8 Treatment-plan transitions

Current advisor routes:

- `/admin/treatment-plans/<id>/start`
- `/admin/treatment-plans/<id>/complete`
- `/admin/treatment-plans/<id>/defer`

Current behaviour:

- each route loads the plan;
- directly assigns the target status;
- commits immediately;
- no source-state validation is performed;
- no lifecycle timestamps or transition reasons are stored;
- no canonical treatment-plan event is emitted.

Observed assignments:

- any current state -> `in_progress`;
- any current state -> `completed`;
- any current state -> `deferred`.

Architecture risk:

Invalid transitions are possible because status change legality is not centralized.

## 4.9 Client treatment visibility

`cars.routes.car_detail` loads every `TreatmentPlan` for the owner's vehicle and passes full ORM rows to the shared template.

The model contains both `internal_instructions` and `client_summary`.

This audit does **not** assert a current disclosure bug without completing template-level verification, but Wave 2 implementation must preserve a strict rule: client rendering may use client-safe fields only; internal instructions remain advisor-only.

## 4.10 Priority scheduling

`cars.routes.request_priority_scheduling`

Current behaviour:

- feature-gated;
- creates a normal `Consultation` immediately as `scheduled`;
- `scheduled_for=datetime.utcnow()`;
- note records that it is a priority scheduling request;
- no dedicated priority-request row/state exists.

Architecture finding:

Priority-request intent is encoded as consultation notes rather than as a durable priority workflow.

## 4.11 Emergency review

`cars.routes.request_emergency_review`

Current behaviour:

- feature-gated;
- creates a `CarFault` / Reported Concern with category `emergency_review`;
- status begins as `reported`.

This is correctly represented as a concern rather than a diagnosis.

## 4.12 Driver issue reporting

`driver.routes.driver_report_issue`

Current behaviour:

- requires active driver assignment;
- creates a `CarFault` with `source="driver"`;
- commits normally.

Important existing protection:

`services.reported_concern_session_events` is registered at application startup and observes **all** `CarFault` creation/status transitions in the Aura SQLAlchemy session. It emits canonical concern events immediately before commit through `services.event_emission`.

Therefore driver concern reports already become `concern.reported` events transactionally. They do **not** need a duplicate `driver.*` concern event.

## 4.13 Driver daily check-in

`driver.routes.driver_daily_checkin`

Current behaviour:

- requires driver authority for the car;
- prevents a second same-day check-in at route level;
- creates `DriverCheckIn`;
- directly modifies `User.driver_score` based on warning flags;
- commits the check-in and score together;
- does not emit a canonical driver-observation event.

Architecture findings:

1. the check-in itself is a valid durable operational observation;
2. `driver_score` mixes gamification/scoring into the same transaction and conflicts with Aura's later non-gamified product direction;
3. Wave 2.4 should review score retention separately from event migration rather than carrying it into the canonical event contract.

## 4.14 Legacy treatment-record API

`events.routes` exposes `/treatments/...` endpoints that create, edit, archive and list `VehicleEvent` rows as generic treatment records.

Current characteristics:

- owner-created records are allowed;
- `event_type` is user-provided/free-form;
- rows use legacy `VehicleEvent` fields directly;
- updates rewrite the row and add `EventAuditLog` history;
- it does not use the canonical Wave 1.2 taxonomy/emitter.

Architecture finding:

This API is **legacy event-record functionality**, not the same thing as the `TreatmentPlan` lifecycle. Wave 2 must not merge these concepts by name. It requires compatibility classification before any treatment-action migration.

## 5. Canonical event foundation already in production

`services.event_emission` currently owns canonical event creation.

Current supported families:

- `concern.*`
- `evidence.*`

The service already provides:

- controlled taxonomy;
- subject-type rules;
- progression-direction validation;
- explicit visibility;
- active-ownership resolution;
- actor authority resolution through `security.access.resolve_vehicle_authority`;
- deterministic fingerprinting;
- idempotent replay protection;
- PostgreSQL concurrent duplicate protection;
- sensitive-key rejection for JSON payloads;
- caller-owned transaction semantics (`flush`, never independent `commit`).

Wave 2 should extend this service rather than create a second emitter.

## 6. Current authority model

`security.access.resolve_vehicle_authority` derives vehicle authority from persisted relationships:

- active ownership -> `owner`;
- active driver assignment -> `driver`;
- current admin identity -> advisor authority;
- otherwise no authority.

The current application still represents advisor authority through the `admin` role in this helper.

Wave 2 state services must use this canonical access/authority layer. They must not infer authority from route location alone.

## 7. Missing lifecycle capabilities confirmed by audit

### Consultation

Not observed as first-class transitions:

- requested state distinct from scheduled;
- advisor accept/confirm;
- cancel;
- reschedule event/history;
- defer;
- reopen;
- transition reason/audit metadata;
- canonical consultation event emission.

### Assessment

Not observed:

- canonical assessment events;
- additive correction record/event for a finalized assessment;
- explicit correction policy;
- service-owned start/finalize transitions.

### Treatment plan

Not observed:

- draft/proposal boundary;
- source-state validation;
- scheduling;
- monitoring state distinct from legacy `deferred`;
- escalation;
- cancellation;
- lifecycle timestamps/reasons;
- treatment-action model;
- canonical treatment-plan events.

### Priority

Not observed:

- dedicated priority request model;
- queue/status lifecycle;
- accept/defer/resolve transitions;
- priority request event subject.

### Driver operational history

Not observed:

- canonical `driver_observation.*` events for check-ins;
- advisor review/escalation linkage for check-ins.

## 8. Transaction and concurrency findings

### Existing strengths

- assessment finalization and auto-created treatment plan commit together;
- concern mutations and concern canonical events commit together via the registered session integration;
- `services.event_emission` already provides PostgreSQL-safe idempotent event creation.

### Current risks

- consultation and treatment-plan transitions are route-owned;
- treatment-plan transitions accept illegal source states;
- consultation booking uses multiple commits for one user operation;
- notification side effects are not modelled as downstream/outbox work;
- daily driver check-in uniqueness is checked in application code and should rely on/retain a database constraint for concurrency safety;
- generic legacy treatment records and canonical events share `VehicleEvent` storage but not the same semantic contract.

## 9. Backfill policy

Wave 2 must preserve Wave 1.2's rule: **do not invent historical facts.**

For existing consultation, assessment and treatment-plan rows:

- current persisted state may be retained;
- timestamps already present may be reused only for facts they directly prove;
- do not fabricate missing requested/approved/rescheduled/reopened transitions;
- do not invent actors for old transitions;
- do not synthesize canonical events merely to improve longitudinal-data counts;
- historical rows with insufficient transition provenance remain legacy state until a deterministic backfill rule is approved.

## 10. Compatibility/deprecation findings

Keep and extend:

- `Consultation`
- `VehicleAssessment`
- `TreatmentPlan`
- `DriverCheckIn`
- `VehicleEvent`
- `services.event_emission`
- `security.access` authority resolution
- `services.consultation_guard`
- concern session-event integration.

Do not rebuild them under new names.

Classify before later migration:

- generic `/treatments` legacy `VehicleEvent` API;
- legacy `CarOwnership.priority_access` boolean;
- `User.driver_score` operational scoring;
- duplicate/legacy concern route surfaces (they are currently protected by the domain-level concern session integration, so removal is not required for Wave 2.1).

## 11. Wave 2.1 conclusion

Aura does **not** need a new care-record database architecture.

It needs a lifecycle boundary.

The approved direction is:

```text
HTTP route / channel adapter
        ↓
care lifecycle service
        ↓
validate authority + legal transition
        ↓
mutate authoritative domain model
        ↓
emit canonical VehicleEvent in same transaction
        ↓
commit once
        ↓
notifications / channel side effects consume committed outcome
```

The first implementation slice should be **Consultation lifecycle**, because:

- assessment authority already depends on an active consultation;
- consultation is the entry point to professional care;
- the current model is small;
- its current direct route transitions are easy to isolate;
- adding consultation events immediately improves real longitudinal coverage without inventing predictions.

The exact transition and event contract is defined in `AURA_WAVE_2_1_STATE_EVENT_CONTRACT.md`.
