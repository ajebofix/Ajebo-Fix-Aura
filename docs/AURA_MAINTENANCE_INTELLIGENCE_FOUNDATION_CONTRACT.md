# Aura Maintenance Intelligence Foundation Contract

Issue: #145  
Parent: #144  
Status: M1 architecture contract

## 1. Objective

Aura Maintenance Intelligence must answer one bounded question:

> Given this vehicle's verified identity, latest verified odometer, verified maintenance schedule knowledge, and actual service history, which maintenance items are `upcoming`, `due`, `overdue`, or `unknown`, and what evidence produced that state?

This is deterministic maintenance monitoring. It is not predictive failure detection, mechanical diagnosis, repair prescribing, or remaining-life estimation.

## 2. Existing production foundation

Aura already has several pieces that must be extended rather than replaced:

- `Car` is the canonical vehicle identity and owns `current_mileage`.
- `VehicleProfile` stores decoded identity enrichment and provenance.
- `MileageObservation` plus `Car.current_mileage` defines current-odometer semantics. Historical mileage observations never roll the present odometer backward.
- service history is stored as canonicalized `VehicleEvent` rows with `event_type="service"`, odometer snapshots, occurrence time, source and verification metadata.
- `MaintenanceSchedule` exists as a vehicle-scoped table with `service_name`, `due_mileage`, `due_date`, `completed_at`, `status`, and `source`.
- `MaintenanceProvider` exists as a provider abstraction. The only current implementation, `MockMaintenanceProvider`, is development/test data and is not production maintenance knowledge.
- `CareSignalLifecycleService` owns the durable `maintenance_monitoring` care-signal lifecycle.

## 3. Current semantic gaps

### 3.1 Schedule knowledge and evaluated state are conflated

The current `MaintenanceSchedule` row mixes a service concept, due point, completion timestamp, state and source in one vehicle-specific record. It does not represent reusable verified maintenance knowledge independently from a vehicle's evaluated maintenance state.

### 3.2 Production health still uses a generic interval

`services.vehicle_intelligence` currently defines:

```python
SERVICE_INTERVAL_KM = 12_000
```

It treats the latest recorded service of any type as a generic maintenance baseline. That rule is a temporary compatibility shortcut, not verified OEM maintenance knowledge, and must not remain the authoritative maintenance-intelligence model.

### 3.3 Care-signal evaluation depends on prose

`CareSignalService` currently derives maintenance monitoring by searching health-risk reason strings for `overdue`. Typed maintenance state must replace this prose-coupled trigger.

### 3.4 Service records do not yet have a stable maintenance item key

Existing service history preserves service type text and strong provenance, but schedule matching cannot depend on free-form labels such as `Service A`, `oil change`, or `engine oil service` being textually identical.

## 4. Domain boundaries

Maintenance Intelligence has four distinct concepts.

### 4.1 Maintenance Knowledge

A reusable verified rule describing when one maintenance item is expected for an applicable vehicle configuration.

Maintenance knowledge is not a service record and is not a due-state occurrence.

Minimum conceptual fields:

- `maintenance_item_key` — stable machine key such as `engine_oil`, `brake_fluid`, `service_a`;
- client-safe display name;
- applicability scope;
- mileage interval and/or time interval;
- source name and source reference;
- source type (`oem`, `advisor_verified`, `provider_verified`, `manual_reference`);
- verification status;
- verified-by actor and verification timestamp when human-reviewed;
- effective/version metadata;
- notes restricted to advisor-safe factual context.

No rule may become production-active merely because a provider returned it.

### 4.2 Service History

A durable fact that maintenance or repair work was recorded as having occurred at a time and odometer snapshot.

Service history remains canonical `VehicleEvent` evidence. It must not be rewritten into schedule knowledge.

Each service row may eventually carry a normalized `maintenance_item_key` when an advisor or deterministic approved mapping can establish that classification. Unknown/ambiguous service labels stay unmatched rather than being guessed.

### 4.3 Maintenance State

A deterministic evaluation result for one vehicle + one verified maintenance item rule at one evaluation time.

Allowed states:

- `upcoming`
- `due`
- `overdue`
- `unknown`

`unknown` is a first-class safe result, not an error.

### 4.4 Care Signal

A durable monitoring occurrence managed by `CareSignalLifecycleService` when typed maintenance-state policy requires advisor/client attention.

