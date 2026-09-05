# Aura Wave 2.4 — Priority, Care Signal and Driver Observation State/Event Contract

Issue: #108  
Parent epic: #74  
Workflow backlog: #3  
Companion audit: `docs/AURA_WAVE_2_4A_OPERATIONAL_SIGNAL_AUDIT.md`  
Status: locked architecture contract before runtime implementation

## 1. Permanent rule

Wave 2.4 will not turn every derived score, queue projection or Rina recommendation into a durable event.

Canonical events must point to durable domain subjects that represent real operational facts.

The approved durable subjects for Wave 2.4 are:

- existing `DriverCheckIn` for driver observations;
- existing `VehicleHealthAlert` for care-signal occurrences;
- a new durable `PriorityRequest` (or equivalently named model) before any `priority.*` event family is introduced.

Existing Reported Concern, Consultation and Treatment Plan subjects keep their existing canonical families. Wave 2.4 must link to them rather than duplicate them.

## 2. Transaction rule

For every Wave 2.4 mutation:

```text
validate actor + vehicle authority
        ↓
validate durable subject/state preconditions
        ↓
apply domain mutation
        ↓
emit canonical VehicleEvent
        ↓
flush / validate
        ↓
commit once in outer coordinator
```

No lifecycle service may commit independently.

No Rina response, alert-center projection, notification, priority score or feature entitlement is itself the durable transition.

## 3. Driver observation contract

### 3.1 Durable subject

Use existing `DriverCheckIn`.

Canonical subject:

- `subject_type="driver_checkin"`
- `subject_id=DriverCheckIn.id`

### 3.2 Creation semantics

A check-in is an additive observation record, not a mutable health-state lifecycle.

Canonical event:

- `driver_observation.checkin_recorded`

Transition semantics:

- previous state: `None`
- new state: `recorded`

Progression direction:

- `insufficient_evidence`

Reason: tyre warning, dashboard light, vibration, unusual sound, fuel-low state and driver free text are observations. They are not sufficient professional evidence to classify mechanical deterioration or resolution.

### 3.3 Authority

Allowed actor:

- currently assigned active driver for the vehicle.

Not allowed:

- owner submitting as if they were the assigned driver;
- unrelated user;
- Rina/provider/system fabricating a driver observation;
- advisor backdating a driver observation as though submitted by the driver.

Advisor may later add separate professional records through the appropriate existing domain.

### 3.4 Visibility

Initial canonical event visibility: `advisor`.

Owner-facing check-in projection may be added only after a client-safe field policy is explicit.

Unrestricted `DriverCheckIn.notes` must not be copied into client-visible canonical event payloads.

The canonical payload may include only bounded structured observations such as boolean warning flags and an optional safe note-presence indicator, not unrestricted free text.

### 3.5 Idempotency and operational day

Wave 2.4 implementation must persist an explicit operational day/date or an equivalent database-safe key.

Required uniqueness contract:

```text
(driver_id, car_id, operational_date)
```

A repeated identical submission for the same operational date must return the persisted check-in/event idempotently.

A conflicting second submission for the same operational date must fail closed rather than silently overwrite the first observation.

### 3.6 Driver score

`User.driver_score` is not canonical vehicle intelligence.

Wave 2.4 implementation must stop the check-in lifecycle from mutating this score as part of the durable observation transaction.

Historical values are not rewritten. The column may remain for compatibility until a separate cleanup decides whether to remove it.

### 3.7 Driver-reported concern

No new event family.

Driver issue reporting continues to create a Reported Concern and uses:

- `concern.reported`;
- actor authority `driver`;
- `CarFault.source="driver"`.

This avoids double-counting one real-world report.

## 4. Care Signal / VehicleHealthAlert contract

### 4.1 Durable subject

Keep existing `VehicleHealthAlert` as the durable care-signal occurrence.

Canonical subject:

- `subject_type="vehicle_health_alert"`
- `subject_id=VehicleHealthAlert.id`

Do not create a second care-alert table.

### 4.2 Canonical occurrence states

