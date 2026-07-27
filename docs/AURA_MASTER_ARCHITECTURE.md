# Aura Master Architecture

**Product:** Aura by Ajebo Fix  
**Assistant:** A.J. Rina  
**Status:** Living architecture and delivery constitution  
**Current platform:** Flask/Jinja application deployed on Railway  

## 1. Purpose

This document is the permanent architectural map for Aura.

It exists to stop duplicated systems, route sprawl, renamed features, speculative AI claims and provider-specific logic from becoming the product architecture.

Every substantial feature must have:

1. a defined product purpose;
2. a place in one of Aura's architecture domains;
3. a data model and ownership boundary;
4. a route or service boundary;
5. an authority and privacy policy;
6. automated tests and migration safety where applicable.

No feature should be implemented merely because it sounds intelligent or impressive.

## 2. Permanent product identity

Aura is a private automotive health-management platform.

Aura is not:

- a mechanic marketplace;
- a DIY repair application;
- an autonomous diagnosis engine;
- a workshop CRM with AI branding;
- a generic chatbot.

A.J. Rina is a clinical automotive care assistant. Rina observes, explains, remembers, structures, escalates and supports continuity. Human advisors remain the final authority for assessments, treatment decisions and professional recommendations.

## 3. Cross-cutting rules

### 3.1 Authority before personality

Rina must establish who is speaking before selecting tone, visibility, tools or actions.

Supported authorities include:

- owner;
- driver;
- advisor;
- administrator.

Future roles may include fleet manager, supplier and enterprise operator, but no new role should bypass the same permission model.

### 3.2 Progression before prediction

Aura must first understand what changed across time before claiming that it can predict what will happen next.

Prediction must be based on structured evidence, declared confidence, provenance and an honest insufficiency state.

### 3.3 Records before automation

Important conversations, observations, assessments, interventions and outcomes must become durable, vehicle-scoped records before automated follow-up depends on them.

### 3.4 Human authority remains visible

Provider data and AI output must not silently become professional truth.

Aura must distinguish:

- user-reported information;
- provider-sourced intelligence;
- Aura-generated interpretation;
- advisor-verified conclusions.

### 3.5 Privacy by architecture

Personal, vehicle and communication data must be minimised, scoped, encrypted where required, audited without logging sensitive values and disclosed only to authorised roles.

## 4. Architecture domains

## Domain A — Vehicle Intelligence

Purpose: know the vehicle and preserve trustworthy automotive knowledge.

Includes:

- VIN decoding and vehicle identity enrichment;
- diagnostic-code definitions and vehicle DTC occurrences;
- maintenance schedules;
- recall intelligence;
- future TSB, warranty, service-history and live-vehicle connectors;
- source provenance and advisor verification;
- vehicle health timeline inputs.

Boundary: Vehicle Intelligence informs care. It does not independently diagnose or prescribe repairs.

## Domain B — Rina Intelligence

Purpose: provide context-aware, role-aware and continuity-aware automotive assistance.

Includes:

- authority resolution;
- vehicle-scoped context assembly;
- conversation memory;
- clinical summaries;
- escalation behaviour;
- emotional calibration;
- channel-consistent responses;
- future voice and multimodal reasoning.

Boundary: Rina may explain, structure and escalate. Rina may not replace professional inspection or advisor judgement.

## Domain C — Communication and Channel Infrastructure

Purpose: allow Aura to communicate consistently across the application, email, WhatsApp and future channels.

Includes:

- in-app messaging;
- WhatsApp templates and conversation workflows;
- Resend transactional email;
- reminders and follow-ups;
- delivery records and failure handling;
- channel preference and fallback rules.

Boundary: channels transport Aura decisions; they do not define business logic independently.

## Domain D — Driver Intelligence

Purpose: make drivers accountable operational participants without granting owner or advisor authority.

Includes:

- driver assignments and invitations;
- check-ins and concern reports;
- operating observations;
- driver/owner communication boundaries;
- future usage-pattern and stewardship signals.

Boundary: drivers report and operate. They do not approve treatment, access private financial information or override owners/advisors.

## Domain E — Advisor Operations

Purpose: give Ajebo Fix a calm operational command centre.

Includes:

- client registry and profiles;
- consultations;
- assessments;
- treatment plans;
- advisor notes;
- evidence uploads;
- risk, priority and monitoring queues;
- audit trails and restricted internal information.

Boundary: client-facing clarity must remain separate from internal professional notes and deliberation.

## Domain F — Ajebo OS

Purpose: support Ajebo Fix's broader business operations without turning the Aura core into uncontrolled ERP sprawl.

Future scope may include:

- estimates, invoices and receipts;
- payments and accounting;
- suppliers and inventory;
- job cards and time tracking;
- employees and restricted admin roles;
- operational reporting.

Boundary: Ajebo OS modules must integrate through explicit domain services and must not weaken Aura's automotive-health identity.

## Domain G — Commercial and Enterprise Platform

Purpose: convert operational value into a sustainable business.

Includes:

- care plans and subscriptions;
- priority access;
- commercial-readiness metrics;
- fleet and enterprise accounts;
- pricing and entitlement rules;
- partnerships and integrations;
- investor-readiness evidence;
- scalability and reliability controls.

Boundary: essential safety awareness must never be withheld merely to force payment.

## 5. Infrastructure ownership

### GitHub

GitHub is Aura's engineering source of truth for:

- architecture documents;
- issues and delivery epics;
- pull requests;
- migration history;
- automated tests and security gates;
- release history.

### Railway

Railway remains the current runtime host for the Flask Aura application and its production configuration.

### Cloudflare

