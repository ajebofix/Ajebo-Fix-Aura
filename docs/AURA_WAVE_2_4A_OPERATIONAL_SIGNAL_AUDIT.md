# Aura Wave 2.4A — Operational Signal Audit

Issue: #108  
Parent epic: #74  
Workflow backlog: #3  
Status: audit only; no runtime/schema changes in this slice

## 1. Purpose

Wave 2.4 moves Aura's remaining priority, monitoring and driver operational signals into explicit, trustworthy longitudinal records without turning derived scores, UI queues or Rina guidance into fake durable care facts.

This audit maps what exists today before the Wave 2.4 state/event contract is implemented.

## 2. Architectural rule carried forward

Wave 2.4 must reuse the existing domain rows and the canonical `VehicleEvent` ledger. It must not create a parallel workflow engine or duplicate facts already represented by Reported Concerns, Consultations or Treatment Plans.

State mutation belongs in explicit domain services. Routes, templates, Rina and channel adapters may request transitions but must not own transition legality or independent commits.

## 3. Driver workflow — current behavior

### 3.1 Driver assignment

`CarDriver` currently stores:

- `car_id`;
- `user_id`;
- `assigned_at`;
- `is_active`.

There is no observed accept/decline state machine on the assignment itself.

The driver dashboard queries active assignments and exposes only assigned vehicles.

### 3.2 Driver concern report

`driver.routes.driver_report_issue`:

- requires driver role;
- requires assigned-vehicle access;
- creates `CarFault(status="reported", source="driver")`;
- commits directly in the route.

Decision carried forward from Wave 2.1: this is a Reported Concern fact. It must continue to use the existing `concern.reported` family, not a second driver-specific concern event.

### 3.3 Driver daily check-in

`driver.routes.driver_daily_checkin` currently:

- requires driver role and assigned-vehicle access;
- checks for an existing row using `date(created_at) == today`;
- creates `DriverCheckIn` directly in the route;
- captures `tyre_warning`, `fuel_low`, `dashboard_light`, `vibration`, `unusual_sound` and free-text `notes`;
- mutates `User.driver_score` as a side effect;
- commits directly in the route;
- emits no canonical `VehicleEvent`.

### 3.4 Check-in integrity gap

The one-check-in-per-day rule is application-only. Concurrent requests can pass the pre-query and create duplicate rows because no database uniqueness contract enforces the operational day.

The model does not currently persist an explicit operational date separate from `created_at`, so a durable same-day uniqueness contract will require a schema decision.

### 3.5 Driver score product drift

`User.driver_score` starts at 100 and the check-in route adds/subtracts points based on submission and warning flags.

Wave 2.1 explicitly classified this score outside the canonical observation contract. It is a gamified user score, not a trustworthy vehicle-health fact and must not become a permanent longitudinal intelligence signal by accident.

## 4. VehicleHealthAlert / care-signal workflow — current behavior

### 4.1 Durable record already exists

`VehicleHealthAlert` is the existing durable record used for care signals/notices. Existing architecture documents classify it as the record to keep and repair rather than replace.

Current code uses fields including:

- vehicle / ownership linkage;
- `alert_type`;
- `severity`;
- `status`;
- `message`;
- `is_active`;
- creation timestamp;
- resolution timestamp and related resolution metadata where present.

### 4.2 CareSignalService

`services.health_alert_service.CareSignalService.evaluate()`:

- calculates current health;
- inspects current trajectory;
- raises or resolves signals for rules such as low health status, declining trajectory, elevated-risk indicators and maintenance monitoring;
- `_raise_signal()` prevents a second active row for the same car/ownership/type using an application query;
- `_resolve_signal()` sets `is_active=False` and `resolved_at`;
- `evaluate()` commits internally.

It emits no canonical signal event.

### 4.3 Recurrence problem

The existing active-row lookup and historical uniqueness assumptions were already identified in architecture/migration audits as problematic for recurrence history. A signal that resolves and later genuinely recurs must become a new durable occurrence rather than silently mutate old history or be blocked by uniqueness design.

## 5. Alert / notice routes — current behavior

There are overlapping read surfaces:

- `alerts/routes.py` exposes owner active signals/history and advisor active signals;
- `health/alert_routes.py` exposes similar owner/advisor clinical-notice views.

This is duplicate projection/API surface over the same `VehicleHealthAlert` model.

`alerts.routes.advisor_close_signal` directly:

- checks advisor role;
- sets `is_active=False`;
- sets `resolved_at`;
- commits in the route.

There is no service-owned transition and no canonical event.

## 6. Alert Center — durable records mixed with ephemeral queue projections

`AlertService.build_alert_center()` combines four different concepts into one UI list:

