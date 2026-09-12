# Aura Wave 2.5 — Synthetic Longitudinal Validation

## Status

Synthetic test harness only. This document does not change the production Wave 2.5 readiness contract.

## Purpose

Aura does not yet have a real multi-vehicle user cohort. The synthetic longitudinal cohort exists so the 90-day Reported Concern recurrence gate can be exercised end-to-end before real operational evidence exists.

It validates mechanics, not market or clinical evidence.

## Isolation rule

The cohort must run in an isolated validation database, separate from Aura's real production PostgreSQL database.

Synthetic records must never be copied into the real production readiness cohort and must never be described as real clients, real vehicles, real concern episodes or real outcomes.

## Provenance

Every synthetic canonical event uses:

- `source = synthetic_wave_2_5`
- `data_origin = synthetic`
- `dataset = wave_2_5_validation`
- a deterministic `scenario_id`

Synthetic domain records are also visibly labelled.

## Deterministic scenarios

The validation cohort contains four vehicles and four resolved concern episodes:

1. `recurrence_positive_01` — resolved, then reopened 35 days later.
2. `recurrence_positive_02` — resolved, then reopened 80 days later, close to the 90-day boundary.
3. `non_recurrence_01` — resolved with a complete observable 90-day window and no reopen.
4. `censored_followup_01` — resolved too recently to complete the 90-day follow-up window.

At the fixed audit cutoff `2026-09-12T00:00:00`, the expected recurrence result is:

- resolved episodes: 4
- vehicles with resolved episodes: 4
- completed 90-day windows: 3
- positive recurrence outcomes: 2
- observed non-recurrence outcomes: 1
- labelled outcomes: 3
- censored outcomes: 1 (`censored_insufficient_followup`)

## Decision boundary

If the existing Wave 2.5 logic reaches `proceed_to_rules_baseline` on this isolated synthetic database, the synthetic wrapper reports only:

`synthetic_validation_passed`

That result means the recurrence-gate logic behaves correctly for the seeded scenarios.

It does **not** mean:

- production has enough real longitudinal evidence;
- the real rules baseline is approved;
- predictive implementation is approved;
- Rina may make predictive claims;
- synthetic results may be presented as traction or real-world model evidence.

The production Wave 2.5 gate remains governed by real production data and its existing decision contract.
