# Aura Wave 2.3C — Treatment Action, Outcome and Database Contract

Issue: #106  
Parent Wave: #99  
Depends on: #102

## Purpose

This document locks the Wave 2.3C domain, database, authority, evidence and canonical-event contract before runtime routes are introduced.

Recommendation, owner authorization, professional intervention and observed outcome remain separate durable facts.

## 1. Domain records

### TreatmentPlan

The existing `TreatmentPlan` remains the professional care-pathway container. It is not an intervention line item and is not an outcome record.

### TreatmentAction

`TreatmentAction` is one concrete professional intervention under exactly one Treatment Plan and exactly one vehicle.

Required fields:

- `id`
- `treatment_plan_id`
- `car_id`
- `created_by_user_id`
- `title`
- `client_summary` — optional client-safe explanation
- `internal_instructions` — optional advisor-only execution context
- `status`
- `scheduled_for`
- `started_at`
- `completed_at`
- `deferred_at`
- `cancelled_at`
- `idempotency_key`
- `created_at`
- `updated_at`

The initial action-state vocabulary is:

- `planned`
- `scheduled`
- `in_progress`
- `completed`
- `deferred`
- `cancelled`

Primary flow:

`planned -> scheduled -> in_progress -> completed`

Allowed branches:

- `planned -> deferred`
- `scheduled -> deferred`
- `deferred -> scheduled`
- `planned -> cancelled`
- `scheduled -> cancelled`
- `deferred -> cancelled`

No generic reopen transition exists in Wave 2.3C.

### TreatmentOutcome

`TreatmentOutcome` is an additive, advisor-reviewed observation linked to one Treatment Plan and optionally one Treatment Action.

Required fields:

- `id`
- `treatment_plan_id`
- `car_id`
- optional `treatment_action_id`
- `recorded_by_user_id`
- `progression_direction`
- `client_summary`
- optional `internal_notes`
- `observed_at`
- `idempotency_key`
- `created_at`

Allowed progression directions are exactly:

- `improving`
- `stable`
- `deteriorating`
- `resolved`
- `insufficient_evidence`

Treatment Outcomes are append-only professional facts. Ordinary workflow must not update or delete a published outcome. A later observation creates another outcome.

## 2. Parent/vehicle scope contract

Every Treatment Action must belong to the same vehicle as its Treatment Plan.

Every Treatment Outcome must belong to the same vehicle as its Treatment Plan. If an outcome references a Treatment Action, that action must belong to the same Treatment Plan and vehicle.

Runtime services must validate these relationships before mutation.

PostgreSQL must additionally enforce same-vehicle parentage with composite foreign-key contracts where practical:

- `(treatment_plan_id, car_id)` on `treatment_actions` references `(id, car_id)` on `treatment_plans`;
- `(treatment_plan_id, car_id)` on `treatment_outcomes` references `(id, car_id)` on `treatment_plans`;
- an outcome action reference must be verified to match the same plan and vehicle before insertion.

The migration may add the supporting unique `(id, car_id)` index/constraint to `treatment_plans`. This is additive and does not alter historical plan rows.

## 3. Treatment Plan state prerequisites

New Treatment Actions may be created only under a real future/active Treatment Plan state:

- `authorized`
- `scheduled`
- `in_progress`
- `monitoring`
- legacy `approved` for compatibility with an existing real plan

Actions must not be created under:

- `proposed`
- `completed`
- `deferred`
- `cancelled`

This prevents a professional intervention record from appearing before required authorization or after a terminal/deferred pathway.

Action scheduling may occur while the parent plan is `authorized`, `scheduled`, `in_progress`, `monitoring`, or legacy `approved`.

Action start requires the parent Treatment Plan to be actively executing (`in_progress` or `monitoring`). A route/coordinator that begins plan execution and an action together must do both inside one transaction.

Action completion never completes the parent Treatment Plan automatically.

## 4. Authority contract

Treatment Action mutation is advisor-only.

Only an actor whose vehicle authority resolves to advisor/administrator may:

- create an action;
- schedule it;
- start it;
- complete it;
- defer it;
- cancel it;
- record a Treatment Outcome.

Owners may view client-safe action/outcome facts but may not create professional intervention facts or record outcomes.

Drivers may not mutate Treatment Actions or Treatment Outcomes.

Rina, providers and system automation may not create or mutate professional Treatment Actions or record Treatment Outcomes.

## 5. Canonical Treatment Action event family

Subject type: `treatment_action`

Events:

- `treatment_action.created`: `None -> planned`
- `treatment_action.scheduled`: `planned|deferred -> scheduled`
- `treatment_action.started`: `scheduled -> in_progress`
- `treatment_action.completed`: `in_progress -> completed`
- `treatment_action.deferred`: `planned|scheduled -> deferred`
- `treatment_action.cancelled`: `planned|scheduled|deferred -> cancelled`

All Treatment Action lifecycle events use:

- actor type `user`;
- advisor authority;
- `progression_direction="not_applicable"`;
- client visibility with deliberately minimal safe payload unless a later contract explicitly narrows visibility;
- deterministic idempotency;
- `subject_id` equal to the persisted TreatmentAction id.

No action event may imply health improvement, concern resolution, or outcome success.

## 6. Canonical outcome event

`treatment.outcome_recorded` remains a Treatment Plan event:

- `subject_type="treatment_plan"`
- `subject_id=<plan id>`
- advisor actor only
- `previous_state == new_state == current TreatmentPlan.status`
- progression direction equals the advisor-reviewed TreatmentOutcome direction
- event data contains the TreatmentOutcome id and optional TreatmentAction id
- `evidence_refs` contains the accepted evidence references supporting the outcome

`treatment.outcome_recorded` is the only initial treatment-family event permitted to use a progression direction other than `not_applicable`.

Recording an outcome does not mutate Treatment Plan state.

## 7. Evidence/provenance contract

Wave 2.3C reuses `VehicleEvidence` and `EvidenceLink`; it does not create a parallel attachment store.

The evidence subject vocabulary is extended additively with:

- `treatment_action`
- `treatment_outcome`

Treatment Action evidence linkage may document or support an intervention but is not required merely to create an action.

A Treatment Outcome requires at least one supporting evidence record before it can be published.

Every supporting evidence record must:

- belong to the same vehicle;
- have `review_status="accepted"`;
- have `storage_state="available"`;
- not be deleted/superseded for the asserted outcome;
- be linked through governed `EvidenceLink` rows in the same transaction as outcome publication;
- be represented in the canonical outcome event `evidence_refs`.

Provider extraction output is provenance/evidence input only. It cannot become a professional Treatment Outcome until an authorized advisor explicitly records the outcome.

## 8. Visibility contract

Owner-visible Treatment Action fields:

- title;
- client summary;
- lifecycle status;
- client-safe timestamps.

Never expose `internal_instructions` to owner/driver surfaces.

Owner-visible Treatment Outcome fields:

- progression direction;
- client summary;
- observed/recorded timestamp;
- only evidence already allowed by its own visibility contract.

Never expose outcome `internal_notes` to owner/driver surfaces.

## 9. Idempotency and transactions

Lifecycle services never commit independently.

For every mutation:

1. load/lock the target and parent scope;
2. prove advisor authority;
3. validate legal source state;
4. mutate the domain row;
5. flush;
6. emit the canonical event with a deterministic idempotency key;
7. caller commits the outer transaction.

If event emission fails, domain mutation rolls back.

Creation requires a caller-stable action `idempotency_key`. Replaying the same key with different semantics must fail closed.

Treatment Outcome publication uses a unique caller-stable idempotency key and must atomically create the outcome, evidence links and `treatment.outcome_recorded` event.

## 10. PostgreSQL contract

The Wave 2.3C migration must be additive from `c2f7a8e4d910` and must include:

- `treatment_actions` table;
- `treatment_outcomes` table;
- explicit non-null state/direction checks;
- action-state CHECK constraint;
- outcome progression CHECK constraint;
- parent/vehicle foreign keys and indexes;
- unique action/outcome idempotency keys;
- canonical VehicleEvent subject/event CHECK extension for `treatment_action` and `treatment.outcome_recorded`;
- treatment-action transition CHECK rules;
- treatment outcome event direction/state rules;
- supporting parent composite uniqueness if required for composite FKs.

PostgreSQL CHECK expressions must use explicit `IS NOT NULL` guards where null would otherwise evaluate to UNKNOWN and pass accidentally.

## 11. Migration rehearsal and rollback

CI must prove:

1. fresh PostgreSQL upgrade to the new head;
2. contract inspection;
3. downgrade to exact `c2f7a8e4d910` when no 2.3C action/outcome facts exist;
4. exact prior event constraints restored;
5. re-upgrade succeeds;
6. all older evidence, concern, consultation, assessment, correction and Treatment Plan event verifiers remain green.

Downgrade must refuse destructive rollback once TreatmentAction/TreatmentOutcome rows or canonical 2.3C events exist unless a separate explicit data-preservation migration is designed.

## 12. Historical integrity

No backfill is allowed from:

- generic legacy `VehicleEvent(event_type="service")` rows;
- editable `/treatments/.../records` legacy history;
- completed historical Treatment Plans;
- assessment treatment options.

Historical records remain readable as the facts they originally were.

## 13. Explicit non-goals

Wave 2.3C does not add:

- billing or invoice lines;
- parts/inventory semantics;
- automatic concern resolution;
- automatic Vehicle Health mutation;
- AI/provider outcome authority;
- inferred successful repair status;
- synthetic action/outcome history.

## Definition of done

Wave 2.3C is production-proven only when a real authorized plan can contain a real advisor-created Treatment Action, legal action transitions emit canonical events transactionally, accepted same-vehicle evidence supports an explicitly advisor-recorded Treatment Outcome, owner visibility is safe, and completion alone changes neither concern state nor Vehicle Health.