# Aura Wave 2.2A — Consultation Lifecycle Production Closeout

**Parent epic:** #74  
**Delivery issue:** #77  
**Status:** Production-proven and ready to close

## 1. Objective

Wave 2.2A moved Consultation lifecycle authority out of route-owned mutations and into an explicit domain service, while emitting canonical `consultation.*` VehicleEvents transactionally.

The production slice now distinguishes owner requests from advisor-confirmed schedules and preserves the rule that consultation completion depends on a finalized Vehicle Assessment.

## 2. Delivered lifecycle

The production consultation lifecycle is:

- owner request → `requested`;
- advisor confirmation/direct advisor booking → `scheduled`;
- advisor start → `in_progress`;
- advisor completion, only after finalized assessment → `completed`.

Canonical events implemented for this slice:

- `consultation.requested`
- `consultation.scheduled`
- `consultation.started`
- `consultation.completed`

All four use `subject_type="consultation"` and `progression_direction="not_applicable"` because they are workflow facts, not mechanical-health conclusions.

## 3. Production behavior proven

Production verification established that:

- owner booking creates a request rather than an advisor-confirmed appointment;
- advisor queue separates `requested` from `scheduled`;
- owner preferred time is presented as a preference until advisor confirmation;
- advisor can confirm the request, start the consultation and move it to `in_progress`;
- Aura blocks completion until a linked VehicleAssessment is finalized;
- once the assessment is finalized, consultation completion succeeds;
- after completion, the owner is able to request a new consultation;
- consultation mutation and canonical event emission roll back together on database failure.

## 4. Production defects discovered and resolved during verification

### 4.1 PostgreSQL canonical-event contract mismatch

The first production completion attempt failed because application-level consultation event semantics had been added before the production PostgreSQL `vehicle_events` CHECK constraints were extended to allow the `consultation.*` family.

Resolved in PR #81 with migration `e8f5c1a7b240` and PostgreSQL upgrade/downgrade verification.

No failed attempt partially completed the consultation; the transaction rolled back correctly.

### 4.2 Assessment draft persistence defect exposed by consultation verification

During the production consultation flow, repeating assessment form fields submitted with `[]` suffixes were not read by the legacy route. The route could therefore delete existing risk/treatment rows and persist an apparently successful but empty draft.

Resolved in PR #82 by the production-safe assessment draft cutover. The adapter:

- accepts canonical and bracketed repeating field names;
- validates complete parallel groups before destructive replacement;
- preserves existing child data on malformed/partial submissions;
- preserves absent scalar fields;
- uses Post/Redirect/Get so the next screen reloads persisted state.

This fix remains a compatibility bridge and is not the final Assessment lifecycle architecture.

### 4.3 Owner assessment report authorization and routing defects

Production testing also exposed two report-access defects:

- owner report links originally hit an advisor-protected route and returned 403;
- the first correction redirected to a client assessment blueprint that existed in source but was not registered in production, producing a Flask `BuildError`/500.

Resolved through PRs #83 and #84. The currently registered shared report route now applies explicit object-level authorization:

- advisors/admins may access finalized reports;
- active owners may access finalized reports for their own vehicle;
- unrelated users and inactive former owners are denied;
- draft assessments remain unavailable to owners.

All historical finalized reports tested by the active owner opened successfully after deployment.

## 5. Authority and privacy boundaries retained

- Rina/provider does not request, schedule, start, finalize or complete consultations.
- Owner authority is limited to requesting consultation for an actively owned vehicle.
- Advisor authority is required for schedule confirmation, start and completion.
- Internal advisor consultation summary is not copied into client-visible canonical event payloads.
- Notifications remain downstream of durable domain commit.
- No synthetic historical consultation events were backfilled.

## 6. Implementation trail

Wave 2.2A was delivered across the following production slices:

- PR #79 — Consultation lifecycle service + canonical consultation event contract
- PR #80 — Consultation route cutover + requested advisor queue
- PR #81 — PostgreSQL consultation VehicleEvent contract migration
- PR #82 — Assessment draft persistence hardening discovered during production flow
- PR #83 — Owner assessment report authorization correction
- PR #84 — Owner assessment report 500/routing correction and security regression gate

## 7. Closeout decision

Wave 2.2A is **production-proven** and may close.

Remaining consultation lifecycle states such as rescheduled/deferred/cancelled/reopened stay outside this completed slice unless a later operational requirement needs them.

The next approved Wave 2 chapter is **Wave 2.2B — Vehicle Assessment lifecycle and canonical assessment events**.