The maintenance state engine does not directly mutate unrelated health, concern, consultation, assessment or treatment state.

## 5. Vehicle applicability contract

A maintenance rule must declare what vehicle identity is required to apply it.

Supported applicability dimensions may include:

- manufacturer/brand;
- model;
- model year or bounded year range;
- trim;
- engine/fuel configuration;
- transmission/drivetrain when materially required;
- explicit `car_id` override for advisor-verified vehicle-specific rules.

The engine must fail closed when required identity is missing or ambiguous.

Example:

- a rule requiring `Mercedes-Benz / GLE / 2021 / GLE 450 4MATIC` cannot silently apply to a generic `GLE` if the required trim/engine identity is unavailable;
- a manufacturer-wide rule may explicitly declare broader applicability.

Applicability breadth is part of the knowledge record, not inferred at runtime from convenient matches.

## 6. Verification and provenance contract

Production schedule knowledge must have explicit provenance.

Minimum verification states:

- `unverified`
- `source_verified`
- `advisor_verified`
- `superseded`
- `rejected`

Only states explicitly approved by the implementation policy may drive due/overdue client-visible monitoring. M2 must define the exact production-active subset.

Every active rule must retain:

- original source;
- source reference/version where available;
- ingestion time;
- verification actor/authority when human-reviewed;
- verification time;
- correction/supersession history.

Provider output is evidence input, not professional truth.

## 7. Interval semantics

A maintenance knowledge rule may use:

1. mileage interval only;
2. time interval only;
3. both mileage and time interval.

For combined rules, the item becomes due when the first applicable threshold is reached unless the verified source explicitly defines another rule.

The engine must not invent a missing dimension.

### 7.1 Mileage calculation

For recurring mileage rules:

```text
next_due_mileage = latest_matching_service_mileage + interval_km
```

If no trustworthy matching service baseline exists, the engine may only calculate from an explicitly verified first-service/origin rule. Otherwise state is `unknown`.

### 7.2 Time calculation

For recurring time rules:

```text
next_due_date = latest_matching_service_date + interval_months
```

If the source requires an original vehicle in-service date and Aura does not have a verified one, state is `unknown`.

## 8. Service-history matching

Schedule evaluation must use a stable maintenance item key, not free-form string equality.

Matching precedence:

1. explicit advisor-verified `maintenance_item_key` on a service record;
2. deterministic mapping from an approved controlled service vocabulary;
3. otherwise unmatched.

Free-text descriptions must never be used as an AI/keyword guess to assert completion.

One service event may satisfy more than one maintenance item only when that relationship is explicitly recorded/verified.

## 9. State semantics

M3 will lock exact threshold constants, but the semantic contract is fixed now.

### `unknown`

Return `unknown` when any required fact is insufficient, including:

- no applicable verified schedule knowledge;
- required vehicle identity is missing;
- current odometer is unavailable for a mileage-only rule;
- no trustworthy recurring-service baseline exists;
- service history cannot be mapped confidently;
- source/rule has been superseded or rejected.

Unknown state must not subtract health score or produce overdue language.

### `upcoming`

A verified due threshold exists and is still outside the configured due window.

### `due`

A verified threshold has entered the configured due window or has just been reached without crossing the overdue threshold.

### `overdue`

A verified due threshold has been exceeded according to the approved deterministic policy.

No state represents a mechanical fault.

## 10. Evidence contract

Every evaluation result must be explainable from bounded structured facts.

Minimum result envelope:

```text
maintenance_item_key
state
rule_id / rule_version
rule_source
verification_status
vehicle_identity_used
current_odometer_km + odometer provenance/freshness
latest_matching_service_event_id (nullable)
latest_matching_service_mileage/date (nullable)
next_due_mileage (nullable)
next_due_date (nullable)
evaluated_at
unknown_reasons[]
```

No raw prompts, chat text, unrestricted advisor notes, personal/financial data or unrelated concern text belongs in the maintenance evidence envelope.

## 11. Durable vs derived-state rule

Maintenance knowledge and service-history facts are durable.

The calculated `upcoming/due/overdue/unknown` state is a deterministic projection unless/until a later issue explicitly approves a durable evaluation snapshot model. The projection must never be mistaken for an advisor-authored fact.

Existing `MaintenanceSchedule` rows therefore require an explicit compatibility decision in M2; do not silently reinterpret legacy rows as verified knowledge.

## 12. Reevaluation triggers

