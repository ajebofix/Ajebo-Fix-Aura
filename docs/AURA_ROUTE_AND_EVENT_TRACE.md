# Aura Route and Event Trace

**Issue:** #29  
**Parent epic:** #28  
**Pull request:** #34  
**Trace baseline:** `main` at `f773e82bab7c108a196faa57efc37e1b417781e5`  
**Status:** Draft audit evidence — no production behaviour changes  

## 1. Purpose

This document traces how Aura currently changes durable state and whether each change produces a `VehicleEvent`, health snapshot, health alert or audit record.

The question is not merely whether a route writes to the database. The question is whether Aura can later reconstruct:

- what changed;
- when it changed;
- who caused it;
- under which authority;
- what the previous state was;
- what evidence supported the change;
- whether the change was visible to a client, driver or advisor;
- whether the change improved, worsened, recurred or resolved a concern.

This trace confirms that Aura already stores valuable progression evidence, but most workflows write only their domain row. `VehicleEvent` is currently a treatment/service-record table in practice, not yet the canonical event ledger for the whole platform.

## 2. Executive conclusion

### Confirmed current behaviour

`VehicleEvent` is created through only a small number of service/treatment-record paths:

1. client service entry through `cars.routes.create_service_event`;
2. advisor service entry in `admin.routes.admin_add_service`;
3. JSON treatment-record entry through `events.routes.record_treatment`.

The remaining progression-bearing systems mutate their own domain records without emitting a common event:

- Reported Concerns;
- consultations;
- assessments;
- treatment plans;
- DTC occurrences;
- recalls and maintenance schedules;
- driver check-ins;
- ownership transitions;
- conversation summaries;
- health-alert acknowledgement/resolution.

### Architecture decision direction

The evidence still supports **extending `VehicleEvent` rather than creating a parallel event table**, but only if Wave 1.2 changes its responsibility from an editable treatment-record store into a versioned, append-oriented event ledger.

The current table is usable because it already has:

- vehicle scope;
- ownership scope;
- event type;
- source;
- actor ID through `created_by`;
- duplicate fingerprint;
- flexible JSON payload;
- creation time;
- an audit companion.

It is not sufficient unchanged because it lacks:

- explicit subject type and subject ID;
- actor authority;
- immutable occurrence time separate from insertion time;
- schema version;
- visibility policy;
- previous and new state;
- progression direction;
- correlation and causation IDs;
- evidence links;
- correction semantics;
- one emission service used by all domains.

## 3. Mutation and evidence matrix

Legend:

- **Domain row:** the workflow changes its own model.
- **Event:** creates or updates `VehicleEvent`.
- **Snapshot:** creates `VehicleHealthSnapshot`.
- **Signal:** creates/resolves `VehicleHealthAlert`.
- **Audit:** creates `EventAuditLog` or a domain-specific audit record.

