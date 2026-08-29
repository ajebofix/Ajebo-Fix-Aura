# Aura Wave 2.3 — Treatment Lifecycle Audit

Issue: #99  
Contract slice: #100  
Parent epic: #74  
Existing workflow backlog: #3

## Purpose

Record the production-relevant Treatment Plan behavior that exists before Wave 2.3 runtime work, identify where lifecycle semantics are currently implicit or unsafe, and define the compatibility boundaries that the next implementation slice must preserve.

This document is descriptive. It does not authorize production data rewrites or synthetic historical events.

## Current model behavior

`TreatmentPlan` already exists and is linked to the vehicle, consultation and assessment. The model currently carries:

- `car_id`
- `consultation_id`
- `assessment_id`
- `advisor_id`
- `title`
- `internal_instructions`
- `client_summary`
- `status`

The current model default for `status` is `approved`.

That default predates a first-class treatment lifecycle and therefore cannot be interpreted as a reliable owner-consent fact.

## Current assessment-to-treatment bridge

Wave 2.2B deliberately kept a compatibility side effect inside `AssessmentLifecycleService.finalize()` so existing treatment UI continued to work while Assessment lifecycle was completed.

`_ensure_legacy_treatment_plan()`:

1. looks up one existing plan by `assessment_id`;
2. reuses it if already present;
3. otherwise creates one Treatment Plan in the same transaction as assessment finalization;
4. sets the new plan to `status="approved"`;
5. copies the finalized professional recommendation into advisor-only `internal_instructions`;
6. exposes only a generic client summary.

This bridge is transactionally safe and idempotent for one plan per assessment, but its `approved` label is only a compatibility artifact. Wave 2.3 must not promote that artifact into the canonical consent model.

## Current route-owned mutations

The existing advisor UI mutates TreatmentPlan rows directly in route functions.

Observed routes include:

- Start Treatment: directly sets `plan.status = "in_progress"` and commits.
- Mark Completed: directly sets `plan.status = "completed"` and commits.
- Monitor / Defer: directly sets `plan.status = "deferred"` and commits.

Problems:

- lifecycle legality is route-owned instead of service-owned;
- the current routes do not consistently validate source state;
- plan mutation and canonical event emission cannot currently be atomic because no treatment event family exists;
- completion can be invoked without a first-class intervention/action record;
- `deferred` currently mixes operational pause with the UI concept of monitoring;
- no separate durable owner-authorization fact exists;
- no explicit outcome record links treatment completion to observed follow-up evidence.

## Current canonical-event gap

`services/event_emission.py` currently supports Reported Concern, Evidence, Consultation and Assessment families. It has no Treatment Plan or Treatment Action event family.

Therefore current TreatmentPlan mutations do not contribute trustworthy treatment progression to the longitudinal ledger.

This is the central Wave 2.3 gap.

## Current visibility boundary

Treatment Plans are rendered on the vehicle record and contain both:

- advisor-only `internal_instructions`; and
- client-safe `client_summary`.

Wave 2.3 must preserve this split. Owner-facing surfaces must never expose internal instructions, diagnostic debate, hidden advisor rationale or restricted evidence.

## Historical production rows

Production already contains TreatmentPlan rows created and exercised before the Wave 2.3 contract. Some are in states such as:

- `approved`
- `in_progress`
- `completed`
- `deferred`

These rows are durable historical application facts.

Wave 2.3 must **not**:

- rewrite every historical `approved` row into `authorized`;
- fabricate `treatment.proposed` or `treatment.authorized` events for events that never happened under the canonical contract;
- silently infer owner consent from the old `approved` value;
- infer health improvement or concern resolution from an old `completed` value.

Legacy rows remain readable. The lifecycle service may support them as compatibility source states for a real future transition.

## Product distinction that must become explicit

The current implementation collapses four different concepts that must be separated:

1. **Professional recommendation** — an advisor proposes a treatment pathway.
2. **Owner authorization** — the active owner consents to proceeding where authorization is required.
3. **Treatment execution** — an advisor starts and manages real intervention work.
4. **Observed outcome** — a later, evidence-backed professional observation records whether the vehicle is improving, stable, deteriorating, resolved or still uncertain.

A finalized assessment may create (1). It must not automatically create (2), (3) or (4).

## Treatment Plan versus assessment treatment options

`VehicleAssessmentTreatmentOption` rows remain part of the finalized Assessment record. They describe professional options considered at assessment time.

A `TreatmentPlan` is a separate operational care record created from an assessment context. It represents the pathway selected for actual coordination/execution.

Wave 2.3 must not mutate the finalized assessment options to represent live treatment progress.

## Treatment Plan versus Treatment Action

A plan is the coordinating pathway. A Treatment Action is a concrete intervention record within that pathway.

Examples of actions may include a service operation, inspection, replacement, fluid service, software procedure or follow-up check, but the data model must remain generic enough to avoid hard-coding mechanic instructions into the lifecycle layer.

The Treatment Action record needs its own state, attribution, timestamps and evidence/provenance links.

## Outcome semantics

Treatment completion is an operational fact, not a health outcome.

Therefore:

- `treatment.completed` must use `progression_direction="not_applicable"`;
- it must not auto-resolve Reported Concerns;
- it must not auto-change vehicle health state;
- it must not automatically mark an Assessment recommendation as successful;
- it must not create an inferred prediction label.

Outcome is recorded separately through advisor-reviewed evidence.

## Authority audit

### Advisor / administrator

May own professional treatment creation, scheduling, start, monitoring, completion, escalation and outcome recording, subject to legal state transitions and vehicle scope.

### Active owner

May view the client-safe plan and explicitly authorize/decline or request defer where the runtime contract exposes those actions. Owner authorization is not interchangeable with professional plan creation.

### Driver

May receive operationally necessary instructions later, but must not authorize, start, complete, correct or record professional treatment outcomes.

### Rina / provider / system automation

May explain plan status, remind, surface evidence and request advisor review. Must not authorize treatment, start treatment, complete treatment or create professional outcome observations.

## Runtime cutover requirements

Wave 2.3B must:

- add a `TreatmentPlanLifecycleService`;
- move existing TreatmentPlan mutations behind that service;
- preserve one-plan-per-assessment idempotency;
- change new assessment-originated plans from semantic `approved` to `proposed`;
- keep legacy `approved` rows usable without rewriting them;
- emit plan events in the same transaction as the state mutation;
- add PostgreSQL event-contract checks and migration rehearsal;
- preserve owner/advisor visibility boundaries;
- keep treatment completion independent from concern and health progression.

Wave 2.3C then adds durable Treatment Actions and explicit outcome records.

## Production-proof requirements

A live Wave 2.3 proof must demonstrate at minimum:

1. finalized Assessment produces/reuses one proposed Treatment Plan;
2. owner-safe plan content is visible without advisor-only instructions;
3. owner authorization is an explicit separate action where required;
4. advisor starts only from a legal source state;
5. treatment progression survives reload and remains vehicle-scoped;
6. invalid/cross-vehicle transitions fail closed;
7. canonical treatment events appear exactly once per material transition;
8. completion does not change concern/health state by itself;
9. legacy `approved` plans still work without fabricated history;
10. rollback leaves neither a partial plan mutation nor an orphan canonical event.

## Conclusion

Wave 2.3 is not a rename of the existing Treatment Plan buttons. It is the point where Aura starts recording the difference between recommendation, consent, intervention and observed outcome as durable longitudinal facts.