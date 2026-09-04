# Aura Wave 2.3C — Intervention and Evidence Audit

Issue: #106  
Parent Wave: #99

## Purpose

This audit establishes what Aura already stores before Wave 2.3C introduces durable Treatment Actions and professional outcomes.

The goal is to avoid duplicating legacy service/history records, avoid creating a second evidence subsystem, and preserve the Wave 2.3 rule that recommendation, authorization, intervention, and outcome are separate facts.

## Current Treatment Plan boundary

`TreatmentPlan` is the professional care-pathway container. It currently stores:

- vehicle, consultation and assessment references;
- advisor attribution;
- a professional title;
- advisor-only `internal_instructions`;
- client-safe `client_summary`;
- Treatment Plan lifecycle state.

Wave 2.3B moved plan state transitions behind `TreatmentPlanLifecycleService` and established explicit owner authorization.

A Treatment Plan does **not** currently contain durable child records describing each concrete intervention performed.

That missing intervention record is the reason for `TreatmentAction`.

## Existing service/history behavior is not TreatmentAction

Aura still contains legacy service/history surfaces, including the client `add_service_record` route, which records service activity through event/history behavior rather than a plan-scoped professional intervention entity.

There is no existing `ServiceRecord` or `TreatmentRecord` ORM entity that can safely be renamed into `TreatmentAction`.

Wave 2.3C therefore introduces a new domain entity rather than reinterpreting historical service/event rows.

Historical service/event rows remain historical facts. No migration will convert them into Treatment Actions or synthesize action events.

## Existing evidence foundation

Aura already has a governed evidence subsystem in `evidence/models.py`:

### `VehicleEvidence`

Stores metadata and lifecycle state for one privately stored evidence object. Raw media bytes remain outside PostgreSQL.

Important existing properties:

- vehicle-scoped;
- uploader attribution;
- evidence type and purpose;
- visibility (`client`, `advisor`, `internal`);
- review lifecycle;
- private-storage metadata;
- consent/lawful-purpose/retention metadata;
- advisor review attribution.

The existing evidence-purpose vocabulary already includes `treatment_evidence`.

### `EvidenceLink`

Provides a generic subject link using:

- `evidence_id`;
- `car_id`;
- `subject_type`;
- `subject_id`;
- `relationship_type`;
- creator attribution.

The record has a uniqueness contract per evidence/subject/relationship and a vehicle/subject index.

### `EvidenceExtraction`

Stores provider extraction provenance and encrypted result slots. Provider output is not a professional outcome and must never be copied into Treatment Outcome fields as if advisor-reviewed fact.

## Evidence linkage limitation discovered

Although `EvidenceLink` is structurally generic, the currently governed review/link service in `evidence/review.py` only creates advisor-reviewed links to Reported Concerns.

Its useful safety pattern is:

1. evidence must exist;
2. actor must have advisor authority for the evidence vehicle;
3. evidence must be accepted and available;
4. target subject must exist;
5. target subject and evidence must belong to the same vehicle;
6. link creation and canonical `evidence.linked` event commit atomically;
7. retry is idempotent.

Wave 2.3C must extend this pattern for Treatment Actions and Treatment Outcomes rather than bypassing it.

## Evidence subject vocabulary gap

The existing evidence subject vocabulary includes `treatment_plan` but not yet:

- `treatment_action`;
- `treatment_outcome`.

Wave 2.3C will add those subject types. This is an additive vocabulary change; it does not rewrite existing links.

## Existing canonical event foundation

`VehicleEvent` already provides:

- canonical subject type/id;
- actor and authority;
- client/advisor/internal visibility;
- previous/new state;
- progression direction;
- correlation/causation IDs;
- evidence references;
- deterministic fingerprint/idempotency;
- payload safety limits.

Wave 2.3B registered the Treatment Plan event family through `services/treatment_event_emission.py`.

Two Wave 2.3C gaps remain:

1. `treatment.outcome_recorded` is contracted but not yet registered in the runtime Treatment Plan event family or PostgreSQL constraint.
2. no `treatment_action.*` event family exists yet.

## Domain separation locked for 2.3C

### Treatment Plan

Represents the professional pathway and authorization/execution state.

It answers: **What care pathway was proposed/authorized/coordinated?**

### Treatment Action

Represents one concrete professional intervention within one Treatment Plan.

It answers: **What specific work was planned, scheduled, started, deferred, cancelled, or completed?**

An action is not a diagnosis, recommendation option, invoice line, inventory item, or inferred outcome.

### Treatment Outcome

Represents an additive advisor-reviewed observation after or during intervention.

It answers: **What does accepted evidence or an explicitly recorded professional observation show after the intervention?**

It is not inferred from action completion or plan completion.

## Historical integrity rules

Wave 2.3C will not:

- create TreatmentAction rows for historical completed Treatment Plans;
- convert legacy service/history VehicleEvents into Treatment Actions;
- fabricate `treatment_action.created` or completion events for past work;
- fabricate `treatment.outcome_recorded` from plan/action completion;
- rewrite legacy `approved` Treatment Plan history;
- auto-resolve Reported Concerns;
- auto-change Vehicle Health.

## Authority findings

Existing `resolve_vehicle_authority` remains the object-level authority source.

Wave 2.3C mutations are professional-record mutations:

- advisor/administrator may create and transition Treatment Actions;
- advisor/administrator may record Treatment Outcomes;
- owners may view client-safe action/outcome facts but may not mark professional work completed or record outcomes;
- drivers may not mutate Treatment Actions or outcomes;
- Rina/provider/system automation may not create professional intervention facts or outcomes.

## Implementation consequence

Wave 2.3C should reuse:

- `TreatmentPlanLifecycleService` transaction style;
- canonical `VehicleEvent` emitter;
- existing evidence review/availability rules;
- `EvidenceLink` for governed evidence references;
- object-level vehicle authority resolution.

It should add:

- durable `TreatmentAction`;
- durable additive `TreatmentOutcome`;
- action lifecycle service;
- treatment action canonical event adapter;
- outcome-recording service;
- evidence-link extension for action/outcome subjects;
- PostgreSQL constraints and migration rehearsal;
- owner-safe and advisor-only visibility separation.

## Audit conclusion

There is no existing durable intervention entity to repurpose. `TreatmentAction` is a legitimate new domain model.

There **is** an existing evidence system to reuse. Wave 2.3C must extend it rather than inventing parallel evidence storage or embedding raw evidence inside treatment records.