| Workflow | Entry point | Authority currently enforced | Domain row | Event | Snapshot | Signal | Audit | Finding |
|---|---|---|---:|---:|---:|---:|---:|---|
| Add vehicle | `cars.routes.add_car` | authenticated user; ownership checks local to route | yes | no | no | no | no | Vehicle and ownership begin without canonical progression evidence |
| Client service record | `cars.routes.add_service_record` → `create_service_event` | active owner plus active consultation | yes (`Car.current_mileage`) | yes | no | no | no | Event is written, but no event audit or health re-evaluation |
| Advisor service record | `admin.routes.admin_add_service` | advisor plus active consultation | yes (`Car.current_mileage`) | yes | no | no | no | Duplicates client helper logic instead of consuming one service |
| JSON treatment record | `events.routes.record_treatment` | active owner only | no separate domain row | yes | no | evaluates | yes | Strongest existing event path, but contract and validation remain incomplete |
| Amend treatment record | `events.routes.update_treatment_record` | owner/advisor vehicle access; creator or advisor | no | mutates existing event | no | evaluates | yes | In-place mutation conflicts with future append-only progression history |
| Archive treatment record | `events.routes.archive_treatment_record` | owner/advisor vehicle access; creator or advisor | no | soft-deletes existing event | no | evaluates | yes | Archive is audited, but canonical history should use an additive correction/tombstone event |
| Client concern through `/cars/.../concerns/add` | `cars.routes.add_reported_concern` | active owner | yes (`CarFault`) | no | no | no | no | Concern creation is invisible to canonical event history |
| Client concern through legacy `/faults/add` | `cars.fault_routes.add_fault` | active owner | yes (`CarFault`) | no | no | no | no | Parallel creation path uses slightly different title/source behaviour |
| Driver concern | `driver.routes.driver_report_issue` | assigned driver through vehicle access helper | yes (`CarFault`) | no | no | no | no | Important operating observation is not linked to the timeline ledger |
| Advisor concern | `admin.routes.admin_add_concern` | advisor plus active consultation | yes (`CarFault`) | no | no | no | no | Starts directly at `under_review` but does not preserve a transition event |
| Review concern | `admin.routes.admin_review_concern` | advisor | yes | no | no | no | no | `reported → under_review` is not durably represented outside the current row |
| Monitor concern | `admin.routes.admin_monitor_concern` | advisor | yes | no | no | no | no | `under_review/reported → monitoring` has no transition evidence |
| Resolve concern | `admin.routes.admin_resolve_concern` | advisor | yes | no | no | no | no | Resolver and time are stored, but previous state and supporting action are not |
| Client consultation request | `cars.routes.book_consultation` | active owner | yes (`Consultation`) | no | no | no | no | Booking, state creation and outbound messages are not correlated by one event ID |
| Priority consultation request | `cars.routes.request_priority_scheduling` | feature-gated ownership | yes | no | no | no | no | Uses the same `scheduled` state without a typed request source/event |
| Advisor schedules consultation | `admin.routes.admin_schedule_consultation` | advisor | yes | no | no | no | no | No canonical state-created event |
| Start consultation | `admin.routes.admin_start_consultation` | advisor; only `scheduled` | yes | no | no | no | no | `scheduled → in_progress` is progression-critical but not emitted |
| Complete consultation | `admin.routes.admin_complete_consultation` | advisor; finalised assessment required | yes | no | no | no | no | `in_progress → completed` and summaries remain only on the consultation row |
| Start assessment | `admin.routes.admin_start_assessment` | advisor; active consultation required | yes (`VehicleAssessment`) | no | no | no | no | Draft creation is not correlated into event history |
| Edit assessment | `admin.routes.admin_edit_assessment` | advisor; draft only | yes plus delete/recreate child rows | no | no | no | no | Draft risks/options are destructively replaced without a domain audit trail |
| Finalise assessment | `admin.routes.admin_finalize_assessment` | advisor; complete statuses required | yes | no | no | no | no | Finalisation also creates an approved `TreatmentPlan`, but neither action emits an event |
| Start treatment plan | `admin.routes.start_treatment_plan` | advisor | yes | no | no | no | no | No transition guard beyond route selection; previous state is not recorded |
| Complete treatment plan | `admin.routes.complete_treatment_plan` | advisor | yes | no | no | no | no | Completion does not link to treatment/service evidence |
| Defer treatment plan | `admin.routes.defer_treatment_plan` | advisor | yes | no | no | no | no | Sets `deferred` but client message says monitoring; no reason or event evidence |
| Add DTC occurrence | `admin.routes.add_vehicle_dtc` → `DTCDecoderService.add_vehicle_dtc` | advisor | yes (`VehicleDTC`) | no | no | no | no | Detection is preserved, but not represented in general progression history |
| Clear DTC occurrence | `admin.routes.clear_vehicle_dtc` → `DTCDecoderService.clear_vehicle_dtc` | advisor | yes | no | no | no | no | Clear actor/time are stored, but no correlation to inspection or treatment |
| VIN decode | `admin.routes.decode_vehicle_vin` → `VINDecoderService` | advisor | yes (`VehicleProfile`) | no | no | no | no | Identity enrichment is not an event; that is acceptable unless material identity changes need audit |
| Driver daily check-in | `driver.routes.driver_daily_checkin` | assigned driver | yes (`DriverCheckIn`, `User.driver_score`) | no | no | no | no | High-value observation evidence remains disconnected from timeline and health snapshots |
| Client stewardship transfer | `ownership.routes.request_stewardship_transfer` | active owner plus password/explicit confirmation | yes (`CarOwnership`) | no | attempted | indirect | attempted | Current audit/snapshot path contains blocking defects described below |
| Advisor stewardship reassignment | `ownership.routes.advisor_reassign_stewardship` | advisor | yes | no | no | no | attempted | Attempts an audit record with no event ID; no post-transfer snapshot hook |
| Manual health snapshot | `services.vehicle_health_snapshot.create_health_snapshot` | service call, no route-level policy itself | yes (`VehicleHealthSnapshot`) | no | yes | evaluates | no | Useful evidence source, but almost no valid callers were found |
| Care-signal evaluation | `CareSignalService.evaluate` | service call | yes (`VehicleHealthAlert`) | no | no | yes | no | Signals are derived state, but rule/service key mismatches limit correctness |
| Acknowledge alert | `admin.routes.acknowledge_alert` | advisor decorator only | yes | no | no | yes | no | Actor/time stored; no canonical state event |
| Resolve alert | `admin.routes.resolve_alert` | advisor decorator only | yes | no | no | yes | no | Resolution time stored; no cause/evidence/correlation |
| Rina raw message | `routes.chat.save_message` | authenticated user | yes (`ChatMessage`) | no | no | no | no | Raw turn is user-scoped rather than vehicle-scoped |
| Rina clinical summary | `services.conversation_logger.log_conversation_record` | invoked by chat route | yes (`ConversationRecord`) | no | no | no | no | Vehicle-scoped evidence exists, but is not correlated to a concern or event |
| Advisor note | `admin.routes.add_advisor_note` | advisor | yes (`AdvisorNote`) | no | no | no | no | Correctly remains internal; should emit only an advisor-private event when operationally material |