1. active durable `VehicleHealthAlert` rows;
2. derived recurring-concern entries when three similar unresolved concerns occur within 14 days;
3. derived consultation-delay entries;
4. derived monitoring-stall entries for Treatment Plans in monitoring for at least 14 days.

Only item 1 is a durable alert row.

The other entries have `id=None` and are projections calculated at read time. They must not be treated as canonical state transitions or backfilled into event history merely because they appear in the Alert Center.

If Wave 2.4 later decides one of these projections deserves durable lifecycle state, it must create an explicit durable subject first.

## 7. Monitoring — current meaning is fragmented

"Monitoring" currently appears in several places with different meanings:

- Reported Concern lifecycle (`monitoring`);
- Treatment Plan lifecycle (`monitoring`);
- care-signal rules and alert-center monitoring stalls;
- Rina escalation guidance (`monitor`);
- care-plan entitlement (`active_monitoring`).

These are not one global state machine.

Wave 2.4 must preserve their domain meanings and avoid creating a generic `monitoring.*` event family that collapses unrelated facts.

## 8. Priority — current behavior

### 8.1 Entitlement

`CarOwnership.care_plan` and `services.feature_gateways` define whether priority capabilities are enabled. `priority_access` enables feature flags such as priority scheduling, priority coordination and emergency review.

This is entitlement only. It is not a priority request, queue item or professional decision.

### 8.2 Priority scoring

`PriorityScoringEngine.calculate()` derives a score/band from:

- unresolved concerns;
- an in-progress consultation;
- latest Treatment Plan state;
- calculated health status.

The result is a read-time score (`low|moderate|high|critical`) and is not persisted as a durable priority-request subject.

The engine also still references the legacy `approved` Treatment Plan state, so it is not yet aligned with the production-proven Wave 2.3 canonical plan states.

The score must not itself become a canonical event source.

### 8.3 Missing durable priority request

Wave 2.1 already identified that priority scheduling is encoded indirectly through consultation/notes and that no dedicated priority-request model exists.

Issue #3 requires first-class priority request, emergency request, queue, accept, defer and resolve behavior with eligibility, reason, health context, urgency, advisor outcome and timestamps.

A `priority.*` event family is therefore not valid until a durable priority subject is approved.

## 9. Rina escalation — current behavior

`RinaEscalationEngine` derives one of:

- `monitor`;
- `flag`;
- `escalate`.

Inputs include calculated health, guidance and active care-signal context.

The engine is explicitly non-diagnostic and returns guidance such as professional review recommended. It does not represent a human professional decision.

Therefore Rina escalation output is advisory projection, not durable priority workflow state. Rina may structure or recommend an escalation request later, but may not accept, resolve or manufacture professional priority history.

## 10. Existing canonical-event posture

Wave 2.1 already reserved:

- `driver_observation.checkin_recorded` with `subject_type="driver_checkin"`;
- no duplicate event for driver concern reports;
- no `priority.*` events until a durable priority subject is approved.

Current `services.event_emission` remains the canonical event constructor and caller-owned transaction boundary.

## 11. Primary defects / gaps to address after contract lock

1. Driver check-ins mutate and commit in route code.
2. Driver check-ins emit no canonical observation event.
3. Same-day check-in uniqueness is not database enforced.
4. `driver_score` is mixed into an otherwise useful observation workflow.
5. Care signals raise/resolve with internal service commits and no canonical events.
6. Advisor signal close mutates directly in a route.
7. Alert/notice read surfaces are duplicated.
8. Alert recurrence semantics are not cleanly enforced at the database level.
9. Alert Center mixes durable records with ephemeral projections.
10. Priority is derived/entitlement-only; no durable request/queue subject exists.
11. Priority scoring still contains legacy Treatment Plan assumptions.
12. Rina escalation guidance could be confused with durable escalation if not explicitly bounded.
13. No unified object-level event contract yet exists for priority/alert/check-in lifecycle.

## 12. Historical integrity rule

Wave 2.4 must not synthesize historical canonical events from existing DriverCheckIn, VehicleHealthAlert, priority-score or Rina-escalation data unless actor, timestamp, subject and transition semantics can be proven deterministically.

Default posture: no speculative backfill.

## 13. Recommended implementation order after this audit

1. DriverObservationService + check-in uniqueness + canonical event.
2. CareSignalLifecycleService + recurrence-safe alert lifecycle + canonical events + route cutover.
3. Durable PriorityRequest contract/model + lifecycle/events only after its schema/authority contract is approved.
4. Driver assignment/operational-status completion if still required by #3.
5. Consolidate overlapping read surfaces only after mutation contracts are production-proven.

Wave 2.5 should consume the resulting genuine production events; it must not treat derived priority scores or Rina guidance as labeled longitudinal outcomes.
