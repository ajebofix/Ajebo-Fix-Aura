# Aura Wave 2.2A — Consultation Lifecycle Production Closeout

**Status:** Production-proven and closed  
**Scope:** Consultation request → advisor schedule → start → assessment-gated completion  
**Closeout date:** 23 August 2026  
**Latest production verification commit:** `6212311811f15ac006ad5c50c216cbf31d8234bb`

## 1. Purpose

This document closes Wave 2.2A: Aura's advisor-governed Consultation lifecycle and canonical `consultation.*` event slice.

The closeout records what was actually implemented and exercised in production, the defects uncovered by live use, the safety properties that held during those defects, and the explicit handoff to Wave 2.2B — Vehicle Assessment lifecycle.

This closeout does **not** claim that every reserved Consultation transition is implemented. `rescheduled`, `deferred`, `cancelled` and `reopened` remain later compatibility/extension work unless a concrete product flow requires them.

## 2. Canonical production flow

The production-proven flow is:

```text
owner submits preferred consultation time
        ↓
Consultation = requested
        ↓
consultation.requested
        ↓
advisor reviews request
        ↓
advisor confirms schedule
        ↓
Consultation = scheduled
        ↓
consultation.scheduled
        ↓
advisor starts session
        ↓
Consultation = in_progress
        ↓
consultation.started
        ↓
Vehicle Assessment exists and is finalized
        ↓
advisor completes consultation
        ↓
Consultation = completed
        ↓
consultation.completed
```

The owner-preferred time is not represented as an advisor-confirmed appointment. The advisor queue explicitly distinguishes **Requested / Preferred time** from **Scheduled**.

## 3. Domain ownership established

`ConsultationLifecycleService` is now the explicit lifecycle owner for the implemented Consultation transitions.

The migrated runtime routes delegate lifecycle legality to the service rather than directly assigning Consultation status.

The service enforces:

- owner authority for creating a request;
- advisor authority for schedule/start/complete;
- legal source-state transitions;
- finalized-assessment precondition before completion;
- domain mutation + canonical VehicleEvent inside one caller-owned database transaction;
- idempotent canonical event semantics;
- client-safe event payloads that omit internal advisor summaries.

Notifications such as WhatsApp are downstream of the committed domain fact. A notification-provider failure cannot roll back a valid Consultation request.

## 4. Canonical Consultation events

Wave 2.2A production supports exactly these canonical events:

```text
consultation.requested
consultation.scheduled
consultation.started
consultation.completed
```

Canonical subject:

```text
subject_type = consultation
```

These events are workflow facts and use:

```text
progression_direction = not_applicable
```

They do not claim mechanical deterioration/improvement and do not constitute diagnosis or treatment approval.

## 5. Production verification completed

Live production testing verified the complete implemented lifecycle on the production Mercedes-Benz GLE test vehicle/account flow:

### Owner request

- an existing active Consultation correctly prevented a second owner request;
- after the active Consultation was completed, the owner could open the Consultation request form again;
- a fresh owner request was submitted successfully;
- owner UI showed **Consultation requested**;
- advisor queue showed the record under **Requested**;
- the timestamp was labelled **Preferred time**, not Scheduled.

### Advisor schedule

- advisor opened the request-specific confirmation surface;
- Aura explicitly stated the preferred time was not yet a confirmed appointment;
- advisor confirmed the schedule;
- the record moved from Requested to Scheduled.

### Advisor start

- advisor started the scheduled Consultation;
- the record moved to In Progress;
- the queue exposed the assessment continuation and completion actions.

### Assessment-gated completion

- Consultation completion remained coupled to the professional Vehicle Assessment boundary;
- after a finalized assessment existed, the advisor successfully completed the Consultation;
- owner-side active-consultation blocking cleared after completion;
- the owner could submit a new Consultation request afterward.

## 6. Production defects found and corrected

Live production verification uncovered several defects. They are part of the closeout evidence because the system's rollback and authority boundaries were exercised under failure.

### 6.1 PostgreSQL canonical-event constraint lag — PR #81

The application event taxonomy supported `consultation.*`, but the stricter production PostgreSQL `vehicle_events` check still allowed only the earlier concern/evidence families.

A live `consultation.completed` attempt therefore failed with PostgreSQL `CheckViolation`.