## 4. `VehicleEvent` call-site findings

### 4.1 Three creation contracts currently disagree

#### Client service helper

`cars.routes.create_service_event` creates:

- `event_type='service'`;
- fingerprint from car, ownership, service type, mileage and service date;
- `created_at` set to the supplied service date;
- no `EventAuditLog`;
- no `HealthAlertService.evaluate`;
- no health snapshot.

#### Advisor service route

`admin.routes.admin_add_service` repeats the same logic rather than calling the shared client helper. Its fingerprint adds the literal `admin`, which means the same real service can be accepted once through the client route and once through the advisor route.

#### Treatment-record API

`events.routes.record_treatment` permits a caller-supplied event type and severity, writes an `EventAuditLog`, and runs signal evaluation. Its fingerprint omits ownership, mileage, date and description.

The result is not one canonical event contract. It is three related implementations with different duplicate, auditing and downstream behaviours.

### 4.2 Fingerprint and duplicate behaviour conflict

The treatment-record API first checks for similar records created in the previous 24 hours, but the database fingerprint is globally unique. Because the fingerprint is based only on:

```text
car_id + title + event_type + severity
```

an identical legitimate record may be blocked forever, not merely for 24 hours.

### 4.3 Mileage contract can fail at the database

`events.routes.record_treatment` allows omitted mileage, while `VehicleEvent.mileage` is non-nullable. A payload without mileage can pass route validation and fail on commit.

### 4.4 Occurrence time is ambiguous

The model contains both:

- `event_date` as a date;
- `created_at` as a datetime.

Service routes store the real service date in `created_at`, while other event paths use insertion time. The canonical contract needs:

