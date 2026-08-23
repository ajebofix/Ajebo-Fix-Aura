# Aura Wave 2.2B — Vehicle Assessment Lifecycle Audit

**Parent epic:** #74  
**Delivery issue:** #85  
**Predecessor:** #77 / Wave 2.2A production closeout  
**Architecture baseline:** `docs/AURA_WAVE_2_1_STATE_EVENT_CONTRACT.md`  
**Status:** Architecture/audit only — no runtime behaviour changes

## 1. Objective

Wave 2.2B completes the second half of Wave 2.2 by moving Vehicle Assessment lifecycle state behind an explicit advisor-governed service and extending Aura's canonical longitudinal ledger with assessment lifecycle facts.

This audit records the current production behavior before implementation. It incorporates the defects discovered during the Wave 2.2A production smoke path and distinguishes what is already safe from what remains route-owned or under-specified.

## 2. Existing model is retained

Aura already has the correct root record: `VehicleAssessment`.

Relevant persisted lifecycle/authority fields are:

- `consultation_id` — required and unique, so one assessment per consultation;
- `car_id` — required vehicle scope;
- `advisor_id` — required creating advisor;
- `finalized_by` — nullable until finalization;
- `status` — existing values `draft` / `finalized`;
- `created_at`;
- `is_finalized`;
- `finalized_at`.

The assessment also freezes professional vehicle context such as VIN, mileage and engine identity at creation time.

Child professional content is stored in:

- `VehicleAssessmentRisk`;
- `VehicleAssessmentTreatmentOption`.

Scalar professional content includes the five system statuses, cost/consequence analysis and professional recommendation.

Decision: Wave 2.2B extends these models. It does not rebuild the assessment domain under new names.

## 3. Current creation/start behavior

Current route:

`admin.routes.admin_start_assessment`

Observed behavior:

1. requires advisor/admin route authority;
2. requires linked Consultation to be `in_progress`;
3. looks up the one assessment for that Consultation;
4. if an existing draft exists, redirects to continue it;
5. if an existing finalized assessment exists, refuses to create another assessment for the same Consultation;
6. otherwise creates a `VehicleAssessment(status="draft")`;
7. snapshots vehicle VIN, mileage and engine identity into the assessment;
8. commits directly in the route.

No canonical `assessment.created` event is emitted today.

Architecture finding: the preconditions are mostly correct, but lifecycle ownership is still in the route.

## 4. Current draft editing behavior

The legacy `admin.admin_edit_assessment` body still exists, but production runtime binding is replaced by `services.assessment_draft_cutover.admin_edit_assessment_cutover`.

That compatibility adapter was added after live production testing exposed destructive draft persistence.

Current production-safe behavior:

- only `draft` assessments may enter ordinary edit flow;
- repeating risk fields accept both canonical names and current template `[]` names;
- repeating groups are validated as parallel sets before destructive replacement;
- a missing repeating group preserves existing rows rather than deleting them;
- malformed/incomplete groups roll back and preserve existing data;
- scalar fields update only when actually submitted;
- absent cost/consequence input no longer silently nulls stored content;
- successful save commits and uses Post/Redirect/Get so the next page reload proves persisted database state.

No canonical event is emitted for draft saves.

Architecture finding: this hardening must be preserved when draft persistence moves behind `AssessmentLifecycleService`. The compatibility adapter is not the final domain boundary.

## 5. Production draft-loss incident

During Wave 2.2A live verification, the template submitted repeating fields such as:

```text
risk_description[]
risk_cause[]
risk_consequence[]
risk_urgency[]
treatment_title[]
treatment_description[]
treatment_code[]
```

The legacy route read unsuffixed names.

As a result, a successful Save Draft request could:

1. delete existing child risk/treatment rows;
2. read zero replacement rows;
3. recreate zero rows;
4. commit successfully.

The Professional Recommendation survived because it was a scalar field without the naming mismatch.

PR #82 corrected this failure mode.

Permanent contract implication: draft persistence must never perform destructive replacement until the entire submitted replacement group is validated.

## 6. Current finalization behavior

