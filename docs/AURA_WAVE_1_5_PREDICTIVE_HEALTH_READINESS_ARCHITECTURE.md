# Aura Wave 1.5 — Predictive-Health Readiness Architecture

**Status:** Architecture / data-readiness gate  
**Runtime implementation:** Not approved  
**Parent:** Issue #33  
**Depends on:** Wave 1.1 architecture audit, Wave 1.2 canonical progression contracts, Wave 1.3 Rina authority/memory contracts, reviewed production/pilot data

## 1. Decision this wave must make

Wave 1.5 does **not** begin with a predictive model.

It begins with one decision:

> Does Aura have enough structured, reviewed, outcome-linked and temporally valid data to support a narrowly defined predictive-health capability without inventing certainty?

The allowed final decisions are:

```text
proceed
collect_more_data
defer
```

`collect_more_data` and `defer` are successful outcomes when the evidence does not justify implementation.

## 2. Product boundary

Aura may eventually identify evidence-backed patterns that help an advisor decide what deserves review.

Permitted future problem classes may include:

- recurrence risk for an already-described Reported Concern;
- maintenance-risk progression from verified maintenance state and elapsed usage/time;
- deterioration pattern detection from canonical longitudinal events;
- advisor-review prioritisation from explicit operational risk signals;
- confidence-aware pattern surfacing with a mandatory abstention path.

Wave 1.5 does **not** authorise:

- predicting a failed component from sparse records;
- declaring a mechanical diagnosis before inspection;
- presenting generic DTC definitions as vehicle-specific failure predictions;
- unsupported remaining-life estimates;
- autonomous treatment approval;
- autonomous repair instructions;
- client-facing certainty labels such as "will fail soon";
- using client anxiety, writing style or emotional state as a mechanical-risk feature;
- marketing predictive-health claims before validation.

## 3. Current known architecture constraints

Wave 1.2 deliberately proved the canonical progression pattern with Reported Concerns first. Other initial event-source families were reserved for later migration and must reuse the same event, authority, visibility, idempotency and transaction contracts.

Wave 1.4 added canonical evidence governance events (`evidence.reviewed`, `evidence.linked`), but those events are deliberately `not_applicable` to mechanical progression. Evidence review/linkage is therefore not itself a deterioration/improvement label.

The secure image-evidence production slice now gives Aura governed reviewed evidence, but current production/pilot event volume, time coverage, reviewed outcome coverage and class balance have not yet been measured under a predictive-readiness report.

Therefore:

> **No predictive implementation is approved merely because Wave 1.4 is production-live.**

## 4. Prediction contract required before implementation

Any future predictive capability must be defined by an explicit contract before code is written.

At minimum, the contract must state:

```text
prediction_name
prediction_target
subject_type
subject_vehicle_id
as_of_time
prediction_horizon
permitted_inputs
prohibited_inputs
required_source/provenance
label_definition
outcome_window
confidence_method
abstention_conditions
advisor_review_requirement
client_visibility_policy
rules/model_version
feature_version
rollback_flag
```

### Example of an acceptable narrow target

```text
Question:
Has this already-reported concern shown an evidence-backed recurrence pattern that warrants advisor review?

Output:
recurrence_pattern_detected | no_supported_pattern | insufficient_evidence
```

This is materially different from:

```text
The steering rack will fail in 30 days.
```

The second statement is outside Aura's current evidence and authority boundary.

## 5. Data-readiness inventory

Before target approval, Aura must produce a reproducible inventory from canonical records.

### Event coverage

Measure counts by:

- canonical event type;
- subject type;
- source/provenance;
- vehicle;
- vehicle class where available;
- calendar period;
- client/advisor visibility;
- reviewed/unreviewed state where applicable.

### Longitudinal coverage

Measure:

- number of vehicles with more than one meaningful event;
- observation duration per vehicle;
- follow-up duration after intervention;
- recurrence windows;
- gaps in timeline coverage;
- vehicles lost to follow-up.

### Outcome coverage

Measure whether records contain reviewed outcomes such as:

- concern resolved / remained active / recurred;
- consultation completed;
- assessment finalised;
- treatment action completed;
- post-intervention observation;
- DTC cleared/reappeared where canonical integration exists;
- maintenance completed/overdue where canonical integration exists.

An input record without a trustworthy later outcome cannot automatically become a training label.

### Data quality

Measure:

- missing fields;
- contradictory states;
- stale data;
- duplicated events;
- out-of-order timestamps;
- unknown/unverified source;
- legacy rows without canonical provenance;
- evidence reviewed after the outcome window;
- unresolved corrections/disputes.

### Label quality

For each candidate prediction target, report:

- label definition;
- label source;
- who or what established the outcome;
- review status;
- time between input and outcome;
- ambiguous labels;
- class distribution;
- inter-advisor disagreement where measurable.

## 6. Source and provenance policy

Only inputs with known origin and appropriate authority may contribute to predictive readiness.

Potential input families must be classified as one of:

```text
verified_canonical
reviewed_professional
client_reported
provider_verified
provider_unverified
legacy_unscoped
unknown
```

