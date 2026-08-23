# Aura Wave 2.2B — Vehicle Assessment State and Canonical Event Contract

**Parent epic:** #74  
**Delivery issue:** #85  
**Companion audit:** `docs/AURA_WAVE_2_2B_ASSESSMENT_LIFECYCLE_AUDIT.md`  
**Status:** Architecture contract for review — no runtime implementation

## 1. Decision

Wave 2.2B keeps the existing `VehicleAssessment` model and its existing two-state lifecycle:

```text
draft → finalized
```

Aura will not introduce a generic assessment `reopened` state.

A finalized assessment is a durable professional care record. Ordinary editing stops at finalization. Any later correction is additive and separately attributable.

`AssessmentLifecycleService` becomes the explicit owner of assessment lifecycle legality in the implementation slice.

## 2. Canonical states

Approved states remain:

- `draft`
- `finalized`

Meaning:

`draft`
: Professional working record. Advisor-editable while the linked Consultation is active. It is not yet the finalized client care record.

`finalized`
: Locked professional assessment. The original document and child records are no longer editable through ordinary assessment flow.

No third state is required for correction/addendum behavior.

## 3. Lifecycle actions

Approved actions:

- `start_or_resume`
- `save_draft`
- `finalize`
- later `add_correction` / `add_addendum`

`save_draft` is a persistence action inside `draft`; it is not a canonical lifecycle event.

Correction/addendum is an additive record attached to a `finalized` assessment. It does not transition the assessment back to draft.

## 4. Allowed transitions and actions

| Current state | Action | Result | Actor | Required conditions |
|---|---|---|---|---|
| none | `start_or_resume` | new `draft` | advisor | linked Consultation exists, same vehicle, Consultation `in_progress`, no assessment already exists |
| `draft` | `start_or_resume` | same `draft` | advisor | same Consultation/vehicle; return existing row, do not create a second assessment/event |
| `draft` | `save_draft` | `draft` | advisor | structured submission valid; frozen identity fields unchanged |
| `draft` | `finalize` | `finalized` | advisor | linked Consultation still `in_progress`; five required system statuses present; assessment/consultation vehicle scope consistent |
| `finalized` | ordinary edit/save | prohibited | none | fail closed |
| `finalized` | `start_or_resume` | prohibited | advisor | do not create a second assessment for same Consultation |
| `finalized` | `add_correction` | assessment remains `finalized`; new additive record | advisor | durable correction/addendum model exists; reason/category + actor + timestamp captured |

All other state transitions are invalid.

## 5. Assessment creation contract

The implementation should expose a service operation equivalent to:

```text
AssessmentLifecycleService.start_or_resume(
    consultation_id,
    actor_user_id,
    source,
)
```

The service must:

1. load the Consultation;
2. verify advisor authority through Aura's canonical authority layer;
3. require `Consultation.status == "in_progress"`;
4. verify the Consultation and assessment vehicle scope are consistent;
5. query the one assessment allowed by `consultation_id`;
6. return an existing draft without emitting a duplicate creation event;
7. reject an existing finalized assessment for that Consultation;
8. when creating, snapshot current vehicle identity/mileage fields into the assessment;
9. create `VehicleAssessment(status="draft")`;
10. emit `assessment.created` in the same transaction;
11. never commit independently inside the canonical event emitter.

The existing unique constraint on `consultation_id` remains a database-level concurrency protection and must be retained.

## 6. Frozen vehicle context

The following assessment creation context is treated as frozen professional-record context once the assessment exists:

- VIN;
- mileage at assessment;
- engine number;
- engine type;
- any other explicitly snapshotted vehicle identity fields added later.

Draft editing must not silently refresh these fields from the current vehicle profile.

If a frozen identity value is wrong, correction semantics must be explicit rather than overwriting finalized history.

## 7. Draft persistence contract

The HTTP/form adapter must normalize incoming form data into a structured draft command before lifecycle persistence.

`AssessmentLifecycleService.save_draft` (or equivalent domain method) must preserve the production hardening proven by PR #82:

- accept normalized risk/treatment collections, not raw assumptions about HTML field names;
- validate an entire repeated group before destructive replacement;
- never delete existing child rows when the corresponding group is absent;
- reject mismatched parallel groups before mutation;
- update scalar fields only when explicitly present in the command;
- preserve cost/consequence and other scalar values when omitted;
- roll back the full draft save if structured validation fails;
- leave assessment state as `draft`;
- never mutate frozen vehicle context during ordinary save.

A successful route should continue using Post/Redirect/Get so a fresh database read proves persistence.

## 8. No canonical event for each draft save

Approved canonical assessment lifecycle events are not an edit journal.

Therefore:

```text
save_draft → no VehicleEvent
```

Reasons:

- draft saves may be frequent;
- field-level edits are not lifecycle milestones;
- detailed professional text should not be copied into broad event payloads;
- event spam would degrade longitudinal signal quality.

