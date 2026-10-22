# Aura Wave 2.2B — Vehicle Assessment Lifecycle Contract

**Parent epic:** #74  
**Delivery issue:** #88  
**Follows:** Wave 2.2A / #77  
**Status:** Contract-first; no runtime behavior changes in this PR

## 1. Objective

Move `VehicleAssessment` lifecycle authority behind one explicit domain service and extend Aura's canonical `VehicleEvent` ledger with reviewed assessment lifecycle facts.

Wave 2.2B does not redesign the assessment form. It formalizes the lifecycle Aura already partly enforces and closes the gaps exposed by production use: route-owned start/finalize logic, draft durability, finalized-record immutability, owner report authorization, and absence of canonical assessment history.

## 2. Existing production baseline

Current durable model rules:

- one `VehicleAssessment` per consultation (`consultation_id` is unique);
- assessment is vehicle-scoped and advisor-authored;
- persisted states are `draft` and `finalized`;
- finalized metadata includes `is_finalized`, `finalized_at`, and `finalized_by`;
- draft content includes frozen vehicle context, system statuses, risks, treatment options, cost/consequence analysis and professional recommendation;
- ordinary edit access is draft-only;
- finalization currently creates a `TreatmentPlan` in the same SQLAlchemy transaction;
- owner access to finalized reports is object-authorized by active vehicle ownership.

Production Wave 2.2A testing also proved that draft persistence must be treated as a durable workflow concern: malformed or partial repeating-field submissions must never silently erase existing draft content.

## 3. Locked assessment states

Wave 2.2B keeps the primary lifecycle intentionally small:

- `draft`
- `finalized`

No `approved`, `completed`, `reopened`, `cancelled`, or `archived` state is introduced in this slice.

A finalized assessment is a professional record boundary. It is not returned to `draft` through ordinary product behavior.

## 4. Approved canonical event family

The first assessment event family is:

- `assessment.started`
- `assessment.finalized`
- `assessment.addendum_recorded`

All use:

- `subject_type="vehicle_assessment"`
- vehicle scope inherited from the assessment row;
- `progression_direction="not_applicable"`.

These events record professional workflow/document facts. They do not independently assert that vehicle mechanical condition improved or deteriorated.

### 4.1 `assessment.started`

Meaning: a persisted assessment draft was created for an active consultation.

Required transition:

- `previous_state=None`
- `new_state="draft"`

Authority: advisor/administrator only.

Visibility: advisor by default. Client visibility is not required merely because a draft exists.

### 4.2 `assessment.finalized`

Meaning: the advisor locked the professional assessment as the reviewed record for the consultation.

Required transition:

- `previous_state="draft"`
- `new_state="finalized"`

Authority: assigned advisor/administrator only.

Visibility: a client-safe event may be `client` because finalized assessment existence is already visible to the owner. Event payload must not contain internal free-text findings, raw professional notes, or hidden treatment instructions.

### 4.3 `assessment.addendum_recorded`

Meaning: a dated, attributed additive correction/addendum was attached to a finalized assessment without rewriting the original finalized record.

Required state semantics:

- `previous_state="finalized"`
- `new_state="finalized"`

Authority: advisor/administrator only.

Visibility is explicit per addendum as client-safe or advisor-only. Canonical event payload records metadata/provenance, not unrestricted addendum body text.

This event requires a durable addendum/correction subject before implementation. Wave 2.2B1 locks the contract; Wave 2.2B3 may introduce the additive record after service cutover.

## 5. Lifecycle authority contract

A future `AssessmentLifecycleService` owns legal assessment transitions.

### 5.1 Start

Preconditions:

- consultation exists;
- consultation status is `in_progress`;
- actor has advisor/administrator authority for the vehicle;
- if the consultation is assigned to another advisor, Aura fails closed unless administrator override is explicitly supported;
- no assessment already exists for that consultation.

Effects in one transaction:

1. create `VehicleAssessment(status="draft", is_finalized=False)`;
2. freeze available vehicle identity/mileage context into the row;
3. emit `assessment.started`;
4. caller commits once.

If an existing draft exists, the start operation reuses it rather than creating a duplicate row/event. If an existing finalized assessment exists, start is rejected.

### 5.2 Save draft

Saving draft content is a content mutation inside `draft`, not a lifecycle transition.

Therefore ordinary draft saves do **not** emit a canonical lifecycle event in this slice. Emitting one event per save would pollute longitudinal history and create misleading operational volume.

Draft-save rules:

- advisor authority required;
- assessment must remain `draft`;
- repeating risk/treatment groups are validated before replacement;
- malformed/partial submissions preserve existing child rows;
- absent scalar fields do not erase stored values;
- save is atomic;
- finalized assessments cannot be edited through this path.

If future audit-grade draft revision history is required, it should be a dedicated version/audit mechanism rather than canonical vehicle-progression events.

### 5.3 Finalize

Preconditions:

- assessment exists and is `draft`;
- actor has required advisor authority;
- all required system-status fields are present;
- assessment belongs to the active consultation/vehicle context;
- finalization has not already occurred.

