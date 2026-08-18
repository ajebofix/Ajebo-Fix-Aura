# Aura Wave 1.5 — Reported Concern Recurrence Prediction Target Contract

## Status

**Evaluation target defined. Predictive implementation is not approved.**

This contract exists so Aura can collect and evaluate the right longitudinal evidence without drifting into unsupported diagnosis or generic failure prediction.

Production readiness audit result on 18 Aug 2026: `collect_more_data`.

## Target statement

For one advisor-resolved Reported Concern, estimate whether **that same Reported Concern will be reopened within 90 days after its canonical `concern.resolved` event**.

This is a recurrence/follow-up-priority target. It is not a prediction that a component will fail, not a diagnosis, and not a remaining-life estimate.

## Prediction unit

One **resolved Reported Concern episode**.

The prediction timestamp (`t0`) is the `occurred_at` timestamp of the canonical:

```text
concern.resolved
```

event for that Reported Concern subject.

The prediction must be based only on information known at or before `t0`.

## Outcome definition

### Positive outcome

`recurrence_within_90_days = true` only when the **same canonical Reported Concern subject** receives:

```text
concern.reopened
```

with `occurred_at > t0` and `occurred_at <= t0 + 90 days`.

### Negative outcome

`recurrence_within_90_days = false` only when:

1. a full 90-day follow-up window has elapsed after `t0`;
2. no canonical `concern.reopened` event exists for that subject in the window; and
3. Aura has not lost the ability to observe the vehicle for reasons that make the outcome unknowable.

### Censored / unknown outcome

The episode must remain `unknown` / censored rather than be labelled negative when any of the following applies:

- fewer than 90 days of follow-up have elapsed;
- ownership/stewardship ended or vehicle monitoring was revoked before the window closed;
- canonical event history is incomplete for the episode;
- timestamps are invalid or materially ambiguous;
- a correction invalidates the apparent resolved/reopened sequence;
- the concern identity cannot be proven to be the same canonical subject.

Unknown/censored episodes must never be silently converted into negatives.

## Intended operational use

If the target is later validated, the permitted product use is **advisor follow-up prioritisation**.

A future output may say, for example:

```text
recurrence_follow_up_priority = elevated
```

with evidence/provenance and an explicit abstention state.

It must not say:

- “this part will fail”;
- “the vehicle is going to break down”;
- “the previous repair failed”;
- “replace component X”;
- “the cause is Y”;
- or any equivalent mechanical diagnosis/repair conclusion.

## Permitted feature families

Only time-valid, vehicle-scoped, provenance-bearing information available at or before `t0` may be considered.

Initial candidate feature families are:

- prior canonical concern-event counts for the same vehicle;
- prior reopen count for the same Reported Concern subject;
- elapsed time between the subject's canonical report/review/resolution events;
- prior resolved/reopened concern episodes for the vehicle;
- vehicle mileage observed at or before `t0`, when timestamped and trustworthy;
- structured vehicle profile attributes available before `t0`;
- reviewed canonical DTC/maintenance/treatment/evidence signals, only after those domains are formally migrated and their provenance rules are approved.

Feature eligibility does not imply model approval. Each feature family must survive leakage, missingness, provenance and harm review first.

## Prohibited inputs

The first target must not use:

- any event or field occurring after `t0`;
- raw Reported Concern free text as a learned feature;
- raw chat messages or Rina-generated language;
- emotional tone, anxiety, urgency phrasing or sentiment;
- client name, email, phone number, payment history, wealth/profession or other personal-status proxies;
- advisor identity as a quality/risk shortcut;
- raw evidence media, OCR/transcription or unreviewed extraction output;
- unverified provider data;
- future-filled mileage or maintenance values;
- another vehicle's records in the feature vector;
- hidden prompt/provider metadata;
- treatment decisions made after `t0`.

## Leakage boundary

All feature generation must accept an explicit `as_of_time = t0` and fail closed when a source cannot prove temporal availability.

Train/validation/test splitting must be performed at the **vehicle level**, not merely at the event/episode level, so the same vehicle cannot leak its history into both training and evaluation partitions.

Evaluation must also include a forward-time holdout once enough data exists.

## Authority and visibility

- Prediction/evaluation scope is vehicle-specific.
- Advisor/administrator authority is required for any future surfaced recurrence priority.
- No owner/driver client-facing prediction is authorised by this contract.
- Prediction output cannot mutate Reported Concern state, progression, assessment, treatment or evidence review state.
- Advisor correction/override remains authoritative and must be auditable if a future prediction surface is introduced.

## Abstention contract

A future baseline/model must abstain when required context is missing, stale, contradictory, outside the supported cohort or below confidence/calibration thresholds.

`insufficient_evidence` is an acceptable and expected result.

## Current data-readiness state

The first production audit showed:

- 1 vehicle;
- 3 canonical events;
- all 3 events in the `evidence` family;
- 0 canonical mechanical progression outcomes;
- 0 canonical `concern.*` rows in the audited production ledger;
- only ~0.01 days of observed canonical-event span.

Therefore there are currently **zero eligible resolved-concern recurrence episodes** for this target in the audited canonical dataset.

## Implementation gate

No predictive implementation may begin until a target-specific read-only audit proves that Aura has a sufficient multi-vehicle cohort containing:

- canonical `concern.resolved` episodes;
- completed 90-day follow-up windows;
- both positive recurrence and observed non-recurrence outcomes where the real prevalence permits;
- censored outcomes measured separately;
- adequate temporal spread;
- known missingness/provenance;
- no material leakage path;
- enough outcome volume to support a statistically defensible baseline/evaluation design.

No arbitrary row count is declared here. Required sample size must be derived from observed recurrence prevalence, intended metric precision, calibration needs and harm tolerance rather than chosen to satisfy a roadmap date.

## First allowed evaluation sequence

Once data readiness is sufficient:

1. deterministic rules-based baseline;
2. offline target-specific evaluation;
3. calibration and abstention review;
4. false-positive/false-negative harm review;
5. vehicle-level and forward-time holdout evaluation;
6. advisor-only shadow mode;
7. explicit go/no-go decision before any client-facing behavior.

## Explicit non-goals

This contract does not approve:

- an ML library;
- a training pipeline;
- a prediction table/API;
- background scoring;
- a client warning/health score;
- predictive Rina behavior;
- automated treatment approval;
- remaining-life estimates;
- component-failure prediction;
- diagnosis.

## Source of truth

Canonical domain state remains in the existing Aura care models. Canonical `VehicleEvent` remains the append-oriented progression record. Any future recurrence feature or label builder must derive from those existing sources rather than introduce a parallel history system.