If Aura later requires a compliance-grade assessment edit history, introduce a dedicated assessment revision/audit mechanism rather than emitting one canonical VehicleEvent per form save.

## 9. Finalization contract

The implementation should expose an operation equivalent to:

```text
AssessmentLifecycleService.finalize(
    assessment_id,
    actor_user_id,
    source,
)
```

The service must:

1. prove advisor authority for the assessment vehicle;
2. require `assessment.status == "draft"`;
3. require linked Consultation state `in_progress`;
4. verify assessment, Consultation and vehicle IDs are consistent;
5. preserve the current minimum finalization completeness rule requiring:
   - engine status;
   - transmission status;
   - suspension status;
   - electrical status;
   - cooling status;
6. set:
   - `status="finalized"`;
   - `is_finalized=True`;
   - `finalized_at` once;
   - `finalized_by=actor_user_id`;
7. emit `assessment.finalized` in the same transaction;
8. allow the existing legacy TreatmentPlan compatibility creation to participate in the same **outer** transaction without giving AssessmentLifecycleService ownership of TreatmentPlan lifecycle;
9. commit once at the orchestration boundary.

Wave 2.2B must not invent additional mandatory professional fields solely to tighten the form. Any new clinical completeness rule requires an explicit product decision.

## 10. Finalizer versus creator

`advisor_id` remains the assessment creator.

`finalized_by` remains the explicit finalizer.

The contract does not require the finalizer to be the same advisor who created the draft. Aura may permit professional handoff between authorized advisors.

If a future policy needs every draft edit attributed to an individual advisor, that requires a dedicated assessment audit/revision mechanism. Do not rewrite `advisor_id` on each save to simulate edit attribution.

## 11. Finalized immutability

After finalization:

- ordinary edit route/service access fails closed;
- risk child rows cannot be replaced;
- treatment-option child rows cannot be replaced;
- system statuses cannot be rewritten;
- cost/consequence analysis cannot be rewritten;
- professional recommendation cannot be rewritten;
- frozen vehicle context cannot be rewritten;
- finalization actor/time cannot be changed through ordinary workflow.

Report rendering is read-only.

The assessment may remain visible as historical vehicle care record subject to authorization/ownership policy.

## 12. Canonical event taxonomy

Wave 2.2B2 may add exactly:

```text
assessment.created
assessment.finalized
```

Wave 2.2B3 may add, only after a durable correction/addendum record exists:

```text
assessment.corrected
```

Subject contract:

```text
subject_type = "vehicle_assessment"
subject_id   = VehicleAssessment.id
```

No parallel assessment event emitter is permitted. Extend `services.event_emission`.

## 13. `assessment.created` contract

Required state semantics:

```text
previous_state = null
new_state      = "draft"
```

Required authority:

```text
actor_type      = "user"
actor_authority = "advisor"
```

Default visibility:

```text
advisor
```

Progression direction:

```text
not_applicable
```

The event must not include draft risk text, likely-cause text, treatment-option text, professional recommendation or other free-form clinical working content.

Minimal event meaning: a professional assessment working record was created for the vehicle under an active Consultation.

## 14. `assessment.finalized` contract

Required state semantics:

```text
previous_state = "draft"
new_state      = "finalized"
```

Required authority:

```text
actor_type      = "user"
actor_authority = "advisor"
```

Default visibility:

```text
client
```

Progression direction:

```text
not_applicable
```

The client-visible event may state that the professional Vehicle Health Assessment was finalized and is available in the care record.

It must not copy:

- internal advisor reasoning;
- full risk descriptions;
- likely causes;
- raw treatment options;
- internal treatment-plan instructions;
- Rina/provider prompts or outputs.

The finalized report itself remains the controlled surface for professional content.

## 15. Idempotency and replay

Recommended deterministic keys:

```text
assessment:{assessment_id}:created:{created_at}
assessment:{assessment_id}:finalized:{finalized_at}
```

`start_or_resume` on an existing draft returns the existing assessment and must not emit another `assessment.created`.

A repeated finalize call after successful finalization must not create a second event or second TreatmentPlan. It may fail closed as already finalized; it must never silently repeat side effects.

A legacy finalized assessment with no canonical finalization event must **not** receive a synthetic event merely because a retry reaches the new service.

## 16. PostgreSQL canonical constraint

Wave 2.2B2 must align production PostgreSQL with the application taxonomy in the same PR/slice.

The migration must permit only:

```text
assessment.created   + subject_type=vehicle_assessment + null -> draft
assessment.finalized + subject_type=vehicle_assessment + draft -> finalized
```

with:

```text
progression_direction = not_applicable
```

The migration must preserve all existing concern/evidence/consultation rules.

It must include:

- preflight of current canonical rows;
- upgrade test on PostgreSQL;
- downgrade restoring the exact prior contract;
- re-upgrade rehearsal;
- wrong subject/event rejection;
- wrong assessment state-transition rejection.

## 17. TreatmentPlan compatibility boundary

Current assessment finalization also creates a `TreatmentPlan(status="approved")`.

