# Aura Wave 2.3 — Treatment State and Event Contract

Issue: #99  
Contract slice: #100

## Contract status

Locked for Wave 2.3B/C implementation unless a production-discovered contradiction requires an explicit contract amendment.

## Core principle

Aura must record recommendation, owner authorization, treatment execution and observed outcome as separate facts.

No one state or event may silently stand in for another.

## Treatment Plan states

Canonical states for newly created plans:

- `proposed`
- `authorized`
- `scheduled`
- `in_progress`
- `monitoring`
- `completed`
- `deferred`
- `cancelled`

Legacy compatibility state:

- `approved` — historical only; accepted as a compatibility source state, never created by new canonical workflow after Wave 2.3B cutover.

## Treatment Plan state machine

Primary pathway:

`proposed -> authorized -> scheduled -> in_progress -> monitoring -> completed`

Allowed direct transitions:

- `proposed -> authorized`
- `proposed -> deferred`
- `proposed -> cancelled`
- `authorized -> scheduled`
- `authorized -> in_progress`
- `authorized -> deferred`
- `authorized -> cancelled`
- `scheduled -> in_progress`
- `scheduled -> deferred`
- `scheduled -> cancelled`
- `in_progress -> monitoring`
- `in_progress -> completed`
- `monitoring -> in_progress`
- `monitoring -> completed`
- `deferred -> authorized`
- `deferred -> scheduled` only when the service has an explicit preserved authorization fact
- `deferred -> cancelled`

Legacy source transitions:

- `approved -> scheduled`
- `approved -> in_progress`
- `approved -> deferred`
- `approved -> cancelled`

A legacy transition emits the actual `previous_state="approved"`. The system must not pretend a historical `authorized` event existed.

Terminal states:

- `completed`
- `cancelled`

Ordinary workflow does not reopen a completed Treatment Plan. If later care is required, create a new plan or use an explicitly contracted follow-up mechanism in a later wave.

## Escalation contract

`escalated` is not a Treatment Plan status.

Escalation records that the current treatment situation was referred into a higher-attention workflow while preserving the real execution state.

A plan may emit `treatment.escalated` while remaining `proposed`, `authorized`, `scheduled`, `in_progress`, `monitoring` or `deferred`.

Escalation may carry a correlation to Consultation/Priority workflow but does not itself mutate plan status.

## Authorization contract

### Professional proposal

An advisor creates/proposes the Treatment Plan.

A finalized Assessment may be the cause of a proposal. Assessment finalization alone does not authorize treatment.

### Owner authorization

The active owner may explicitly authorize a client-visible plan where the workflow requires consent.

Owner authorization must be:

- vehicle-scoped;
- plan-scoped;
- attributed to the owner user;
- timestamped;
- idempotent;
- represented by a durable plan transition/event.

An advisor may not fabricate owner authorization on the owner's behalf through an ordinary advisor action.

If an operational workflow legally allows advisor-authorized work without a separate client click, that exception must be explicit in the service inputs and audit data rather than inferred from role or from assessment finalization.

### Driver

Driver has no plan authorization authority.

### Rina / provider / system

No treatment authorization authority.

## Advisor execution authority

Advisor/administrator authority is required for:

- creating/proposing a professional Treatment Plan;
- scheduling;
- starting treatment;
- entering monitoring;
- marking completion;
- recording defer/cancel professional disposition where applicable;
- escalation;
- professional outcome recording;
- Treatment Action lifecycle mutations.

All actions require object-level vehicle authority.

## Client/advisor visibility

Client-visible surfaces may show:

- plan title/summary;
- current client-safe state;
- scheduled date/window where appropriate;
- client-safe Treatment Actions;
- client-safe timeline entries;
- explicit authorization state;
- advisor-reviewed outcome summary where marked client-visible.

Client surfaces must not expose:

- `internal_instructions`;
- advisor-only notes;
- diagnostic debate;
- hidden evidence annotations;
- provider prompts/AI reasoning;
- internal escalation rationale not classified for the client.