Maintenance state must be reevaluated when facts that can change the result are accepted:

- accepted/verified odometer observation advances `Car.current_mileage`;
- current or historical service record is created/corrected with usable evidence;
- service record gains/changes a verified `maintenance_item_key`;
- maintenance knowledge is verified, superseded, rejected or corrected;
- vehicle identity enrichment changes an applicability dimension.

Time passage alone must not mutate `Car.current_mileage`.

A scheduled date-based evaluator may later be added for time-only due dates, but it must evaluate from stored dates rather than invent vehicle usage.

## 13. Care-signal integration

The existing `maintenance_monitoring` alert family remains the durable client/advisor monitoring occurrence.

M5 must change its input from prose scanning to typed maintenance results.

Initial safe policy direction:

- `overdue` on a production-active verified item may raise/maintain `maintenance_monitoring`;
- when no qualifying overdue items remain, the system may resolve the active system signal;
- `unknown` must not raise an overdue signal;
- advisor-facing evidence may list the item(s), source and due threshold;
- client-facing wording stays calm and avoids repair prescription.

Care-signal lifecycle rules from Wave 2.4 remain authoritative.

## 14. Health-score compatibility

The generic `SERVICE_INTERVAL_KM = 12_000` path is compatibility debt.

Migration direction:

1. M2/M3 introduce verified maintenance knowledge and typed state without immediately deleting the legacy health path.
2. M3 proves deterministic evaluation with explicit `unknown` behavior.
3. M5 changes maintenance monitoring to consume typed results.
4. M6 removes or feature-disables the generic 12,000 km production shortcut once equivalent verified behavior is production-proven.

Unverified maintenance knowledge must never reduce vehicle health merely because a generic interval was exceeded.

## 15. Provider boundary

`MaintenanceProvider` remains an ingestion interface only.

Provider responsibilities:

- retrieve/normalize source data;
- declare source and errors;
- perform no database writes.

Aura responsibilities:

- validate applicability;
- store provenance;
- require verification before production activation;
- evaluate deterministic maintenance state;
- retain corrections/supersession audit history.

`MockMaintenanceProvider` is test/development-only and must be impossible to confuse with verified production knowledge.

## 16. Authority model

### System
May evaluate deterministic state from already verified facts and may request approved `maintenance_monitoring` care-signal lifecycle actions.

### Advisor/admin
May verify/reject/correct maintenance knowledge, classify ambiguous service-history items, and inspect full provenance/evidence.

### Owner/driver
May supply odometer/service evidence through approved workflows but may not promote schedule knowledge to verified truth.

### Rina/provider
May explain already-authorized client-safe maintenance state or provide candidate source information, but may not verify knowledge, assert completion, or mutate maintenance workflow authority.

## 17. M2–M6 implementation map

### M2 — Verified maintenance knowledge foundation
- decide whether to extend or split current `MaintenanceSchedule`;
- add provenance/applicability/version/verification contract;
- migration + PostgreSQL rehearsal;
- advisor verification service;
- block mock/unverified knowledge from production-active evaluation.

### M3 — Deterministic state engine
- pure/service-owned evaluator;
- mileage/date/combined semantics;
- explicit `unknown` reasons;
- evidence envelope;
- vehicle isolation and stale/missing-data tests.

### M4 — Service-history normalization
- controlled maintenance item vocabulary;
- advisor classification workflow;
- matching/idempotency rules;
- reevaluation hooks for odometer/service/schedule changes.

### M5 — Care signals and surfaces
- typed state → `maintenance_monitoring` integration;
- advisor evidence view;
- owner-safe upcoming/due/overdue/unknown wording;
- no broad redesign.

### M6 — Production closeout
- remove/quarantine generic 12,000 km production semantics;
- migration upgrade/downgrade rehearsal;
- authorization + cross-vehicle tests;
- production smoke and observability;
- rollback/feature-disable plan.

## 18. M1 decision

The current foundation is reusable but not production-safe as Maintenance Intelligence by itself.

Approved direction:

```text
Verified vehicle identity
        +
Verified current odometer
        +
Verified maintenance knowledge
        +
Matched service-history facts
        ↓
Deterministic maintenance state
(upcoming / due / overdue / unknown)
        ↓
Governed maintenance_monitoring care signal
        ↓
Advisor interpretation + client-safe guidance
```

No schema or production behavior should change until M2 implements the verified maintenance-knowledge boundary defined here.