Wave 2.4 canonical semantic states:

- `new`
- `acknowledged`
- `resolved`

`is_active` remains a compatibility/storage field during migration, but service legality is defined by the canonical semantic state.

Required consistency:

- `new` => active;
- `acknowledged` => active;
- `resolved` => inactive with `resolved_at`.

### 4.3 Allowed transitions

```text
none -> new
new -> acknowledged
new -> resolved
acknowledged -> resolved
```

A resolved signal is terminal as one occurrence.

If the same signal condition later genuinely recurs, create a **new VehicleHealthAlert occurrence** and a new `care_signal.raised` event. Do not reopen or rewrite the resolved row.

This preserves recurrence history honestly.

### 4.4 Canonical events

Approved family:

- `care_signal.raised`
- `care_signal.acknowledged`
- `care_signal.resolved`

Transitions:

`care_signal.raised`
- previous: `None`
- new: `new`

`care_signal.acknowledged`
- previous: `new`
- new: `acknowledged`

`care_signal.resolved`
- previous: `new|acknowledged`
- new: `resolved`

Progression direction for all three:

- `not_applicable`

A care-signal lifecycle event is an operational monitoring fact, not proof that vehicle health improved or deteriorated.

### 4.5 Signal types and severity

Existing alert types may continue where supported by real rule inputs, including current care-signal categories such as:

- low health status;
- declining health trajectory;
- maintenance monitoring.

The current `elevated_risk_indicator` implementation is explicitly flagged for review because it searches risk-reason text for the word `predicted`. Wave 2.4 must not preserve a pseudo-predictive rule merely because it exists today. It must either be grounded in approved non-predictive inputs or disabled/renamed before canonicalization.

Severity (`low|moderate|high|critical` where supported) is classification metadata, not lifecycle state.

### 4.6 Actor authority

`care_signal.raised` may be produced by:

- approved deterministic Aura rule evaluation (`actor_type="system"`), or
- an authorized advisor where a manual signal type is explicitly supported.

`care_signal.acknowledged`:

- advisor/administrator only.

`care_signal.resolved` may be produced by:

- deterministic rule evaluation when the exact originating rule condition no longer holds; or
- advisor/administrator manual resolution where policy permits.

System actors may never use care-signal events to assert diagnosis, treatment approval or concern resolution.

### 4.7 System actor migration requirement

Current canonical event code reserves `system/provider` actors because the legacy event schema expects a human actor field.

Wave 2.4 implementation must explicitly extend the canonical event/database contract to permit `actor_type="system"` with no `actor_user_id` **only for approved deterministic system event families**.

Do not fake a human advisor actor for automatically raised/resolved care signals.

Provider actors remain prohibited from professional/care-signal mutation unless a separate contract explicitly approves them.

### 4.8 Visibility

Default:

- raised: `client` when message is calm and client-safe;
- acknowledged: `advisor` by default; client visibility only if acknowledgement itself is a useful client-facing fact;
- resolved: `client` with client-safe wording.

Never include unrestricted rule internals, model/provider output, prompts or advisor reasoning in client payloads.

### 4.9 Alert Center projections

The following current Alert Center items remain projections unless separately persisted by an approved domain model:

- recurring-concern projection;
- consultation-delay projection;
- monitoring-stall projection.

They do not receive `care_signal.*` events merely because they are displayed in an advisor queue.

## 5. Monitoring contract

Wave 2.4 does **not** introduce a generic monitoring state machine.

The word monitoring retains domain-local meanings:

- concern monitoring -> existing concern lifecycle/event family;
- treatment monitoring -> existing treatment lifecycle/event family;
- care signal -> `VehicleHealthAlert` occurrence lifecycle;
- priority queue -> future PriorityRequest lifecycle;
- Rina `monitor` -> advisory projection only;
- `active_monitoring` care plan -> entitlement only.

Cross-domain dashboards may aggregate these facts, but aggregation does not create new lifecycle state.

## 6. Priority Request contract

### 6.1 Durable subject required

Wave 2.4 approves creation of a dedicated durable priority request subject before implementing priority mutation routes/events.

