# Aura Wave 2.3C — TreatmentAction Database and Event Contract

Issue: #106  
Parent Wave: #99  
Depends on production-proven Treatment Plan lifecycle: #102

## Contract status

Locked for Wave 2.3C implementation unless production evidence exposes a contradiction that requires an explicit amendment.

## Core rule

Aura records four different facts separately:

1. **Treatment Plan** — professional recommendation/pathway.
2. **Owner authorization** — consent to proceed where required.
3. **Treatment Action** — concrete professional intervention activity.
4. **Treatment Outcome** — advisor-reviewed evidence/professional observation after intervention.

No completion flag may silently stand in for an outcome.

---

## `treatment_actions` table contract

One row is one concrete professional intervention inside one Treatment Plan.

Required fields:

- `id` — primary key;
- `treatment_plan_id` — required FK to `treatment_plans.id`;
- `car_id` — required FK to `cars.id`;
- `created_by_user_id` — required FK to `users.id`;
- `creation_key` — required stable idempotency key, unique within a Treatment Plan;
- `title` — required professional action title;
- `status` — required canonical state;
- `visibility` — required `client` or `advisor` classification;
- `created_at` / `updated_at`.

Optional fields:

- `client_summary` — client-safe explanation;
- `internal_instructions` — advisor-only execution notes;
- `scheduled_for`;
- `started_at`;
- `completed_at`;
- `deferred_at`;
- `cancelled_at`.

### Canonical action states

- `planned`
- `scheduled`
- `in_progress`
- `completed`
- `deferred`
- `cancelled`

### State machine

Primary:

`planned -> scheduled -> in_progress -> completed`

Branches:

- `planned -> deferred`
- `planned -> cancelled`
- `scheduled -> deferred`
- `scheduled -> cancelled`
- `deferred -> scheduled`
- `deferred -> cancelled`

Terminal:

- `completed`
- `cancelled`

Ordinary workflow does not reopen a completed/cancelled action.

### Parent-plan compatibility

Action state does not mutate the Treatment Plan automatically.

Service-level parent guards:

- create `planned`: allowed only on non-terminal Treatment Plans;
- schedule action: parent Treatment Plan must be `scheduled` or `in_progress`;
- start action: parent Treatment Plan must be `in_progress`;
- complete action: parent Treatment Plan must be `in_progress` or `monitoring`;
- defer/cancel: action transition must be legal and parent plan must not be terminal.

Legacy `approved` Treatment Plans may contain newly created `planned` actions for real future work; no historical actions are backfilled.

### Scope rule

`TreatmentAction.car_id` must equal its parent `TreatmentPlan.car_id`.

This is enforced by the lifecycle service and covered by PostgreSQL/integration tests. A mismatched cross-vehicle action must fail closed.

### Idempotency

`(treatment_plan_id, creation_key)` is unique.

Replaying the same creation key with identical semantics returns the existing action. Replaying the same key with different title/visibility/client/internal semantics fails closed.

---

## `treatment_outcomes` table contract

One row is one additive advisor-reviewed outcome observation. Outcome rows are never overwritten in place to make history look cleaner.

Required fields:

- `id` — primary key;
- `treatment_plan_id` — required FK;
- `car_id` — required FK;
- `recorded_by_user_id` — required advisor FK;
- `recording_key` — required stable idempotency key, unique within a Treatment Plan;
- `progression_direction`;
- `summary`;
- `visibility` — `client` or `advisor`;
- `observed_at`;
- `provenance_kind`;
- `created_at`.

Optional:

- `treatment_action_id` — nullable FK when the outcome is action-specific;
- `advisor_note` — advisor-only supporting interpretation;
- `provenance_data` — minimal structured provenance for an accepted professional observation source.

### Allowed progression directions

- `improving`
- `stable`
- `deteriorating`
- `resolved`
- `insufficient_evidence`

### Provenance kinds

- `reviewed_evidence`
- `professional_observation`
- `insufficient_evidence`

Rules:

- progression other than `insufficient_evidence` may not use `provenance_kind="insufficient_evidence"`;
- `professional_observation` requires explicit minimal provenance data;
- `reviewed_evidence` requires one or more accepted, available, same-vehicle `VehicleEvidence` links to the persisted outcome before the transaction commits;
- `insufficient_evidence` may be recorded without evidence and must not be presented as improvement/resolution.

### Scope rule

If `treatment_action_id` is present, the action must belong to the same Treatment Plan and vehicle as the outcome.

---

## Evidence linkage contract

Existing `VehicleEvidence` and `EvidenceLink` remain the only evidence storage/linking mechanism.

Wave 2.3C adds evidence subject types:

- `treatment_action`
- `treatment_outcome`

Only advisor-accepted, available evidence may support a professional Treatment Outcome.