- immutable `occurred_at`;
- separate `recorded_at`;
- optional trustworthy source timestamp metadata.

### 4.5 Current edits rewrite the event

The PATCH route mutates the existing `VehicleEvent` and stores old/new snapshots in `EventAuditLog`. That preserves some history, but a canonical progression ledger should treat material corrections as additive records referencing the original event.

## 5. Confirmed route and service defects

These are audit findings, not fixes in PR #34.

### 5.1 Stewardship audit cannot satisfy the current model

`EventAuditLog.event_id` is declared non-nullable and references `vehicle_events.id`, but both stewardship transfer routes create `EventAuditLog(event_id=None, ...)`.

Expected result under the declared schema: the transaction fails before transfer completion. A generic event-audit model cannot currently audit non-`VehicleEvent` domains.

**Architecture implication:** either canonical domain transitions must emit a `VehicleEvent` first and audit that event, or a separate general mutation-audit contract must be introduced deliberately. Do not loosen `event_id` casually without deciding which responsibility the table owns.

### 5.2 Snapshot hook uses the wrong keyword

`create_health_snapshot` accepts:

```python
recorded_via
```

The client stewardship route calls it with:

```python
triggered_by='stewardship_transferred'
```

Even if the transfer transaction succeeds, this call raises `TypeError` and no snapshot is created.

### 5.3 “Intelligence trigger” has no side effects

`trigger_vehicle_intelligence` is an alias of `invoke_vehicle_assessment`, whose documented contract is read-only and explicitly performs no storage, notification or action.

The stewardship route invokes it and ignores the returned assessment. The call therefore creates no durable progression evidence.

### 5.4 Active trajectory route calls a missing method

The registered route in `routes/health_trends.py` calls:

```python
HealthTrendService.analyze_car_trend(car_id)
```

The service exposes only:

```python
analyze_car_trajectory(car_id)
```

The active client trajectory endpoint is therefore inconsistent with the service API.

### 5.5 Historical trajectory module is also incompatible

`health/trend_routes.py` is not registered by `app.py`, duplicates the same URL family and calls another missing method:

```python
HealthTrendService.analyze(car_id)
```

It should be classified as deprecated after tests prove no external import depends on it.

### 5.6 Signal rule reads the wrong trajectory key

`VehicleCareTrajectoryService` returns:

```text
accelerated_deterioration
```

`CareSignalService.evaluate` checks:

```text
rapid_decline
```

The declining-trajectory signal cannot be raised through this rule as currently written.

### 5.7 Advisor helpers call a boolean property as a function

The canonical `User.is_admin` implementation is a property. These helpers call `user.is_admin()`:

- `health.routes.is_advisor`;
- `health.alert_routes.is_advisor`;
- `events.audit_routes.require_advisor`;
- `health.trend_routes.is_advisor`.

Those advisor paths can raise `TypeError: 'bool' object is not callable` rather than returning an authorisation decision.

### 5.8 Concern states are inconsistent at creation

Client and driver paths create `CarFault.status='reported'`, but `CarFault.is_active()` recognises only:

- `under_review`;
- `monitoring`.

Other code treats every non-`resolved` concern as active. This produces competing definitions of “active concern”.

### 5.9 Assessment draft edits are destructive

The advisor assessment edit route deletes all risk and treatment-option child rows, then recreates them from the form. That is reasonable for a mutable draft, but there is no domain audit proving what changed before finalisation.

Wave 1.2 should not turn every draft keystroke into a client-visible event. It should emit only material lifecycle events and preserve professional-document audit separately.

### 5.10 Consultation booking is not atomic

The client booking route commits the consultation, updates booking intent through additional commits, repeats the booking-intent completion query, then attempts WhatsApp delivery.

There is no correlation ID joining:

- booking intent;
- consultation;
- client message;
- advisor message;
- future assessment.

The event layer should provide correlation, while message delivery remains non-fatal and separately auditable.

## 6. Health snapshot and signal trace