## Canonical Treatment Plan events

Subject type: `treatment_plan`

Initial event family:

- `treatment.proposed`
- `treatment.authorized`
- `treatment.scheduled`
- `treatment.started`
- `treatment.monitoring_started`
- `treatment.completed`
- `treatment.deferred`
- `treatment.cancelled`
- `treatment.escalated`
- `treatment.outcome_recorded`

## Event transition rules

### `treatment.proposed`

- previous: `None`
- new: `proposed`
- actor: advisor/administrator
- visibility: normally client
- progression: `not_applicable`

### `treatment.authorized`

- previous: `proposed` or `deferred`
- new: `authorized`
- actor: active owner by default; any explicitly contracted professional-authority exception must be audited separately
- visibility: client
- progression: `not_applicable`

### `treatment.scheduled`

- previous: `authorized`, `deferred` with preserved authorization, or legacy `approved`
- new: `scheduled`
- actor: advisor/administrator
- visibility: client
- progression: `not_applicable`

### `treatment.started`

- previous: `scheduled`, `authorized`, or legacy `approved`
- new: `in_progress`
- actor: advisor/administrator
- visibility: client
- progression: `not_applicable`

### `treatment.monitoring_started`

- previous: `in_progress`
- new: `monitoring`
- actor: advisor/administrator
- visibility: client unless the monitoring reason is advisor-only; event payload remains minimal either way
- progression: `not_applicable`

### `treatment.completed`

- previous: `in_progress` or `monitoring`
- new: `completed`
- actor: advisor/administrator
- visibility: client
- progression: `not_applicable`

Completion means the planned operational work was recorded complete. It does not mean the vehicle improved, a concern resolved, or an assessment was proven correct.

### `treatment.deferred`

- previous: `proposed`, `authorized`, `scheduled`, or legacy `approved`
- new: `deferred`
- actor: active owner when explicitly requesting defer, or advisor/administrator when recording a professional/operational defer
- visibility: client
- progression: `not_applicable`
- payload must distinguish disposition source without exposing private rationale

### `treatment.cancelled`

- previous: `proposed`, `authorized`, `scheduled`, `deferred`, or legacy `approved`
- new: `cancelled`
- actor: advisor/administrator or active owner where client cancellation is explicitly supported
- visibility: client
- progression: `not_applicable`

### `treatment.escalated`

- previous state: current plan state
- new state: same current plan state
- actor: advisor/administrator
- visibility: advisor or client according to escalation classification
- progression: `not_applicable`
- must carry correlation/causation linkage when a new Consultation/Priority record is created

### `treatment.outcome_recorded`

- previous state: current plan state, normally `monitoring` or `completed`
- new state: same current plan state
- actor: advisor/administrator
- visibility: client or advisor according to outcome classification
- progression: one of `improving`, `stable`, `deteriorating`, `resolved`, `insufficient_evidence`

This event may never be emitted by automatic completion logic.

For progression values other than `insufficient_evidence`, the outcome record must reference reviewed supporting evidence or another explicitly accepted professional observation source.

## Event payload minimization

Canonical Treatment Plan events record lifecycle facts, not the complete treatment document.

Client-visible event data may contain minimal identifiers/metadata such as:

- assessment_id;
- consultation_id;
- authorization source classification;
- scheduled timestamp/window;
- action count;
- outcome record id;
- escalation correlation id.

Do not put these into canonical event payloads:

- full internal instructions;
- hidden advisor rationale;
- full diagnostic notes;
- raw provider/AI output;
- secrets/tokens;
- unrestricted evidence blobs.

## Idempotency

Each material transition must have a deterministic idempotency key derived from the persisted plan id, action semantics and stable occurrence/operation identifier.

A replay with the same key and same semantics returns/reuses the existing event/effect.

A replay with the same key and different semantics fails closed.

