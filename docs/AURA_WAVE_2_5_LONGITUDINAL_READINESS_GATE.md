# Aura Wave 2.5 — Longitudinal Coverage and Data-Quality Gate

Parent epic: #74  
Implementation issue: #130  
Production rerun: #131  
Predictive-readiness source of truth: #33  
Conditional rules baseline: #132

## 1. Purpose

Wave 2.5 does not begin by building a predictive model. It asks whether the real canonical history produced by Aura is now sufficient to evaluate the already-approved `reported_concern_recurrence_90d_v1` target responsibly.

A successful Wave 2.5 result may still be `collect_more_data`.

The gate must remain read-only, aggregate-only, vehicle-scoped and provenance-aware. It must not manufacture history to satisfy the roadmap.

## 2. Approved target remains unchanged

The Wave 1.5 target contract remains the source of truth:

> For one advisor-resolved Reported Concern, does that same canonical Reported Concern reopen within 90 days after `concern.resolved`?

This is a recurrence/follow-up-priority target. It is not a component-failure prediction, diagnosis, remaining-life estimate or repair recommendation.

Positive, observed-negative and censored/unknown outcomes retain their existing definitions. In particular, an episode with incomplete follow-up must never be silently treated as a negative.

## 3. Current canonical event families

Wave 2.5 measures the actual post-Wave-2 event taxonomy rather than the older placeholder family names used by the first Wave 1.5 inventory.

Current canonical families expected from the implemented lifecycle are:

| Family | Durable subject |
| --- | --- |
| `concern` | `reported_concern` |
| `consultation` | `consultation` |
| `assessment` | `vehicle_assessment` |
| `treatment` | `treatment_plan` |
| `treatment_action` | `treatment_action` |
| `driver_observation` | `driver_checkin` |
| `care_signal` | `vehicle_health_alert` |
| `priority` | `priority_request` |
| `evidence` | `vehicle_evidence` |

The audit may report additional verified families if they exist. It must not fabricate missing families or convert read-time projections into canonical facts.

## 4. What the auditor measures

`scripts/audit_wave_2_5_readiness.py` composes two views:

1. the current canonical `VehicleEvent` ledger;
2. the existing target-specific 90-day Reported Concern recurrence eligibility audit.

The canonical-ledger section measures only aggregates:

- event counts by event type and current family;
- distinct vehicles represented;
- events per represented vehicle;
- top-vehicle concentration;
- per-vehicle observation span;
- current-family coverage;
- expected subject-type mismatches;
- required canonical-field missingness;
- aggregate source, visibility and actor classifications;
- lifecycle outcome-event coverage;
- deliberately narrower mechanical-outcome coverage.

The target-specific section retains the existing recurrence contract:

- resolved concern episodes;
- distinct vehicles with resolved episodes;
- completed 90-day windows;
- positive recurrence outcomes;
- observed non-recurrence outcomes;
- censored outcomes and reasons;
- labelled-outcome concentration.

## 5. Outcome semantics

Wave 2.5 must not treat operational closure as proof of mechanical success.

Examples such as `consultation.completed`, `care_signal.resolved` and `priority.resolved` are useful lifecycle outcomes, but they are not automatically mechanical outcomes.

The first audit therefore keeps a narrower mechanical/outcome-bearing set for readiness context:

- `concern.resolved`;
- `concern.reopened`;
- `treatment.outcome_recorded`.

This distinction prevents Aura from learning that a queue item disappearing, a priority request closing or an advisor projection changing means a vehicle mechanically improved.

## 6. Decision states

The Wave 2.5 report returns exactly one decision:

### `defer`

Used when the canonical ledger itself is structurally unreliable for target evaluation, for example:

- the canonical event table/required contract is absent;
- required canonical fields are missing on stored events;
- current canonical family-to-subject contracts are violated.

No rules baseline is permitted until the integrity problem is repaired.

### `collect_more_data`

Used when the canonical ledger is measurable but the approved recurrence target still has target-specific collection blockers, such as:

- no eligible resolved concern episodes;
- no completed 90-day follow-up windows;
- no observed target outcomes;
- no positive recurrence outcomes;
- no observed non-recurrence outcomes;
- single-vehicle or empty target cohort.

This is the expected outcome while genuine longitudinal production history is still sparse.

### `proceed_to_rules_baseline`

Used only when the automated structural and target-eligibility blockers are clear.

This decision permits the next **offline deterministic rules-baseline evaluation only**. It does not approve predictive deployment and does not imply that a statistically useful model exists.

Human review of sample adequacy, calibration feasibility, harm tolerance and evaluation design still applies under Issue #33 before any later capability can advance beyond the baseline/evaluation stage.

## 7. Family coverage is context, not a fabricated prerequisite

Wave 2.5 reports missing current event families because broad longitudinal coverage matters. However, the approved target is Reported Concern recurrence.

A missing unrelated family is therefore an advisory data-coverage gap rather than permission to synthesize events. The target gate is determined from genuine target-specific outcomes plus canonical integrity.

This prevents roadmap completion pressure from becoming synthetic history.

## 8. Privacy boundary

The Wave 2.5 report must not contain:

- vehicle IDs or VINs;
- Reported Concern/subject IDs;
- ownership or user IDs;
- names, email addresses or phone numbers;
- raw concern text;
- chat/Rina text;
- raw event payloads;
- raw evidence/media identifiers;
- financial/client-status attributes.

The script may use row identity internally to count cohorts and classify target episodes, but emitted output is aggregate-only.

## 9. Explicitly prohibited labels and features

The following remain non-canonical and must not become outcomes or labels:

- Alert Center computed projections;
- `PriorityScoringEngine` score/band;
- care-plan priority entitlement;
- Rina `monitor|flag|escalate` guidance;
- legacy `driver_score`;
- client anxiety/urgency language;
- elapsed time alone without observable follow-up.

No client-facing prediction, prediction API, background score, automated treatment action or Rina predictive behavior is introduced by Wave 2.5A.

## 10. Production sequence

1. Merge the aggregate/read-only audit and regression coverage from #130.
2. Confirm Railway production remains healthy after the code-only deployment.
3. Run the Wave 2.5 audit against production PostgreSQL from an authorised deployment shell.
4. Record the aggregate result on #131, #33 and parent #74.
5. If the result is `collect_more_data`, continue real longitudinal collection and rerun later.
6. If the result is `defer`, repair the identified integrity problem before any target evaluation.
7. Only if the result is `proceed_to_rules_baseline` may #132 open for offline deterministic baseline evaluation.

## 11. Definition of done for Wave 2.5A

Wave 2.5A is complete when:

- current canonical family taxonomy is measured correctly;
- target-specific recurrence readiness is composed into one report;
- subject-contract and required-field integrity failures fail closed;
- outcome semantics distinguish operational closure from mechanical outcome evidence;
- aggregate/privacy/read-only behavior is regression tested;
- the existing recurrence-readiness CI covers the new Wave 2.5 script/tests;
- the code is merged and production remains healthy.

Production data sufficiency itself is decided in #131. No data is invented to make that decision favourable.