Important safety result: Consultation mutation and canonical event shared one transaction, so the failed completion rolled back. Aura did not leave a falsely completed Consultation without its event.

Alembic revision `e8f5c1a7b240` aligned the production database constraint with the four approved Consultation events and state transitions while preserving the earlier concern/evidence contract and legacy compatibility.

### 6.2 Assessment draft repeating-field loss — PR #82

During the same production smoke path, Vehicle Assessment repeating fields used `[]` names in the template while the legacy route read unsuffixed names.

The route could therefore delete existing risk/treatment child rows and recreate zero rows while returning a successful save.

The hardened compatibility adapter now accepts both naming forms, validates parallel repeated groups before replacement, preserves child rows on partial submissions and uses Post/Redirect/Get so a reload proves persisted state.

This defect is not a Consultation lifecycle defect, but it established critical requirements handed to Wave 2.2B.

### 6.3 Owner assessment report authorization — PRs #83 and #84

The shared vehicle profile rendered an advisor-oriented assessment report endpoint for owners.

Initial correction removed the 403 but exposed that the target client blueprint was not registered in production, causing Flask `BuildError` / HTTP 500.

The final production route now performs explicit authority directly:

- advisor/admin may access a finalized assessment;
- an active vehicle owner may access that vehicle's finalized assessment;
- unrelated users are denied;
- inactive former owners are denied;
- draft assessments remain unavailable.

Production verification confirmed all three historical finalized assessment report links open successfully for the active owner.

## 7. Implementation PR sequence

Wave 2.2A was delivered and hardened through:

- **PR #79 — Wave 2.2A1:** Consultation lifecycle domain service and canonical events;
- **PR #80 — Wave 2.2A2:** runtime route cutover and Requested advisor queue;
- **PR #81 — Wave 2.2A3:** PostgreSQL canonical Consultation event contract;
- **PR #82 — Wave 2.2A4:** assessment draft persistence hardening discovered during live verification;
- **PR #83 — Wave 2.2A5:** owner assessment report authorization correction;
- **PR #84 — Wave 2.2A6:** final owner assessment report 500 fix and authorization regression gate.

The final production runtime identity used for closeout verification is:

```text
6212311811f15ac006ad5c50c216cbf31d8234bb
```

Railway reported the deployment as `SUCCESS` with PostgreSQL at Alembic revision `e8f5c1a7b240`.

## 8. Authority and privacy boundaries after Wave 2.2A

Future Consultation work must preserve these rules:

1. owner requests do not equal confirmed schedules;
2. only advisor authority can confirm, start or complete a professional Consultation;
3. completion requires the professional assessment boundary defined by the lifecycle service;
4. domain mutation and canonical event succeed or fail together;
5. notifications are downstream channels, not state owners;
6. internal advisor summaries are not copied into client-visible canonical event payloads;
7. historical Consultation rows remain readable without synthetic event backfill;
8. workflow events remain `not_applicable` to mechanical progression;
9. Rina/provider output cannot approve Consultation state transitions;
10. object-level vehicle/ownership authorization remains mandatory for client care records.

## 9. Intentionally deferred Consultation scope

The following remain outside the completed Wave 2.2A slice:

- explicit `consultation.rescheduled` runtime flow;
- explicit `consultation.deferred` runtime flow;
- explicit `consultation.cancelled` runtime flow;
- explicit `consultation.reopened` runtime flow;
- schema separation of owner preferred time from confirmed scheduled time;
- cleanup deletion of unbound legacy route mutation bodies where compatibility adapters currently own runtime binding;
- synthetic backfill of pre-cutover Consultation events.

These are extension/cleanup items and do not invalidate the production-proven request/schedule/start/complete lifecycle.

## 10. Wave 2.2A closeout decision

Wave 2.2A is **production-proven and complete for the approved Consultation lifecycle slice**.

Issue #77 may be closed as completed.

The next Wave 2 chapter is **Issue #85 — Wave 2.2B: Vehicle Assessment lifecycle and canonical assessment events**.

Wave 2.2B must begin with an architecture/state/event contract before runtime mutation changes. It must preserve durable draft persistence, finalized professional-record immutability, active-owner report authorization and additive correction/addendum semantics established by Wave 2.2A production testing.
