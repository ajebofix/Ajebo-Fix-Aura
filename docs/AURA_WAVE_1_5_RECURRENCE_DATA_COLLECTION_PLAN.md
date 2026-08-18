# Aura Wave 1.5 — Reported Concern Recurrence Data Collection Plan

## Purpose

Convert the 18 Aug 2026 production audit result (`collect_more_data`) into a concrete collection plan for the first Wave 1.5 evaluation target:

> After an advisor resolves a Reported Concern, does that same Reported Concern reopen within 90 days?

This plan is for **data readiness**, not model implementation.

## Current production baseline

The first aggregate/read-only production audit observed:

- 1 vehicle;
- 1 Reported Concern row;
- 5 consultations;
- 1 vehicle assessment;
- 1 treatment plan;
- 2 reviewed evidence rows;
- 1 evidence link;
- 4 conversation records;
- 32 chat messages;
- 3 canonical VehicleEvent rows;
- all 3 canonical rows in the evidence family;
- 0 mechanical progression outcomes;
- 0 audited canonical concern-family rows;
- ~0.01 days of canonical longitudinal span.

The existing data is therefore insufficient to evaluate the recurrence target.

## What must be collected

For each future Reported Concern episode used by this target, Aura needs a trustworthy canonical sequence where applicable:

```text
concern.reported
      ↓
concern.review_started / concern.monitoring_started
      ↓
concern.resolved
      ↓
90-day observation window
      ↓
concern.reopened OR observed non-recurrence OR censored
```

The sequence does not require every optional intermediate state, but the `concern.resolved` timestamp and subsequent observation status must be unambiguous.

## Collection priority 1 — use the existing concern event contract consistently

No new parallel recurrence table should be created merely to collect labels.

New Reported Concern transitions must continue to emit canonical `VehicleEvent` rows through the existing transactional event-emission boundary.

Operations should prefer normal product usage over fabricated backfill:

- create genuine Reported Concerns when clients/drivers report them;
- use advisor review/monitoring transitions accurately;
- resolve only when the advisor determines the concern is actually resolved in Aura's care record;
- reopen the same canonical concern only when that same concern genuinely returns;
- do not create synthetic recurrence events to improve dataset size;
- do not fabricate historical event timestamps for legacy records.

## Collection priority 2 — expand multi-vehicle longitudinal use

The current production cohort is one vehicle. Predictive evaluation must not be based on one client's history.

The collection objective is therefore broader longitudinal adoption:

- more real vehicles under Aura monitoring;
- repeated use of Reported Concern workflows over time;
- complete advisor-reviewed resolution states;
- continued observation long enough to close 90-day windows;
- outcome diversity across vehicles, makes/models and operating patterns where the real client base provides it.

No user should be enrolled, retained or contacted merely to generate training data without the applicable product/privacy basis.

## Collection priority 3 — preserve follow-up observability

A resolved episode cannot be treated as a negative recurrence outcome merely because Aura did not receive another message.

For each 90-day window, the future target-specific auditor must distinguish:

- `positive`: same concern reopened within the window;
- `negative_observed`: full window elapsed with no reopen and observation remained valid;
- `censored_monitoring_ended`: vehicle/ownership/monitoring ended before window close;
- `censored_insufficient_followup`: 90 days have not elapsed;
- `censored_history_incomplete`: canonical event history cannot support a reliable label;
- `censored_corrected`: corrections make the episode unsuitable for evaluation.

Censored states are evaluation metadata, not client-facing health labels.

## Collection priority 4 — improve structured provenance without increasing prediction scope

Useful future pre-resolution context may come from domains already present in Aura, but those domains must not be treated as predictive features until their event/provenance boundaries are trustworthy.

Priority future canonical migrations should be chosen for operational value first and recurrence-evaluation value second. Likely useful families include:

1. treatment/intervention outcomes;
2. assessment/finalisation events;
3. maintenance completion/state changes;
4. verified DTC detection/clearance;
5. mileage-bearing vehicle events where observed reliably.

Consultation, conversation and evidence governance data may support care continuity, but raw text/media should remain outside the first recurrence feature contract.

## Data-quality checks required on every readiness rerun

A target-specific readiness report must measure at minimum:

- eligible resolved-concern episode count;
- distinct vehicles contributing eligible episodes;
- completed 90-day windows;
- positive recurrence count;
- observed non-recurrence count;
- censored count by reason;
- recurrence prevalence among observable outcomes;
- distribution of follow-up duration;
- repeated episodes per vehicle;
- missing canonical timestamps;
- correction frequency;
- provenance/source distribution;
- mileage availability at `t0` where considered;
- vehicle-level concentration (whether a small number of vehicles dominate the dataset);
- calendar-time concentration;
- class imbalance;
- duplicate/idempotency anomalies.

The report must remain aggregate-only.

## Leakage prevention during collection

Collection and future feature generation must preserve the prediction-time boundary.

For one resolved episode at `t0`, nothing after `t0` may appear in a feature vector. The future recurrence outcome is label data only.

Examples of prohibited leakage:

- using a `concern.reopened` event as an input to predict that same reopen;
- using treatment/assessment updates created after resolution;
- forward-filling mileage from a future service;
- using later chat/evidence text that explicitly says the concern returned;
- counting the 90-day outcome itself in pre-resolution aggregates.

## Evaluation split policy

When enough data exists:

- split by vehicle, not by individual episode;
- ensure no vehicle appears in both training and final evaluation sets;
- retain a later-time holdout cohort for forward validation;
- prevent multiple episodes from the same vehicle from leaking stable identity patterns across partitions.

## No arbitrary sample-count shortcut

This plan intentionally does not declare that “N rows means ready.”

Readiness depends on observed recurrence prevalence, number of distinct vehicles, follow-up completeness, outcome balance, desired confidence/calibration precision and the harm cost of false positives/false negatives.

Once the cohort is large enough to estimate prevalence meaningfully, Wave 1.5 must perform a target-specific sample-size/power and calibration-feasibility analysis before approving model development.

## Operational review cadence

The read-only readiness audit may be rerun periodically as real Aura usage grows. A target-specific recurrence audit should later replace broad guessing with explicit episode/outcome counts.

No automatic model training or prediction should be triggered by an audit crossing a threshold. Readiness remains a documented human go/no-go decision.

## Decision states

Each future target-specific review must end in one of:

- `collect_more_data` — target remains sensible but evidence is insufficient;
- `defer` — target is not currently supportable or not worth pursuing;
- `proceed_to_rules_baseline` — data is sufficient to build/evaluate an explainable non-ML baseline;
- `proceed_to_shadow_model_evaluation` — only after the rules baseline and evaluation contract are proven.

There is deliberately no direct `proceed_to_client_prediction` state.

## Immediate next engineering boundary

After this contract is accepted, the next permitted implementation is a **read-only target-specific recurrence eligibility auditor** that computes the episode/outcome/censoring aggregates above.

It may not:

- write labels into production tables;
- create prediction records;
- export row-level training data;
- expose vehicle/client identities;
- add a prediction API;
- change Rina behavior;
- introduce ML dependencies.

## Success condition for the collection phase

The collection phase succeeds when Aura has enough real, reviewed, longitudinal, multi-vehicle resolved-concern episodes to make a defensible decision about whether a recurrence baseline/model can be evaluated safely.

A documented decision to continue collecting or defer remains a valid Wave 1.5 outcome.