Cloudflare currently serves the Ajebo Fix official website deployment and may later support Aura through WAF, Turnstile, R2 private storage, edge protection or controlled API infrastructure. Moving Aura's core runtime to Cloudflare is not assumed by this document.

### Resend

Resend is the transactional-email provider. It delivers communications selected by Aura workflows; it does not own care logic, escalation logic or clinical records.

### OpenAI

OpenAI is an intelligence provider behind Aura's own orchestration, authority, safety, context, audit and persistence layers. Routes and templates must not call the provider directly.

## 6. Wave 1 — Intelligence foundation

Wave 1 must be completed in this order.

## 6.1 Current-system architecture audit

Objective: establish what already exists before new models, services or routes are proposed.

Audit at minimum:

- authentication and security;
- users, owners, drivers, advisors and administrators;
- client profiles and privacy controls;
- vehicles, ownership and driver assignments;
- conversations and clinical records;
- consultations, assessments and treatment plans;
- vehicle events, alerts and health state;
- VIN, DTC and other Vehicle Intelligence components;
- WhatsApp and email delivery;
- existing admin/advisor surfaces;
- migration heads, test coverage and production configuration.

Deliverables:

- current system map;
- canonical model and route catalogue;
- duplicate/deprecated component list;
- confirmed gaps;
- migration and compatibility risks;
- proposed ownership boundaries for future work.

No new intelligence model should be approved until this audit is reviewed.

## 6.2 Event and progression intelligence

Objective: allow Aura to understand how a vehicle, concern and care relationship change over time.

The common progression layer must answer:

- what changed;
- when it changed;
- what the previous state was;
- whether the change represents improvement, deterioration or no material change;
- whether the concern has recurred;
- what intervention or advisor action followed;
- what evidence supports the conclusion.

Required design concepts:

- canonical event taxonomy;
- subject type and subject identifier;
- vehicle scope;
- actor and actor authority;
- event source and provenance;
- previous state and new state;
- relationship/correlation identifiers;
- intervention and outcome linkage;
- visibility classification;
- immutable timestamps;
- idempotency and duplicate protection.

The first implementation must be rules-based and auditable. No predictive claims are required here.

## 6.3 Rina memory and authority engine

Objective: make Rina reliably aware of who is speaking, which vehicle is in scope and what information/action that role is allowed to access.

Required capabilities:

- authority resolver;
- active vehicle/context resolver;
- vehicle-scoped memory retrieval;
- role-specific language policy;
- role-specific tool and record permissions;
- separation of client-visible and advisor-only memory;
- durable conversation summary linkage;
- explicit uncertainty and escalation states;
- prompt-injection and cross-vehicle leakage tests.

Personality switching and emotional calibration must be layered on top of this engine, not used as a substitute for it.

## 6.4 Multimodal intake

Objective: accept controlled images, documents and voice notes as evidence linked to a vehicle and an authorised care workflow.

Required controls:

- authenticated and vehicle-scoped upload permission;
- allowed file types and strict size limits;
- malware/polyglot protection;
- server-side image decode/re-encode and metadata stripping where relevant;
- private object storage and signed/protected access;
- consent, retention and deletion policy;
- advisor-review state;
- source/provenance record;
- no automatic diagnosis from media;
- privacy-safe logs.

Multimodal output must be treated as an observation or extracted evidence until reviewed under the appropriate authority policy.

## 6.5 Predictive health

Objective: identify evidence-based deterioration, recurrence or maintenance-risk patterns without pretending certainty.

This phase is blocked until the progression layer contains sufficient structured and reviewed data.

Minimum requirements before implementation:

- stable event taxonomy;
- quality and completeness metrics;
- labelled historical outcomes;
- reproducible evaluation dataset;
- baseline rules for comparison;
- confidence and abstention thresholds;
- explanation/provenance output;
- advisor review and correction loop;
- drift monitoring;
- clear non-diagnostic client language.

The system must be able to say, "There is not enough verified information to assess a pattern."

## 7. Wave 1 dependency graph

```text
Current-system architecture audit
              ↓
Event and progression intelligence
              ↓
Rina memory and authority engine
              ↓
Multimodal intake
              ↓
Predictive health
```

Some implementation work may overlap after interfaces are locked, but no later phase may redefine earlier domain ownership silently.

## 8. GitHub epic structure

Wave 1 is tracked through one parent epic and five delivery epics:

1. Master Architecture and Wave 1 governance;
2. Current-system architecture audit;
3. Event and progression intelligence;
4. Rina memory and authority engine;
5. Secure multimodal intake;
6. Predictive health readiness and implementation.

Each delivery epic must contain:

- objective;
- current state;
- explicit non-goals;
- architecture and data changes;
- routes/services affected;
- permissions and privacy requirements;
- migrations;
- automated tests;
- deployment/rollback plan;
- definition of done;
- dependencies and blockers.

## 9. Definition of done for Wave 1

Wave 1 is complete only when:

- the current architecture is documented and duplicate concepts are resolved;
- progression can be reconstructed from durable events;
- Rina cannot leak information or authority across roles or vehicles;
- multimodal evidence is private, controlled and reviewable;
- prediction is either validated against evidence or explicitly deferred;
- migrations pass upgrade/downgrade rehearsals on PostgreSQL;
- CI covers security, authorisation, data isolation and major state transitions;
- production deployment includes observability and rollback instructions;
- user-facing language remains calm, non-diagnostic and honest.

## 10. Change-control rule

A change to any protected product boundary, authority rule, event taxonomy or AI safety rule requires:

1. a documented architectural decision;
2. review of data and route impact;
3. migration and compatibility analysis;
4. updated automated tests;
5. an approved pull request.

Aura should become more capable without becoming less trustworthy.