No duplicate state transition or duplicate canonical event may result from refresh/double-submit/retry.

## Transaction boundary

`TreatmentPlanLifecycleService` must never independently commit.

The route/coordinator owns the outer transaction so:

- domain state mutation;
- authorization/consent record where applicable;
- Treatment Action side effects where applicable;
- canonical event emission

all succeed or roll back together.

## Assessment integration

After Wave 2.3B cutover:

- finalizing a new Assessment may create/reuse exactly one plan by `assessment_id`;
- that new plan uses `status="proposed"`;
- plan creation emits `treatment.proposed` in the same outer transaction as assessment finalization, or is coordinated in an explicitly atomic follow-on operation if the implementation proves that safer;
- no owner authorization is inferred;
- finalized Assessment content remains immutable.

The exact transaction coordinator must be covered by rollback tests so an event failure cannot leave assessment finalization and treatment proposal in contradictory partial state.

## Legacy `approved` compatibility

Existing rows with `status="approved"` are not rewritten.

Rules:

- readable in advisor/owner-safe views;
- service can transition them through the allowed legacy source transitions;
- first future canonical event preserves `previous_state="approved"`;
- no fabricated `treatment.proposed`/`treatment.authorized` events;
- no mass state update solely to make old rows look canonical.

## Treatment Action contract

Wave 2.3C introduces a durable Treatment Action entity.

Canonical states:

- `planned`
- `scheduled`
- `in_progress`
- `completed`
- `deferred`
- `cancelled`

Primary flow:

`planned -> scheduled -> in_progress -> completed`

Branches:

- `planned -> deferred|cancelled`
- `scheduled -> deferred|cancelled`
- `deferred -> scheduled|cancelled`

A Treatment Action is always:

- linked to one Treatment Plan;
- linked to one vehicle through that plan;
- attributed;
- timestamped;
- evidence/provenance-capable;
- client-safe by explicit visibility rules.

## Treatment Action canonical events

Subject type: `treatment_action`

- `treatment_action.created`
- `treatment_action.scheduled`
- `treatment_action.started`
- `treatment_action.completed`
- `treatment_action.deferred`
- `treatment_action.cancelled`

All use `progression_direction="not_applicable"`.

Treatment Action completion alone is not a health outcome.

## PostgreSQL contract

Wave 2.3B/C migrations must:

- extend canonical subject/event CHECK constraints deliberately;
- explicitly guard nullable previous/new-state fields so PostgreSQL UNKNOWN cannot accidentally pass invalid treatment transitions;
- preserve all existing Concern/Evidence/Consultation/Assessment constraints;
- preserve subject-less legacy compatibility where still required by the established VehicleEvent schema;
- preflight conflicting existing rows before replacing constraints;
- include downgrade to the exact previous production contract and re-upgrade rehearsal.

## No historical synthesis

No migration, command or deploy step creates canonical treatment events for historical plan activity that predates this contract.

Historical rows remain historical rows.

## Production acceptance sequence

Wave 2.3B live proof should demonstrate:

`finalized assessment -> treatment proposed -> explicit authorization -> optional schedule -> start -> monitoring/completion`

with canonical events exactly matching the material transitions.

Separately prove:

- legacy `approved` plan can make one real future transition without invented history;
- owner cannot see advisor-only instructions;
- driver/unrelated user cannot mutate treatment;
- Rina/provider cannot authorize or complete treatment;
- double-submit is idempotent;
- event failure rolls back state mutation;
- completion leaves Reported Concern and Vehicle Health unchanged unless separately updated through their own contracts.

Wave 2.3C then proves Treatment Actions and explicit evidence-backed outcomes.

## Non-goals

- autonomous treatment approval;
- mechanic-procedure automation;
- auto-resolution of concerns;
- automatic health-score improvement;
- synthetic outcome labels;
- billing/inventory semantics;
- rewriting historical `approved` rows;
- reopening completed plans through ordinary workflow.