Recommended model name: `PriorityRequest`.

Canonical subject:

- `subject_type="priority_request"`
- `subject_id=PriorityRequest.id`

A derived priority score, care-plan entitlement or Rina escalation output cannot substitute for this row.

### 6.2 Required durable fields

Minimum contract:

- `id`;
- `car_id`;
- `ownership_id` snapshot/reference;
- `requested_by_user_id`;
- `request_source` (`owner`, `advisor`, `rina_structured_request` where explicitly confirmed by owner/advisor);
- `request_kind` (`priority`, `emergency_review`);
- `status`;
- client-safe `reason_summary`;
- optional advisor-only review note kept out of client events;
- eligibility snapshot showing whether priority entitlement existed at request time;
- bounded health-context snapshot/reference, not unrestricted health calculation internals;
- timestamps for requested/reviewed/accepted/deferred/resolved/cancelled as applicable;
- reviewing/resolving advisor attribution;
- stable idempotency/request key.

### 6.3 Canonical states

Approved initial states:

- `requested`
- `under_review`
- `accepted`
- `deferred`
- `resolved`
- `cancelled`

`request_kind="emergency_review"` is urgency classification, not a separate lifecycle state.

### 6.4 Allowed transitions

Primary:

```text
none -> requested -> under_review -> accepted -> resolved
```

Branches:

```text
requested -> cancelled
requested -> deferred
under_review -> deferred
under_review -> cancelled
accepted -> resolved
deferred -> under_review
deferred -> cancelled
```

No automatic transition from derived priority band or Rina escalation output.

### 6.5 Authority

Owner:

- may create a priority request for an actively owned vehicle when server-side entitlement/policy permits;
- may cancel before advisor acceptance subject to policy;
- may view client-safe status.

Advisor/administrator:

- may create a professional priority request where justified;
- may move requested/deferred items into review;
- may accept, defer, resolve or cancel according to the state contract;
- remains final authority for queue handling.

Driver:

- may not create/accept/resolve an owner Priority Access request merely because they are assigned to the vehicle;
- may submit check-ins and Reported Concerns; those facts may inform advisor review.

Rina/system/provider:

- may explain eligibility and suggest/escalate to a structured request;
- Rina may prepare/request creation only after explicit owner/advisor intent is captured through an approved route;
- may not accept, defer, resolve or fabricate professional priority decisions.

### 6.6 Entitlement versus workflow

`CarOwnership.care_plan="priority_access"` and feature gateways answer **whether a capability is available**.

They do not mean a request exists and do not emit `priority.*` events.

Eligibility must be checked server-side at request creation and captured as a request-time snapshot so later plan changes do not rewrite historical eligibility.

### 6.7 Priority event family

Approved once the durable `PriorityRequest` model exists:

- `priority.requested`
- `priority.review_started`
- `priority.accepted`
- `priority.deferred`
- `priority.resolved`
- `priority.cancelled`

Progression direction:

- `not_applicable` for every initial priority lifecycle event.

Priority resolution means the operational priority request has been concluded. It does not mean a Reported Concern is resolved or vehicle health improved.

### 6.8 Relationship to Consultation

A PriorityRequest may result in or link to a Consultation, but the two lifecycles remain separate.

Example:

```text
priority.requested
    -> priority.accepted
    -> consultation.requested/scheduled
```

Do not replace the priority request with a Consultation note, and do not duplicate Consultation events as priority events.

The link should store a `consultation_id` only when an actual consultation is created/associated.

## 7. Priority scoring contract

`PriorityScoringEngine` remains a derived advisor-support projection, not a durable workflow.

Before reuse in Wave 2.4 runtime it must be reconciled with production Wave 2.3 Treatment Plan states and reviewed for whether each input is clinically/product appropriate.

Rules:

- score/band changes do not emit canonical events;
- score/band cannot auto-create `priority.accepted`;
- score/band may help order an advisor queue only;
- a queue explanation should identify underlying durable facts rather than present the score as diagnosis.

## 8. Rina escalation contract