### Current flow

```text
create_health_snapshot
        ↓
calculate_vehicle_health
        ↓
insert VehicleHealthSnapshot
        ↓
commit
        ↓
CareSignalService.evaluate
        ↓
calculate_vehicle_health again
        ↓
analyse snapshot trajectory
        ↓
raise or resolve VehicleHealthAlert
        ↓
commit
```

### Findings

1. Snapshot generation and signal evaluation are separate commits, so a snapshot may exist even when signal evaluation fails.
2. `recorded_via` is free text and not connected to a durable source event.
3. The snapshot does not preserve the rule/version that produced its score.
4. Alert creation avoids duplicates by active type, but no link identifies which snapshot or event caused the signal.
5. Automatic signal resolution mutates the existing alert rather than recording the evidence that normalised it.
6. The only discovered snapshot caller is the currently broken client stewardship-transfer hook.

### Required Wave 1 direction

Every snapshot should eventually reference:

- the triggering event/correlation ID;
- health-rule version;
- inputs/provenance summary;
- calculation timestamp;
- ownership context.

Every alert should reference:

- the snapshot/event that raised it;
- the rule and threshold;
- the event/snapshot that resolved it;
- advisor acknowledgement separately from technical resolution.

## 7. Duplicate route-family classification

| File/module | Runtime status | Classification direction | Reason |
|---|---|---|---|
| `routes/health_trends.py` | registered | keep and repair as canonical route surface | It is the blueprint registered by `app.py`, but its service call is wrong |
| `health/trend_routes.py` | unregistered | deprecate after dependency search | Duplicate URL/blueprint concept and calls nonexistent service method |
| `admin/modules/assessments.py` | registered | keep as narrow advisor report-download module | It owns the registered `/admin/assessments/<id>/download` route only |
| `admin/routes.py` assessment functions | registered | canonical lifecycle today; later extract to service/module | Start/edit/finalise logic is active here |
| `routes/assessments.py` | unregistered | deprecate/delete later | Test-only route, duplicated imports and imports `db` from `app` |
| `cars/modules/assessments.py` | unregistered | defer decision | Contains a client download route, but current client report access already exists elsewhere |
| `cars/fault_routes.py` | registered | compatibility route family | Legacy `/faults` URLs remain active; creation logic should later consume one concern service |
| `cars.routes.add_reported_concern` | registered | canonical UI candidate | Overlaps legacy concern creation and must not keep separate domain logic indefinitely |
| `events/routes.py` | registered | canonical event API candidate | Strongest event/audit path, but currently owner-created editable treatment records only |

## 8. Authority findings from mutation routes

### Positive controls already present

- owner vehicle routes usually scope through active `CarOwnership`;
- driver vehicle routes use `require_vehicle_access` with driver-only permissions;
- advisor routes generally use `@advisor_required`;
- consultation state transitions check expected current state;
- final assessment is guarded as a point of no return;
- DTC mutation routes are advisor-only;
- stewardship transfer requires password confirmation and explicit confirmation.

### Gaps

- broad state-changing logic lives directly in routes rather than domain services;
- several advisor-only helpers implement authority differently and some are broken;
- treatment-record creation uses a local owner query instead of the canonical vehicle-access helper;
- treatment-plan transitions do not validate permitted previous states;
- alert acknowledge/resolve routes omit `@login_required` even though `@advisor_required` may currently perform that check internally;
- event history permits clients only when they created the event, not necessarily when they own the vehicle;
- advisor-created service records therefore do not expose their audit history to the owner through the client endpoint;
- no central policy determines event visibility by subject, actor, vehicle relationship and event type.

## 9. Final event architecture recommendation from route tracing

### Decision

**Extend `VehicleEvent`; do not introduce a new parallel event table.**

This recommendation is now stronger after route tracing because:

