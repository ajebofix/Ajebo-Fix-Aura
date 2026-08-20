# Aura Wave 2.1 — Care State and Canonical Event Contract

**Parent epic:** #74  
**Delivery issue:** #75  
**Status:** Architecture decision for review — no runtime implementation in this PR  
**Companion audit:** `docs/AURA_WAVE_2_1_CARE_LIFECYCLE_AUDIT.md`

## 1. Decision

Wave 2 will not create a parallel workflow engine or a second event ledger.

Aura will keep the existing domain models as the source of current state and extend the existing canonical `VehicleEvent` architecture for durable lifecycle history.

New care-domain transitions must move behind explicit services. Routes, templates, Rina and channel adapters may request transitions, but they may not own transition legality or canonical event construction.

## 2. Permanent transaction rule

For every migrated care-domain transition:

```text
validate actor + vehicle authority
        ↓
validate source state and domain preconditions
        ↓
apply domain mutation
        ↓
emit canonical event through services.event_emission
        ↓
flush / validate
        ↓
commit once
```

If canonical event emission fails, the domain mutation must roll back.

No notification, WhatsApp message, email or Rina response may be treated as the durable state transition itself.

## 3. Service ownership

Approved service boundaries for implementation:

- `ConsultationLifecycleService`
- `AssessmentLifecycleService`
- `TreatmentPlanLifecycleService`
- later `DriverObservationService`
- later dedicated priority workflow service only after a durable priority-request model is approved.

These names describe responsibilities, not mandatory filenames/classes. One small module per domain is preferred over a generic mega workflow engine.

## 4. Consultation lifecycle contract

### 4.1 Canonical states

Approved target states:

- `requested`
- `scheduled`
- `in_progress`
- `completed`
- `deferred`
- `cancelled`

Current production values `scheduled`, `in_progress`, `completed` remain valid.

### 4.2 Meaning

`requested`
: An owner has asked for professional consultation. No appointment/advisor commitment is implied.

`scheduled`
: Ajebo Fix has accepted/coordinated the consultation and a scheduled time is recorded.

`in_progress`
: An authorised advisor has begun the consultation. This remains Aura's professional-care unlock for assessment work.

`completed`
: The consultation has ended and its required care record has been completed.

`deferred`
: The consultation request remains known but is intentionally not scheduled/continued yet.

`cancelled`
: The consultation will not proceed under the current request.

### 4.3 Allowed transitions

| From | Action | To | Actor authority | Key preconditions |
|---|---|---|---|---|
| none | request | `requested` | owner | active ownership |
| none | advisor schedule directly | `scheduled` | advisor | active ownership, valid schedule |
| `requested` | schedule/accept | `scheduled` | advisor | valid schedule, advisor may be assigned |
| `requested` | defer | `deferred` | advisor | reason required |
| `requested` | cancel | `cancelled` | owner or advisor | reason captured where practical |
| `deferred` | schedule | `scheduled` | advisor | valid schedule |
| `deferred` | cancel | `cancelled` | owner or advisor | reason captured |
| `scheduled` | reschedule | `scheduled` | advisor | new schedule differs from prior schedule |
| `scheduled` | start | `in_progress` | advisor | consultation not already active/completed |
| `scheduled` | defer | `deferred` | advisor | reason required |
| `scheduled` | cancel | `cancelled` | owner or advisor | cannot silently erase history |
| `in_progress` | complete | `completed` | advisor | linked assessment exists and is finalized under current product rule |
| `in_progress` | defer | `deferred` | advisor | explicit reason; use sparingly |
| `completed` | reopen | `in_progress` | advisor | explicit reason; existing completion remains historically visible |
| `cancelled` | reopen | `requested` | owner or advisor | explicit new/reopen action; never silently reset |

Transitions not listed above are invalid and must fail closed.

### 4.4 Existing route correction

The current owner booking route creates `scheduled` immediately. Wave 2.2 should change owner booking to create `requested`.