Unknown or unverified inputs may be retained operationally but must not silently receive the same predictive weight as reviewed professional facts.

Raw provider text, unreviewed uploaded media and free-form AI output are not authoritative labels.

## 7. Feature policy

Any future feature must be:

- deterministic from versioned source data;
- vehicle-scoped;
- reproducible at a historical `as_of_time`;
- traceable to supporting canonical records;
- unavailable if source visibility/authority would make the result unsafe to expose.

### Prohibited predictive features

Unless a later architecture decision explicitly proves necessity, fairness and lawful purpose, do not use:

- customer name, email or phone;
- wealth/status proxies;
- occupation;
- marketing preferences;
- emotional state or anxiety;
- writing style;
- raw advisor private notes;
- raw Rina prompts/responses;
- protected or unrelated personal attributes;
- future events that occurred after the prediction `as_of_time`.

## 8. Leakage prevention

Predictive evaluation must treat time and vehicle identity as first-class boundaries.

Prohibited shortcuts include:

- random row-level train/test splits that put events from the same vehicle on both sides without justification;
- using review decisions created after the prediction time as input features;
- using treatment completion to predict whether treatment will be completed;
- using final concern state to predict the same final concern state;
- backfilled data whose original availability time cannot be reconstructed.

Preferred evaluation should use vehicle-held-out and/or time-forward partitions appropriate to the chosen target.

## 9. Baseline-before-model rule

No ML/LLM model is justified until a transparent rules baseline exists for the same target.

The baseline must be:

- deterministic;
- explainable;
- versioned;
- evaluable against the same held-out outcomes;
- capable of returning `insufficient_evidence`.

A learned model must demonstrate a meaningful improvement over the rules baseline without introducing unacceptable false-positive, false-negative, calibration, privacy or explainability costs.

## 10. Confidence and abstention

Every future predictive result must support abstention.

At minimum:

```text
result
confidence_band
abstained
abstention_reason
supporting_event_ids
as_of_time
rules_or_model_version
review_required
```

Low confidence, stale context, missing provenance, conflicting evidence or insufficient longitudinal coverage must fail toward:

```text
insufficient_evidence
```

not toward a guessed prediction.

## 11. Advisor authority

Predictive output is decision support, not authority.

A surfaced pattern may:

- prioritise advisor review;
- identify supporting records;
- suggest that further assessment is warranted.

It may not:

- approve a treatment plan;
- create a diagnosis;
- authorise a repair;
- silently change Reported Concern progression;
- become client-visible without the approved visibility policy.

Advisor corrections must be recorded separately from the original prediction so evaluation can distinguish system output from professional review.

## 12. Client-facing boundary

Wave 1.5 readiness work is advisor/internal by default.

No client-facing predictive card, health forecast, countdown, probability or warning is approved in the readiness phase.

Any later client surface must use calm non-diagnostic wording, disclose uncertainty, link to supporting reviewed records, and have an immediate rollback/disable switch.

## 13. Privacy and retention review

The readiness report must confirm:

- lawful purpose for each predictive input family;
- whether historical records remain within their approved retention purpose;
- whether model/evaluation datasets require additional retention or consent treatment;
- cross-vehicle isolation;
- whether provider processing sends unnecessary personal information;
- deletion/correction effects on future training/evaluation datasets.

Training/evaluation exports must not become an ungoverned secondary copy of Aura's private records.

## 14. Required readiness report

The first implementation deliverable after this architecture gate is a **read-only data-readiness report**, not a model.

It must contain:

1. event volume and coverage;
2. longitudinal duration;
3. reviewed outcome/label availability;
4. missingness and inconsistency analysis;
5. provenance quality;
6. class imbalance;
7. leakage risks;
8. privacy/retention review;
9. candidate target feasibility;
10. recommendation: `proceed`, `collect_more_data`, or `defer`.

The report must be reproducible and must not mutate production data.

## 15. Implementation gates after the report

### If `defer`

- no predictive runtime code;
- document why;
- identify the exact events/outcomes that must be collected;
- revisit only after a defined evidence window.

### If `collect_more_data`

- define missing canonical event integrations and review workflows;
- improve data capture without manufacturing labels;
- do not expose predictive claims.

### If `proceed`

The next sequence is still constrained:

```text
prediction contract
    ↓
rules baseline
    ↓
offline held-out evaluation
    ↓
false-positive / false-negative harm review
    ↓
calibration / abstention review
    ↓
advisor-only shadow mode
    ↓
production review
    ↓
only then consider a client-safe surface
```

## 16. Non-goals of the opening Wave 1.5 PR

- no new model;
- no ML dependency;
- no prediction table/migration;
- no background prediction job;
- no client-facing score;
- no automated alert;
- no training-data export;
- no Rina predictive prompt;
- no new treatment authority;
- no marketing claim.

## 17. Architecture decision

Wave 1.5 is a **readiness and governance wave first**.

Aura must earn the right to predict from reviewed longitudinal evidence. If the current dataset cannot support a narrow, testable and harm-aware target, the correct engineering decision is to collect more data or defer.