Effects in one transaction:

1. set `status="finalized"`;
2. set `is_finalized=True`;
3. set `finalized_at`;
4. set `finalized_by`;
5. emit `assessment.finalized`;
6. perform any approved downstream TreatmentPlan creation under an explicitly defined boundary;
7. commit once.

Wave 2.2B must decide whether automatic TreatmentPlan creation stays coupled to assessment finalization or moves to Wave 2.3 proposal/approval logic. Until that decision is implemented, no new treatment-plan semantics should be invented inside the assessment service.

## 6. Finalized-record immutability

Once finalized:

- ordinary assessment fields are not editable;
- risk rows are not replaced;
- treatment-option rows are not rewritten;
- `finalized_at` and `finalized_by` are not reset;
- the record is not moved back to `draft`;
- historical report rendering reflects the finalized record plus explicitly modeled addenda/corrections only.

Corrections must be additive and attributed.

## 7. Addendum/correction boundary

Production use demonstrated why finalized assessments need an additive correction path: a professional record may require clarification after it is locked, but silently reopening or rewriting it would destroy auditability.

Wave 2.2B therefore reserves a first-class addendum concept with at least:

- assessment reference;
- author/advisor reference;
- created timestamp;
- reason/category;
- client visibility classification;
- additive text/content;
- optional correction linkage to the field/section being clarified.

The original finalized assessment remains intact.

No addendum schema is introduced by this contract PR; implementation belongs to a later narrow slice after service/event cutover.

## 8. Canonical event payload boundaries

`assessment.*` events may include compact metadata such as:

- assessment id;
- consultation id;
- finalized timestamp;
- reviewed risk/treatment-option counts if needed;
- addendum id for `assessment.addendum_recorded`;
- source route/service identifier.

They must not include:

- raw professional recommendation text;
- unrestricted risk descriptions/likely-cause text;
- internal treatment instructions;
- VIN when not required for event semantics;
- owner contact information;
- chat/Rina content;
- raw evidence media or extraction payloads.

## 9. Transaction and idempotency contract

- `AssessmentLifecycleService` never commits independently;
- caller owns the transaction;
- assessment mutation and canonical event emission succeed or fail together;
- event fingerprint/idempotency uses the existing canonical emitter;
- replay of `assessment.started` or `assessment.finalized` must not create duplicate facts;
- concurrent duplicate assessment creation remains blocked by the one-assessment-per-consultation database constraint;
- PostgreSQL event CHECK constraints must be extended and verified before production cutover.

## 10. Authorization and object isolation

Required tests:

- assigned advisor can start/save/finalize;
- unrelated owner cannot mutate assessment;
- driver cannot mutate assessment;
- unrelated advisor fails closed where assignment rules require it;
- active owner can access only finalized reports for the owned vehicle;
- former/inactive owner cannot access finalized report;
- cross-vehicle assessment ids do not bypass object authorization.

## 11. Historical data/backfill policy

Do not synthesize `assessment.started` or `assessment.finalized` for historical rows merely because current state/timestamps exist.

A deterministic backfill would require provenance sufficient to prove actor, timing and transition semantics. Until such a rule is separately approved, historical assessments remain readable legacy state without fabricated canonical events.

New post-cutover assessment transitions emit canonical events normally.

## 12. Implementation order after this contract

### Wave 2.2B2A — Assessment lifecycle service

- implement `AssessmentLifecycleService.start` and `.finalize`;
- reuse the hardened draft-persistence behavior;
- extend `services.event_emission` with approved `assessment.*` types;
- add PostgreSQL contract migration and verifier;
- add authority, transition, idempotency and rollback tests.

### Wave 2.2B2B — Route cutover

- cut `admin_start_assessment` and `admin_finalize_assessment` over to the service;
- preserve existing URLs/UI;
- keep draft edit behind the hardened persistence adapter until folded cleanly into the service boundary;
- production-test start → draft save → reopen draft → finalize → owner report.

### Wave 2.2B3 — Finalized assessment addendum

- add durable additive correction/addendum record;
- emit `assessment.addendum_recorded`;
- preserve original finalized content;
- verify advisor/client visibility and audit trail.

## 13. Non-goals

- no autonomous diagnosis;
- no Rina assessment approval/finalization;
- no predictive scoring;
- no automatic treatment recommendation approval;
- no rewriting historical finalized assessments;
- no UI redesign;
- no migration of TreatmentPlan lifecycle before Wave 2.3.

## 14. Definition of done for Wave 2.2B

Wave 2.2B is complete when:

- assessment start/finalize transitions are service-owned;
- canonical assessment events are transactionally emitted;
- PostgreSQL enforces the assessment event contract;
- draft persistence remains durable and regression-tested;
- finalized assessments are immutable through ordinary edit paths;
- owner finalized-report authorization remains object-scoped;
- additive correction/addendum workflow exists and is audited;
- production smoke test proves the lifecycle without fabricated backfill.