Existing historical `scheduled` rows must **not** be rewritten to `requested` because Aura cannot prove which were client requests versus advisor-confirmed schedules.

### 4.5 Consultation event taxonomy

Approved canonical types:

- `consultation.requested`
- `consultation.scheduled`
- `consultation.rescheduled`
- `consultation.started`
- `consultation.completed`
- `consultation.deferred`
- `consultation.cancelled`
- `consultation.reopened`

Subject contract:

- `subject_type = "consultation"`
- `subject_id = Consultation.id`

### 4.6 Consultation progression direction

Consultation lifecycle events are operational/professional-care state, not mechanical-health conclusions.

Use:

- `not_applicable` for requested/scheduled/rescheduled/started/deferred/cancelled;
- `not_applicable` for completed/reopened as well unless a later progression rule explicitly derives vehicle-health meaning from separate evidence.

Completing a consultation is **not** equivalent to resolving a mechanical concern.

### 4.7 Consultation visibility

Default visibility:

- requested: `client`
- scheduled: `client`
- rescheduled: `client`
- started: `client`
- completed: `client`
- deferred: `client` with calm client-safe reason only
- cancelled: `client`
- reopened: `client`

Internal advisor notes/reasons must not be copied into client-visible event descriptions or payloads.

### 4.8 Consultation idempotency

Recommended key shape:

```text
consultation:{consultation_id}:{event}:{transition_token}
```

Where `transition_token` is deterministic from the durable transition fact, for example:

- creation: consultation row ID + created/requested timestamp;
- start: `started_at`;
- completion: `completed_at`;
- reschedule: previous scheduled time -> new scheduled time plus durable transition timestamp/revision identifier.

Implementation must not use random UUIDs as the sole idempotency key for a retryable domain transition.

## 5. Assessment lifecycle contract

### 5.1 Canonical states

Keep the existing states:

- `draft`
- `finalized`

Do not add a generic `reopened` assessment state in the first implementation.

### 5.2 Decision: finalization is a care-record boundary

A finalized assessment is treated as a durable professional record.

Ordinary editing after finalization is prohibited.

If an advisor needs to correct a finalized assessment:

- the original finalized assessment remains preserved;
- correction must be additive/audited;
- a future correction record/event may reference the finalized assessment;
- material new clinical work should normally occur through a new consultation/assessment rather than silently reopening the old document.

This preserves the current de-facto immutability already enforced by the draft-only edit route.

### 5.3 Assessment events

Approved initial types:

- `assessment.created`
- `assessment.finalized`
- later `assessment.corrected` after a durable correction model/record is approved.

Subject contract:

- `subject_type = "vehicle_assessment"`
- `subject_id = VehicleAssessment.id`

Progression direction:

- `not_applicable`

Visibility:

- creation: `advisor`
- finalization: `client` only with client-safe description/payload;
- corrections: explicit visibility based on whether the corrected fact is client-visible.

## 6. Treatment plan lifecycle contract

### 6.1 Separation from legacy treatment records

`TreatmentPlan` is the professional plan lifecycle.

The existing `/treatments/...` API stores generic legacy `VehicleEvent` treatment records. It is **not** the canonical `TreatmentPlan` state machine and must not be renamed into it or silently merged with it.

Treatment-action migration requires a separate compatibility decision in Wave 2.3.

### 6.2 Target states

Approved target states:

- `draft`
- `approved`
- `scheduled`
- `in_progress`
- `monitoring`
- `completed`
- `escalated`
- `cancelled`

Existing `deferred` rows/routes are legacy compatibility state and must be audited before normalization. Do not rewrite historical rows blindly.

### 6.3 High-level allowed transitions