Wave 2.2B does not approve that as the permanent TreatmentPlan lifecycle.

Implementation rule:

```text
AssessmentLifecycleService.finalize
        ↓
assessment mutation + assessment.finalized event
        ↓
legacy treatment-plan compatibility helper/coordinator
        ↓
one outer transaction commit
```

The compatibility helper may preserve existing behavior until Wave 2.3, but:

- it is not part of assessment event taxonomy;
- it does not make `approved` a newly endorsed Wave 2.2B state design;
- it must not commit independently;
- failure must roll back finalization and its canonical event;
- Wave 2.3 remains responsible for TreatmentPlan state redesign/migration.

## 18. Owner/advisor report authorization

Wave 2.2B must preserve the production-proven report rules:

- advisor/admin: finalized report access;
- active vehicle owner: finalized report access;
- unrelated user: denied;
- inactive former owner: denied;
- draft assessment: not client-downloadable.

Lifecycle refactoring must not weaken these object-level checks.

The current shared URL may remain as compatibility surface during Wave 2.2B2. Route naming cleanup is not required to prove lifecycle correctness.

## 19. Ownership-transfer privacy boundary

Active ownership currently authorizes access by vehicle rather than by historical ownership episode.

Wave 2.2B does not change this behavior, but it also does not declare cross-owner historical report portability fully reviewed.

Before a new vehicle owner is intentionally guaranteed access to prior-owner assessments, Aura must verify that the report projection contains no prior-owner personal/contact or consultation-specific private data that should not transfer with the vehicle record.

This is a privacy/product review, not a reason to weaken current active-owner authorization.

## 20. Correction/addendum contract for Wave 2.2B3

A correction is not an assessment state transition.

Minimum semantics for a future durable addendum/correction row:

- references one finalized `VehicleAssessment`;
- has its own immutable row ID;
- records creating advisor;
- records creation timestamp;
- records category/reason such as correction, clarification or additional information;
- separates client-visible text from advisor-only/internal text;
- has explicit visibility;
- cannot mutate the original finalized assessment;
- once published, cannot be silently edited or deleted; a further correction is another additive record.

Material new diagnostic/professional work should normally use a new Consultation/Assessment rather than an addendum that effectively replaces the original assessment.

## 21. Future `assessment.corrected` event

Only after the durable correction record exists may Wave 2.2B3 emit:

```text
assessment.corrected
```

Recommended semantics:

```text
subject_type    = vehicle_assessment
previous_state  = finalized
new_state       = finalized
progression     = not_applicable
```

The assessment state remains unchanged.

The canonical event should carry only minimal correction metadata such as the durable addendum ID/category and visibility, not the full professional correction text.

Where supported, `correction_of_event_id` should reference the canonical `assessment.finalized` event being supplemented. The event emitter contract must be explicitly widened for this assessment correction use; do not overload concern-only correction validation silently.

## 22. Correction visibility

If the correction changes or clarifies a fact presented to the owner:

```text
addendum visibility = client
assessment.corrected visibility = client
```

If it is genuinely internal professional context:

```text
addendum visibility = advisor/internal
assessment.corrected visibility = matching restricted class
```

Client-visible report/history should display client-visible addenda **after** the original finalized assessment and make the addendum date/attribution clear.

It must not rewrite the original report as though the original version never existed.

## 23. No synthetic historical assessment events

Wave 2.2B2 begins forward transactional event capture at cutover.

It does not backfill `assessment.created` or `assessment.finalized` for existing rows merely to increase longitudinal counts.

Existing rows remain authoritative legacy state.

Any future backfill requires a separate deterministic provenance design and explicit backfill source classification.

## 24. First implementation slice after contract approval

Wave 2.2B2 should implement only:

1. assessment event taxonomy for `created` and `finalized`;
2. `AssessmentLifecycleService` creation/start-or-resume;
3. structured safe draft persistence preserving PR #82 behavior;
4. service-owned finalization;
5. PostgreSQL canonical assessment event constraint migration;
6. compatibility treatment-plan creation in the same outer transaction;
7. route/adapter cutover;
8. object-level authority and cross-vehicle tests;
9. finalized immutability tests;
10. production smoke verification.

Do **not** implement correction/addendum in the same first runtime PR unless 2.2B2 is already production-proven. Correction remains the narrow Wave 2.2B3 slice.

## 25. Definition of done for Wave 2.2B1

The contract chapter is complete when review accepts that:

- the two-state assessment model remains canonical;
- draft save is durable but not a canonical event;
- `assessment.created` and `assessment.finalized` are the only initial event types;
- finalization is immutable;
- correction/addendum is additive, not reopen/edit;
- TreatmentPlan auto-creation is explicitly compatibility behavior pending Wave 2.3;
- PostgreSQL constraints are part of the implementation, not an afterthought;
- owner/advisor report authorization remains unchanged;
- historical backfill remains prohibited by default;
- 2.2B2 may proceed only inside these boundaries.