Evidence and target action/outcome must belong to the same vehicle.

Raw media bytes, extraction payloads and unrestricted provider output are never copied into TreatmentAction/TreatmentOutcome rows or canonical event payloads.

---

## Treatment Action event contract

Subject type: `treatment_action`

Canonical family:

- `treatment_action.created`
- `treatment_action.scheduled`
- `treatment_action.started`
- `treatment_action.completed`
- `treatment_action.deferred`
- `treatment_action.cancelled`

All are advisor/administrator events and all use `progression_direction="not_applicable"`.

### `treatment_action.created`

- previous: `None`
- new: `planned`

### `treatment_action.scheduled`

- previous: `planned` or `deferred`
- new: `scheduled`

### `treatment_action.started`

- previous: `scheduled`
- new: `in_progress`

### `treatment_action.completed`

- previous: `in_progress`
- new: `completed`

Completion means the specific work was recorded complete. It does not imply that the Treatment Plan is complete or that the vehicle improved.

### `treatment_action.deferred`

- previous: `planned` or `scheduled`
- new: `deferred`

### `treatment_action.cancelled`

- previous: `planned`, `scheduled` or `deferred`
- new: `cancelled`

---

## `treatment.outcome_recorded` event contract

Subject type remains `treatment_plan`.

`TreatmentOutcome` is the durable outcome row; the canonical event points back to the parent Treatment Plan and carries the outcome row id.

Rules:

- actor: advisor/administrator only;
- previous/new Treatment Plan state are equal;
- allowed plan states for initial Wave 2.3C outcome recording: `in_progress`, `monitoring`, `completed`;
- progression direction is one of the five Treatment Outcome values;
- event data may include `outcome_id` and optional `treatment_action_id`;
- event `evidence_refs` contains only governed VehicleEvidence identifiers that were accepted for the outcome;
- event visibility mirrors the outcome visibility;
- automatic Treatment Plan/Action completion code may never emit this event.

---

## Canonical event payload minimization

Action event data may contain only minimal linkage facts such as:

- `treatment_plan_id`;
- action visibility classification;
- scheduled timestamp where applicable.

Outcome event data may contain:

- `outcome_id`;
- optional `treatment_action_id`;
- provenance classification.

Do not include:

- `internal_instructions`;
- `advisor_note`;
- unrestricted evidence text/media;
- provider extraction text;
- prompts/model reasoning;
- secrets/tokens.

---

## Authority contract

Advisor/administrator only:

- create Treatment Actions;
- schedule/start/complete/defer/cancel Treatment Actions;
- record Treatment Outcomes;
- link reviewed evidence to Treatment Actions/outcomes.

Owner:

- view client-visible Treatment Actions/outcomes for actively owned vehicles;
- no professional action mutation;
- no professional outcome recording.

Driver:

- no Treatment Action/outcome mutation.

Rina/provider/system:

- no professional intervention/outcome mutation.

---

## Transaction boundary

`TreatmentActionLifecycleService` and the future outcome-recording service never commit independently.

The outer coordinator owns the transaction so each operation commits or rolls back together with its canonical event and evidence links.

Examples:

- Action state mutation + `treatment_action.*` event;
- TreatmentOutcome row + EvidenceLink rows + `treatment.outcome_recorded` event.

---

## PostgreSQL migration contract

The Wave 2.3C migration must:

1. create `treatment_actions` with state/visibility/idempotency constraints and required indexes/FKs;
2. create `treatment_outcomes` with progression/provenance/visibility/idempotency constraints and required indexes/FKs;
3. extend `ck_vehicle_events_canonical_subject_event` for `treatment_action` events and `treatment.outcome_recorded`;
4. extend the Treatment Plan event CHECK so `treatment.outcome_recorded` is legal only with unchanged current plan state and an allowed progression direction;
5. add a dedicated Treatment Action VehicleEvent CHECK with explicit non-null guards so PostgreSQL `UNKNOWN` cannot pass malformed transitions;
6. preserve every existing Concern/Evidence/Consultation/Assessment/Treatment Plan event contract;
7. preflight incompatible rows before replacing constraints;
8. downgrade to the exact Wave 2.3B database contract only when no Wave 2.3C professional history exists;
9. rehearse upgrade -> downgrade -> re-upgrade in PostgreSQL CI.

---

## No synthesis / no implicit progression

Migration and deployment must not create:

- historical TreatmentAction rows;
- historical TreatmentOutcome rows;
- historical `treatment_action.*` events;
- historical `treatment.outcome_recorded` events.

Neither Treatment Action completion nor Treatment Plan completion may automatically:

- resolve a Reported Concern;
- change Vehicle Health;
- record `improving`/`resolved`;
- create an outcome.

Those remain separate professional facts under their own contracts.