| From | To | Authority |
|---|---|---|
| none | `draft` | advisor |
| `draft` | `approved` | advisor |
| `approved` | `scheduled` | advisor |
| `approved` | `in_progress` | advisor, only if scheduling is intentionally skipped and policy permits |
| `scheduled` | `in_progress` | advisor |
| `in_progress` | `monitoring` | advisor |
| `monitoring` | `in_progress` | advisor |
| `in_progress` | `completed` | advisor |
| `monitoring` | `completed` | advisor |
| `approved`/`scheduled`/`in_progress`/`monitoring` | `escalated` | advisor |
| active non-terminal states | `cancelled` | advisor |

Any shortcut must be explicit in the service contract and tested. Routes may not assign arbitrary target states.

### 6.4 Treatment plan event taxonomy

Approved target types:

- `treatment_plan.created`
- `treatment_plan.approved`
- `treatment_plan.scheduled`
- `treatment_plan.started`
- `treatment_plan.monitoring_started`
- `treatment_plan.completed`
- `treatment_plan.escalated`
- `treatment_plan.cancelled`

Subject contract:

- `subject_type = "treatment_plan"`
- `subject_id = TreatmentPlan.id`

Progression direction:

- default `not_applicable`;
- do not infer `improving`, `deteriorating` or `resolved` merely because a plan changed state;
- actual concern/vehicle progression must come from evidence-backed progression rules.

Visibility:

- client-visible status milestones may use `client`;
- internal plan deliberation/instructions use `advisor` or `internal` and must not be copied into client payloads.

## 7. Driver observation contract

### 7.1 Driver concern reports

A driver-reported issue stored as `CarFault` is already part of the Reported Concern domain.

Decision:

**Do not emit a second driver-specific concern event.**

Use the existing:

- `concern.reported`
- actor authority = `driver`
- persisted `CarFault.source = "driver"`.

This avoids duplicate facts in the canonical ledger.

### 7.2 Driver check-ins

Future approved family:

- `driver_observation.checkin_recorded`

Subject contract:

- `subject_type = "driver_checkin"`
- `subject_id = DriverCheckIn.id`

Progression direction:

- `insufficient_evidence` or `not_applicable` depending on final implementation rule;
- a warning flag alone must not be classified as mechanical deterioration.

Initial visibility recommendation:

- `advisor` for canonical event payloads until an owner-facing driver-check-in visibility policy is explicitly reviewed.

Do not place unrestricted free-text driver notes into broad client-visible event payloads.

### 7.3 Driver score

`User.driver_score` is not part of the canonical observation contract.

Wave 2.4 must decide whether to retire, replace or isolate it. The event migration must not make gamified score changes a permanent intelligence feature by accident.

## 8. Priority workflow contract

Current priority scheduling is encoded as a `Consultation` plus notes, and no dedicated priority-request model exists.

Decision for Wave 2.1:

- do not create `priority.*` canonical events without a durable priority subject;
- priority scheduling that results in a consultation request should use the consultation lifecycle/event contract, with source/provenance indicating priority entry;
- a dedicated priority state machine may be introduced only in Wave 2.4 after its data model, authority and queue semantics are approved.

Care-plan entitlement state (`CarOwnership.care_plan`) remains separate from a priority request.

## 9. Authority matrix

### Owner

May:

- request a consultation;
- cancel an eligible pre-start consultation subject to policy;
- view client-safe consultation/assessment/treatment state for owned vehicles.

May not:

- start/complete consultations;
- finalize assessments;
- approve/start/complete treatment plans;
- access internal instructions or advisor-only reasoning.

### Driver

May:

- submit driver observations/check-ins for assigned vehicles;
- report concerns for assigned vehicles.

May not:

- schedule/start/complete professional consultation state;
- finalize assessment;
- approve treatment;
- access owner financial/private advisor information.

### Advisor

May perform professional lifecycle transitions within proven vehicle scope.

Current `security.access` represents advisor authority through the active admin identity model. Wave 2 must reuse that policy until a separate advisor identity migration is explicitly approved.

### Rina / AI provider

