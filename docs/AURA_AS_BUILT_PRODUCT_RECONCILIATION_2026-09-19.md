# Aura As-Built Product Reconciliation — 19 September 2026

**Product:** Aura by Ajebo Fix  
**Assistant:** A.J. Rina  
**Snapshot branch base:** `main`  
**Base commit observed during reconciliation:** `1ece91f5e12a29962dedfe6bd9a7a93315ba1b35`  
**Purpose:** reconcile the original Aura V2 PRD with the application that actually exists today.

## 1. Decision

The original PRD in `scripts/document structure/aura_PRD.md` remains useful as historical product intent, but it is no longer an accurate implementation checklist.

Its permanent identity rules still hold:

- Aura is a private automotive health-management platform.
- Aura is not a repair marketplace, DIY mechanic tool or autonomous diagnostic AI.
- Rina is a clinical automotive care assistant.
- Human advisor authority remains explicit.
- Client-facing language must remain calm, structured and non-diagnostic.

However, the old PRD's final priority list is obsolete. It still presents ERD, route map, folder structure, clinical logging, treatment plans, priority access, admin console, care-plan gating and freeze as sequential future work. Most of those areas now exist in production-shaped code and have since been extended by security, progression, evidence, maintenance intelligence and lifecycle-governance work.

This document therefore becomes the **as-built product reconciliation**. It does not erase the original PRD.

## 2. Current product spine

Aura now operates across these real implementation domains.

### Identity, security and privacy

Implemented foundations include:

- account signup/login/password reset;
- CSRF protection;
- Redis-capable rate limiting;
- one-time email verification for protected actions;
- advisor-assisted owner creation with hashed, expiring, single-use activation links;
- owner-controlled password activation and guided account completion;
- authenticated session/device registry and revocation;
- role and object-level access controls;
- secure client profile and privacy centre;
- application-layer encryption for protected profile fields;
- security headers, HSTS in production, `/version` and `/healthz`.

Current limitation: advisor and administrator remain represented by the same global `admin` role. Advisor MFA is not yet implemented.

### Vehicle identity, ownership and stewardship

Aura has:

- canonical vehicle records;
- VIN-derived `VehicleProfile`;
- owner continuity through `CarOwnership`;
- driver assignment through `CarDriver`;
- stewardship transfer/reassignment workflows;
- provenance-aware mileage observations;
- vehicle-scoped records and timeline inputs.

Current rule: Aura presents the latest known verified odometer observation. It does not fabricate continuous mileage.

### Reported concerns and clinical care

Aura has durable workflows for:

- Reported Concerns (`CarFault`, retained under its legacy table name);
- Consultations;
- Assessments;
- assessment risks and treatment options;
- finalized assessment correction/addenda;
- Treatment Plans;
- Treatment Actions;
- Treatment Outcomes;
- Advisor Notes;
- Priority Requests;
- care signals / vehicle-health alerts.

These workflows increasingly use explicit state machines and canonical events instead of loose status mutations.

### Progression and longitudinal records

`VehicleEvent` is Aura's canonical append-oriented progression envelope.

Domain models remain authoritative for current state, while canonical events describe durable change over time. The platform now distinguishes genuine lifecycle facts from read-time projections.

Examples of canonical families already established include consultation, assessment, treatment, driver observation, care signal, priority request and evidence lifecycle events.

### Vehicle health

Aura currently contains:

- `VehicleHealthSnapshot`;
- `VehicleHealthAlert`;
- client and advisor health-record projections;
- vehicle trajectory/history surfaces;
- advisor Alert Center;
- governed care-signal lifecycle;
- non-canonical advisor-review projections.

A calculated score or projection is not automatically treated as durable clinical history.

### Vehicle Intelligence

Implemented foundations include:

- VIN decoding and vehicle enrichment;
- reusable `DiagnosticCodeDefinition` knowledge plus vehicle-specific `VehicleDTC` occurrences;
- vehicle recall records;
- maintenance schedule foundation;
- advisor-verified maintenance knowledge;
- service-history normalization/classification;
- deterministic Maintenance Intelligence states;
- client and advisor maintenance surfaces.

Maintenance Intelligence now requires verified identity, verified odometer evidence, verified maintenance knowledge and matching service history before claiming `upcoming`, `due` or `overdue`. Missing evidence produces `unknown`, not a guessed universal interval.

The former generic 12,000 km shortcut is no longer authoritative.

### Evidence

The secure evidence architecture now supports the production-proven image path plus an advisor-governed historical PDF ingestion path.

It includes:

- private Cloudflare R2 storage;
- controlled image upload;
- advisor-only PDF historical-record upload;
- explicit purpose/visibility and finite retention;
- server-side validation/sanitisation for image media and bounded PDF parsing;
- encrypted `EvidenceExtraction` payloads;
- Rina/provider candidate structuring that cannot autonomously publish vehicle truth;
- advisor review/edit/reject before historical facts can be applied;
- explicit separation of recommended, authorised and completed work;
- structured completed-work metadata for services/component replacements, including new versus pre-owned/Tokunbo provenance where known;
- same-vehicle evidence linking;
- client-safe and advisor-specific timeline/context projections;
- canonical evidence events.

Deferred: scanned-only PDF OCR, audio/voice-note ingestion, unrestricted video, transcription, WhatsApp ZIP reconstruction and broader multimodal interpretation.

### Rina

Rina now sits behind Aura-owned orchestration rather than being treated as a free-form chatbot.

Current architecture includes:

- role/authority-aware context;
- vehicle-scoped context handling;
- durable `ChatMessage` and `ConversationRecord` stores;
- governed provider diagnostics/audit;
- escalation and explainability services;
- product-surface authority tests;
- redaction and safety boundaries.

Permanent boundary: provider output and Rina interpretation do not silently become advisor-verified truth.

Predictive health remains blocked from production claims until the data-readiness and governance requirements are met.

### Communication

Current adapters include:

- transactional email through Resend;
- WhatsApp Cloud API integration;
- in-app Rina/chat surfaces.

Remaining integration debt includes unified delivery-attempt/webhook auditing, consent/preference enforcement across channels and removal of any remaining split/legacy delivery paths.

### Advisor operations

The advisor console now includes materially more than the original PRD anticipated:

- unified client registry;
- client profiles;
- fleet health;
- reported concerns;
- consultations;
- assessments;
- treatment plans/actions;
- advisor notes;
- priority requests;
- care signals/Alert Center;
- mileage review;
- maintenance intelligence;
- evidence review;
- Rina provider diagnostics;
- global search.

## 3. Original PRD versus as-built system

| Original PRD area | As-built status | Reconciliation |
|---|---|---|
| Product identity | **Locked** | Still valid and reinforced by Master Architecture and V2 declaration |
| Owner / Driver / Advisor roles | **Implemented with caveat** | Owner/user, driver and admin exist; advisor/admin are not separate global roles yet |
| Rina clinical mode | **Implemented and hardened** | Expanded into orchestration, authority, audit, memory and safety layers |
| Clinical records | **Implemented and expanded** | Raw chat + conversation summaries + canonical `VehicleEvent` progression |
| Vehicle health | **Implemented and expanded** | Snapshots, care signals, health records, trajectory and maintenance state |
| Consultation system | **Implemented** | Governed lifecycle with canonical event cutover |
| Assessment system | **Implemented** | Draft/finalize authority plus addenda/correction layer |
| Treatment Plan system | **Implemented and expanded** | Plans + actions + outcomes + evidence association |
| Care-plan concept | **Partially implemented** | Care context/entitlements exist; full subscription/billing machinery is not complete |
| Priority Access | **Implemented as durable requests** | Distinguished from advisor scoring/projections |
| Admin / physician console | **Implemented and expanded** | Real operational console exists; route extraction/cleanup remains ongoing |
| AI memory | **Implemented in layers** | No unrestricted single memory table is approved |
| Access control | **Implemented and hardened** | Object-level vehicle authority, verification and session controls now matter |
| V2 freeze | **Superseded by architecture governance** | Core identity is frozen; later waves add governed capabilities without reopening product identity |
| VIN / DTC intelligence | **Implemented foundation** | Provider-backed, provenance-aware, non-diagnostic |
| Secure evidence | **Implemented and expanding** | Images are production-proven; governed advisor PDF historical ingestion extends the same authority model |
| Maintenance Intelligence | **Implemented and boundary-validated** | Was not present in original PRD |
| Canonical event progression | **Implemented** | Was not explicit in original PRD and is now central |
| Privacy centre | **Implemented** | Added after original PRD |
| Predictive health | **Readiness only** | No production predictive claim/model is authorised yet |

## 4. What is deliberately NOT complete

The following should not be described as production-complete merely because architecture exists:

- advisor/admin role separation;
- advisor MFA/recovery codes;
- full commercial subscription/billing implementation;
- TSB and warranty intelligence;
- broad licensed OEM repair-data integration;
- comprehensive live-vehicle/telematics connection;
- scanned-only PDF OCR, audio/video evidence and WhatsApp ZIP ingestion beyond the governed text-PDF slice;
- multimodal diagnosis or autonomous repair instruction;
- production predictive-health model;
- complete communication delivery/webhook audit;
- full removal of compatibility routes, legacy names and cutover shims;
- final Ajebo OS billing/accounting integration.

## 5. Product boundaries that remain frozen

1. **Observation is not diagnosis.**
2. **Provider data is not advisor truth without provenance/verification.**
3. **Read-time projections are not durable lifecycle events.**
4. **Evidence does not autonomously establish a fault or treatment.**
5. **Missing maintenance evidence produces `unknown`, not guessed timing.**
6. **Mileage must come from a credible observation; Aura does not simulate an odometer.**
7. **Essential safety awareness must not be paywalled to force an upgrade.**
8. **Rina may explain, structure, remember and escalate; she does not replace professional inspection or advisor judgement.**
9. **Domain models own current state; `VehicleEvent` owns append-oriented progression history.**
10. **New capabilities extend the architecture; they do not create parallel versions of existing systems.**

## 6. Current source-of-truth hierarchy

For implementation decisions, use this order:

1. `docs/AURA_MASTER_ARCHITECTURE.md`
2. current wave/state-event contracts and production closeouts
3. this as-built reconciliation
4. current ERD snapshot
5. current registered-route inventory
6. historical V2 PRD / ERD / route-map documents under `scripts/document structure/`

Historical documents remain useful for intent, but they must not override later production contracts.

## 7. Next product phase

The immediate engineering goal is no longer “add more architecture.”

The next phase should emphasise:

- keeping the as-built documentation synchronized;
- resolving remaining compatibility/deprecation debt only when it materially improves safety or maintainability;
- real-vehicle validation;
- pilot/commercial validation;
- measurable user value;
- production reliability;
- evidence-backed expansion of Vehicle Intelligence and Rina.

The architecture is mature enough that new work should increasingly be justified by real operational or commercial evidence rather than by feature completeness.
