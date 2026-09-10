# Aura Wave 2.4 — Operational State Closeout

Parent epic: #74  
Architecture contract: #108 / `AURA_WAVE_2_4_STATE_EVENT_CONTRACT.md`  
Closeout issue: #125  
Status: **2.4E implementation candidate — production verification required after merge**

## 1. Purpose

Wave 2.4 establishes trustworthy operational state for three previously ambiguous areas without turning read-time scoring, queue projections, entitlements, or Rina guidance into false longitudinal history.

The permanent rule is unchanged: a canonical event must point to a durable domain subject representing a real operational fact.

## 2. Durable Wave 2.4 subjects

### Driver observation

Durable subject: `DriverCheckIn`  
Canonical family: `driver_observation.checkin_recorded`

The check-in is an additive observation. It does not diagnose the vehicle, mutate `driver_score`, or create a second concern event for the same report.

### Care signal

Durable subject: `VehicleHealthAlert`  
Canonical family:

- `care_signal.raised`
- `care_signal.acknowledged`
- `care_signal.resolved`

Resolved occurrences are terminal. A genuine recurrence creates a new occurrence and a new raised event rather than reopening history.

### Priority Access request

Durable subject: `PriorityRequest`  
Canonical family:

- `priority.requested`
- `priority.review_started`
- `priority.accepted`
- `priority.deferred`
- `priority.resolved`
- `priority.cancelled`

Priority entitlement, advisor review scoring and Rina escalation guidance are not substitutes for a `PriorityRequest` row. A linked Consultation keeps its own lifecycle.

## 3. Read-time projections that remain non-canonical

The following are advisor-support projections only:

- recurring-concern projection;
- unresolved-consultation delay projection;
- treatment-monitoring staleness projection;
- `PriorityScoringEngine` score/band;
- Rina `monitor|flag|escalate` guidance;
- care-plan feature entitlement.

Projection creation, disappearance or score movement does not emit a canonical VehicleEvent.

## 4. Wave 2.4E reconciliation

Wave 2.4E makes the boundary visible in the product and code:

1. Alert Center identifies `VehicleHealthAlert` care signals as durable lifecycle records.
2. Recurring-concern, consultation-delay and monitoring-stall rows are explicitly labelled computed read-only projections.
3. The duplicate advisor-wide clinical-notices read route is consolidated into the Alert Center; the client-safe per-vehicle notice projection remains for active owners.
4. The dashboard's historic "Priority Queue" score presentation is renamed to an **Advisor Review Projection** so it cannot be confused with the durable **Priority Requests** queue.
5. `PriorityScoringEngine` remains compatibility code but is explicitly non-workflow, returns projection metadata/reasons, and is aligned with current Consultation and Wave 2.3 Treatment Plan states.
6. Consultation-delay projection uses current states (`requested`, `scheduled`, `in_progress`, `deferred`) rather than the obsolete `approved` assumption.
7. The client registry presents the calculated value as an advisor review score, not as a durable priority state.

## 5. Longitudinal coverage gate

The closeout regression must prove that one vehicle can hold all three Wave 2.4 facts at the same time while preserving distinct subjects:

```text
driver_observation.checkin_recorded -> driver_checkin
care_signal.raised                  -> vehicle_health_alert
priority.requested                  -> priority_request
```

The test must also prove that calculating the advisor review projection emits no canonical event and that no generic `monitoring.*` family is manufactured.

## 6. Production posture

Wave 2.4D merge `8daaf70e21accc02c2a18e65140a091eabd7477d` reached Railway production successfully before 2.4E began.

Wave 2.4 is not formally complete until the 2.4E pull request:

- passes its dedicated longitudinal/projection CI;
- passes all triggered regression/migration/security checks;
- merges to `main`;
- reaches Railway `SUCCESS` with `flask db upgrade && gunicorn app:app` startup healthy;
- shows no new migration/runtime errors in deployment logs.

## 7. Non-blocking debt after Wave 2.4

These items do not justify extending Wave 2.4:

- legacy `driver_score` column removal/renaming;
- broader visual redesign of advisor queues;
- Python/SQLAlchemy deprecation cleanup unrelated to state integrity;
- richer client-facing care-signal wording/category polish;
- Maintenance Intelligence Foundation;
- predictive model work.

They remain separate backlog work. Wave 2.5 may consume the genuine production events created by Wave 2.4, but must not train on or label advisor review scores, Alert Center projections, or Rina guidance as operational outcomes.