Current route:

`admin.routes.admin_finalize_assessment`

Observed behavior:

1. advisor/admin only;
2. loads the assessment directly;
3. requires `status == "draft"`;
4. requires all five system-status fields:
   - engine;
   - transmission;
   - suspension;
   - electrical;
   - cooling;
5. sets:
   - `status="finalized"`;
   - `is_finalized=True`;
   - `finalized_at=datetime.utcnow()`;
   - `finalized_by=current_user.id`;
6. creates a `TreatmentPlan` in the same SQLAlchemy transaction;
7. that TreatmentPlan is created directly in legacy `approved` state;
8. commits once.

No canonical `assessment.finalized` event is emitted today.

Architecture finding: finalization is already treated as a point-of-no-return professional record boundary, but route code still owns the transition and canonical history is missing.

## 7. Finalized immutability is already de-facto production policy

Ordinary assessment editing rejects any assessment whose status is not `draft`.

The production report path reads finalized assessments but does not mutate them.

Wave 2.2A production testing further established that missing finalized content must not be reconstructed by silently reopening and rewriting the original record.

Decision: Wave 2.2B must formalize this existing policy. A finalized assessment remains immutable under normal edit flow.

## 8. Current TreatmentPlan coupling

Assessment finalization currently creates a `TreatmentPlan(status="approved")` in the same transaction.

This coupling has one important safety property: the current finalization and legacy treatment-plan creation cannot partially commit independently.

But it also crosses domain boundaries:

- Assessment finalization is Wave 2.2B scope;
- TreatmentPlan lifecycle semantics are Wave 2.3 scope;
- creating a plan directly as `approved` bypasses the future draft/proposal/approval contract already identified in Wave 2.1.

Wave 2.2B must not redesign TreatmentPlan lifecycle prematurely.

Compatibility requirement for the first implementation slice:

- preserve current user-visible TreatmentPlan creation unless an explicit migration is approved;
- keep it in the same outer transaction as assessment finalization;
- do not move TreatmentPlan lifecycle authority into `AssessmentLifecycleService`;
- treat current plan creation as a compatibility orchestration side effect until Wave 2.3 owns it properly.

## 9. Current finalized-report access

Production now serves finalized assessment reports through the registered shared assessment route.

Verified authority behavior after PR #84:

- advisor/admin may open a finalized report;
- the active owner of the assessment vehicle may open a finalized report;
- unrelated authenticated users are denied;
- inactive former owners are denied;
- draft assessments remain unavailable to owners.

Production verification confirmed three historical finalized assessment report links opened successfully for the active owner.

Architecture finding: Wave 2.2B must preserve this object-level authorization while lifecycle internals move behind a service.

## 10. Historical ownership/privacy caveat

Current owner authorization is vehicle-scoped by **current active ownership**, not by the ownership row that existed when a historical assessment was created.

That means a future transferred vehicle may require a deliberate product/privacy rule for historical assessment portability.

Wave 2.2B1 does not assert that prior-owner assessment content is automatically safe for a new owner merely because the vehicle is the same.

Before historical assessment portability across ownership transfer is declared canonical, Aura should audit the rendered report for prior-owner personal/contact or consultation-specific information.

Wave 2.2B2 must not broaden current report visibility as part of lifecycle migration.

## 11. Canonical event gap

`services.event_emission` currently supports:

- `concern.*`;
- `evidence.*`;
- the four production `consultation.*` events.

Assessment events are not yet in the runtime taxonomy or PostgreSQL canonical subject/event constraint.

Wave 2.1 already approved the initial assessment family:

```text
assessment.created
assessment.finalized
```

with later:

```text
assessment.corrected
```

once a durable correction/addendum record exists.

Subject contract already approved:

```text
subject_type = vehicle_assessment
subject_id   = VehicleAssessment.id
```

Progression direction:

```text
not_applicable
```

Assessment lifecycle milestones are professional-record facts. They do not independently mean that vehicle mechanical health improved, deteriorated or resolved.

## 12. Draft-save event decision

A draft save is durable data persistence but is not a lifecycle milestone.