May explain available state, structure a request and recommend escalation.

May not:

- approve, start, finalize, complete, cancel or reopen professional care state autonomously;
- bypass human authority checks;
- manufacture event history.

## 10. Event-emitter extension rules

`services.event_emission` remains the only canonical event constructor.

Wave 2 extensions must:

1. add new event names to controlled code taxonomy;
2. add subject-type rules;
3. add permitted progression-direction rules;
4. define transition requirements for previous/new state;
5. keep payload size and sensitive-key protections;
6. retain authority resolution through `security.access`;
7. keep caller-owned commit semantics;
8. add PostgreSQL idempotency/concurrency tests;
9. add cross-vehicle/authority tests;
10. avoid independent commits inside event emission.

## 11. Service versus session-listener decision

Reported Concerns currently use SQLAlchemy session listeners because multiple legacy routes already mutate `CarFault` and Wave 1.2 needed complete transactional coverage without rewriting every route at once.

For new Wave 2 care lifecycles, the preferred architecture is different:

- **explicit lifecycle services own the state mutation**;
- those services call `emit_vehicle_event` directly within the same transaction;
- routes become thin adapters.

Do not introduce hidden session listeners for consultation/assessment/treatment as the default implementation pattern.

Reason:

- transition legality belongs in one explicit service;
- care transitions have richer preconditions than simple status observation;
- tests should call the domain service directly;
- explicit services make actor/reason/notification intent easier to audit.

## 12. Notification boundary

Current consultation booking commits before WhatsApp notification and catches notification failure.

Target rule:

- domain transition commits independently of channel delivery success;
- channel delivery occurs only after the durable transition exists;
- delivery failure must not roll back a valid care-state transition;
- notification attempts/failures need their own communication audit path, not mutation of domain state;
- no channel provider owns lifecycle state.

A future outbox may improve reliability, but Wave 2.2 does not need an outbox merely to begin consultation migration.

## 13. Historical migration policy

Existing state rows remain authoritative for their current state.

Canonical event backfill is permitted only when actor/time/source can be derived deterministically.

Examples:

- an existing `Consultation.completed_at` proves that a completion time was recorded, but may not prove which actor completed it if no actor field exists;
- an old `TreatmentPlan.status="approved"` does not prove a separate draft->approved transition occurred;
- an existing `scheduled` consultation does not prove whether it began as a client request or advisor-created schedule.

Therefore the default Wave 2 backfill posture is **no speculative event generation**.

## 14. First implementation slice — Wave 2.2A

Approved first slice:

**Consultation lifecycle service + canonical consultation events.**

Scope:

- add consultation event taxonomy to `services.event_emission`;
- add `ConsultationLifecycleService` (or equivalent domain service);
- move owner request, advisor schedule, start and complete mutations behind the service;
- introduce `requested` state for new owner requests;
- preserve existing historical state values;
- emit events transactionally;
- keep `require_active_consultation` semantics for `in_progress`;
- leave assessment/treatment implementation for later PRs;
- no predictive logic;
- no UI redesign beyond status wording required for the new request state.

Required tests:

- owner can request only for owned active vehicle;
- unrelated owner/driver cannot request/transition another vehicle;
- advisor can schedule/start/complete within policy;
- illegal state transitions fail without partial mutation;
- completion fails without finalized assessment;
- domain mutation rolls back when canonical event emission fails;
- repeated transition/idempotent request does not duplicate canonical events;
- visibility is client-safe;
- existing `scheduled` rows remain readable;
- PostgreSQL migration upgrade/downgrade rehearsals pass for any schema change.

## 15. Wave 2.1 definition of done

Wave 2.1 is architecture-complete when this contract is reviewed and approved together with the current-state audit, and Wave 2.2 can begin without guessing about:

- state ownership;
- legal transitions;
- actor authority;
- visibility;
- canonical event names;
- transaction semantics;
- historical backfill;
- first implementation slice.