`RinaEscalationEngine` outputs `monitor|flag|escalate` as guidance only.

These values are not persistent vehicle states and do not become canonical events.

If a user explicitly asks for professional escalation, Rina may route that intent into the approved PriorityRequest or Consultation creation path while preserving the human request source and authority.

Rina cannot silently convert its own `escalate` classification into a durable accepted priority request.

## 9. Event payload minimization

### Driver observation event may include

- structured warning booleans;
- operational date;
- source classification;
- no unrestricted note text.

### Care signal event may include

- alert type;
- severity;
- bounded rule/source classification;
- no unrestricted health/risk-reason arrays.

### Priority event may include

- request kind;
- eligibility-at-request boolean/classification;
- bounded urgency/source classification;
- linked consultation id where applicable;
- no advisor-only review notes or unrestricted health context.

## 10. No implicit health progression

None of these operations may automatically:

- resolve/reopen a Reported Concern;
- change Vehicle Health status;
- classify `improving`, `deteriorating` or `resolved`;
- complete a Consultation/Treatment Plan;
- create a Treatment Outcome.

Any such fact must come from its own approved domain and evidence/professional authority contract.

## 11. Historical migration policy

No speculative backfill.

Existing DriverCheckIn rows, VehicleHealthAlert rows, derived priority scores and Rina escalation outputs remain readable historical data but do not automatically receive canonical events.

A later migration may backfill only a fact whose actor, occurred-at timestamp, source and semantics are deterministic. The default remains no backfill.

## 12. PostgreSQL requirements for implementation slices

Later runtime/schema PRs must add/rehearse as applicable:

- explicit DriverCheckIn operational-date uniqueness/idempotency;
- recurrence-safe VehicleHealthAlert constraints;
- canonical `VehicleEvent` subject/event/transition CHECKs for driver observations and care signals;
- safe system-actor event contract;
- PriorityRequest table/state/FK/idempotency constraints before `priority.*` events;
- upgrade -> downgrade -> re-upgrade rehearsal;
- downgrade refusal when destructive rollback would erase published Wave 2.4 history.

All CHECKs must use explicit non-null guards where PostgreSQL `UNKNOWN` could otherwise admit malformed canonical rows.

## 13. Proposed implementation slices

### Wave 2.4B — Driver observations

- `DriverObservationService`;
- operational-date schema/uniqueness;
- remove `driver_score` mutation from check-in transaction;
- `driver_observation.checkin_recorded`;
- route cutover and role/object tests;
- no duplicate driver concern events.

### Wave 2.4C — Care signals / alert lifecycle

- recurrence-safe `VehicleHealthAlert` contract;
- `CareSignalLifecycleService`;
- `care_signal.raised|acknowledged|resolved`;
- explicit system actor support;
- rule-engine/advisor route cutover;
- consolidate mutation authority before read-surface cleanup.

### Wave 2.4D — Priority Request lifecycle

- durable PriorityRequest schema;
- server-side eligibility snapshot;
- request/review/accept/defer/resolve/cancel service;
- canonical `priority.*` events;
- advisor queue and client-safe status;
- Consultation linkage;
- Rina may structure explicit user intent but never own professional decisions.

### Wave 2.4E — Operational projection cleanup / production closeout

Only if needed after B/C/D are production-proven:

- reconcile duplicate alerts/notices read routes;
- align PriorityScoringEngine with current states or retire it;
- ensure Alert Center clearly distinguishes durable signals from computed queue projections;
- production longitudinal coverage smoke across driver observation + care signal + priority request.

## 14. Acceptance gates across Wave 2.4

- object-level owner/driver/advisor/unrelated role matrix;
- state changes live in services, not routes/templates/Rina;
- domain mutation + event atomicity;
- deterministic idempotency;
- cross-vehicle and cross-ownership isolation;
- no synthetic history;
- client-safe/advisor-only visibility separation;
- system actor narrowly permitted and tested;
- invalid transitions fail closed;
- derived scores/guidance never masquerade as durable facts;
- production smoke on genuine operational activity only.