Wave 2.2B should **not** emit a canonical event for every field edit or every Save Draft action.

Reasons:

- it would create high-churn event noise;
- canonical events are longitudinal state/progression facts, not keystroke history;
- draft content can include detailed professional text that should not be copied into broad event payloads;
- draft state remains authoritative on `VehicleAssessment` and its child rows.

If future compliance requires field-level professional edit history, that should use a dedicated assessment audit/version mechanism rather than abusing canonical `VehicleEvent`.

## 13. Correction/addendum gap

No durable assessment correction/addendum model exists today.

No route supports an additive correction to a finalized assessment.

Wave 2.1 already prohibited a generic `reopened` assessment state.

The required future behavior is:

- original finalized assessment remains unchanged;
- correction/addendum is a new durable row referencing the finalized assessment;
- actor and timestamp are explicit;
- visibility is explicit;
- published correction/addendum itself is immutable; a further correction is another additive record;
- client-visible corrections are shown alongside the original finalized assessment/report rather than replacing it;
- canonical `assessment.corrected` is emitted only after the durable correction record exists.

Exact correction schema is implementation scope for Wave 2.2B3, not this audit.

## 14. Authority findings

Assessment professional-state authority belongs to advisor/admin identity under the current Aura authority model.

Owner and driver may view permitted client-safe finalized records but may not:

- create professional assessments;
- save professional assessment drafts;
- finalize assessments;
- create corrections/addenda;
- bypass consultation preconditions.

Rina/provider output may structure or explain available information, but may not create, finalize, correct or approve a professional assessment state.

## 15. Transaction boundary required by Wave 2.2B

Target creation:

```text
validate advisor + consultation + vehicle scope
        ↓
create draft VehicleAssessment with frozen vehicle context
        ↓
emit assessment.created
        ↓
flush
        ↓
commit once
```

Target finalization:

```text
validate advisor + draft + consultation + required fields
        ↓
set finalized fields
        ↓
emit assessment.finalized
        ↓
invoke legacy TreatmentPlan compatibility creation in same outer transaction
        ↓
flush
        ↓
commit once
```

If event emission or compatibility plan creation fails, assessment finalization must roll back.

Draft field saves remain transactional data persistence but do not emit canonical lifecycle events.

## 16. Historical backfill policy

Wave 2.2B should default to **no synthetic assessment event backfill**.

Existing finalized rows may contain `finalized_at` and `finalized_by`, but historical event creation is not necessary to prove the forward lifecycle and risks creating a mixed ledger whose source/provenance differs from live transactional emission.

Existing assessment rows remain readable as authoritative legacy state.

New canonical assessment events begin at the explicit Wave 2.2B cutover.

Any later deterministic backfill must be separately designed, reviewed and labelled as backfill provenance.

## 17. PostgreSQL contract requirement

Wave 2.2A proved that application taxonomy alone is insufficient: production PostgreSQL has a stricter canonical subject/event check constraint.

Therefore Wave 2.2B2 must include a PostgreSQL migration that:

- preflights existing canonical data;
- preserves concern/evidence/consultation pairings;
- permits only approved assessment subject/event pairings;
- enforces approved assessment state semantics;
- preserves `progression_direction=not_applicable`;
- rehearses upgrade, downgrade and re-upgrade;
- restores the exact prior production contract on downgrade.

SQLite-only CI is not sufficient for this slice.

## 18. Wave 2.2B audit conclusion

Aura already has the correct Assessment document and a strong de-facto immutable-finalization rule.

The missing architecture is lifecycle ownership and canonical longitudinal history.

The implementation direction is:

```text
advisor route / compatibility adapter
        ↓
AssessmentLifecycleService
        ↓
validate authority + consultation + source state
        ↓
mutate VehicleAssessment
        ↓
emit canonical assessment event when lifecycle state changes
        ↓
commit once
```

Draft content persistence must preserve the PR #82 hardening.

Finalized correction must be additive rather than reopening the document.

The companion Wave 2.2B state/event contract locks the exact forward rules before runtime implementation.