- the model is already used in client, advisor and JSON treatment workflows;
- existing reports and timelines already read it;
- fingerprints and audit history already depend on it;
- replacing it would require migrating working service history and compatibility routes;
- the missing capabilities can be added incrementally without inventing historical facts.

### Responsibility change

`VehicleEvent` should become the immutable envelope for material vehicle progression. Domain models remain authoritative for current state.

Example:

```text
CarFault.status = 'resolved'              ← authoritative current concern state
VehicleEvent(type='concern.resolved')     ← immutable fact that the transition occurred
```

The event payload must not become the only copy of the domain object.

### Required canonical fields

Wave 1.2 should design and migrate toward:

- `occurred_at` — when the real-world/domain event occurred;
- `recorded_at` — when Aura stored it;
- `event_type` — controlled taxonomy;
- `schema_version`;
- `subject_type` and `subject_id`;
- `actor_id` and `actor_authority`;
- `source_domain` and `source_channel`;
- `previous_state` and `new_state`;
- `progression_direction`;
- `visibility`;
- `correlation_id`;
- `causation_event_id`;
- `evidence_links` or typed relation rows;
- idempotency key/fingerprint;
- correction/supersession reference;
- optional ownership snapshot ID.

### Emission service

Routes should not construct event rows independently. A single service should provide operations similar to:

```python
emit_event(
    *,
    vehicle_id,
    event_type,
    subject,
    actor,
    authority,
    occurred_at,
    previous_state,
    new_state,
    visibility,
    source,
    correlation_id,
    evidence,
    idempotency_key,
)
```

The service should:

- validate taxonomy and schema version;
- verify vehicle/subject consistency;
- normalise authority and visibility;
- enforce idempotency;
- write the event in the same transaction as the domain transition where possible;
- enqueue/recalculate snapshots and signals safely after commit;
- create privacy-safe operational logs;
- support additive corrections rather than destructive rewrites.

### Migration sequence

1. Add nullable canonical fields to the existing table.
2. Classify existing rows as legacy service/treatment events with a fixed schema version.
3. Backfill only facts already provable from each row; do not invent actor authority, occurrence precision or visibility.
4. Introduce the central emission service.
5. Convert one domain at a time, beginning with Reported Concern transitions.
6. Convert consultation and assessment lifecycle.
7. Convert treatment plans, DTC occurrences and driver check-ins.
8. Connect snapshots and signals through event correlation.
9. Replace in-place event edits with additive correction semantics.
10. Remove legacy creation paths only after compatibility tests and production observation.

## 10. Next audit tasks

This route/event trace completes a major part of Issue #29, but PR #34 should remain draft until the remaining audit work is finished:

- [x] trace `VehicleEvent` creation, edit and archive paths;
- [x] trace Reported Concern mutation paths;
- [x] trace consultation, assessment and treatment-plan transitions;
- [x] trace DTC add/clear paths;
- [x] trace stewardship, snapshot and signal hooks;
- [x] classify the known duplicate health and assessment route modules;
- [x] issue a route-tracing recommendation to extend `VehicleEvent`;
- [ ] inspect every Alembic migration and identify the actual production head;
- [ ] verify database constraints against PostgreSQL behaviour;
- [ ] catalogue every service module and side effect;
- [ ] confirm all `UserMemory` and `ChatSession` consumers or dormancy;
- [ ] map WhatsApp and Resend delivery/audit behaviour end to end;
- [ ] catalogue CI workflows and uncovered high-risk routes;
- [ ] produce the final repository-wide keep/merge/deprecate matrix;
- [ ] define final Wave 1.2 rollback and compatibility requirements.

## 11. Freeze remains active

Until Issue #29 is completed and PR #34 is reviewed:

- do not add another event table;
- do not add another Rina memory table;
- do not build predictive-health claims;
- do not create a second vehicle-authority resolver;
- do not rename/remove working legacy tables;
- do not bundle the defects found here into the documentation PR.

Each confirmed defect should become a separate, narrow implementation issue or security/bug-fix PR after the audit decides ownership and priority.
