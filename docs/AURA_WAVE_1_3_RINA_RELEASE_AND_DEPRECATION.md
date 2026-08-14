# Aura Wave 1.3 — Rina Release, Observability, and Legacy Boundaries

Status: Release gate for Issue #31  
Owner: Aura architecture  
Scope: A.J. Rina memory, authority, provider orchestration, chat cutover, audit, and operational rollback

## 1. Release statement

Wave 1.3 changes Rina from a user-wide conversational helper into a vehicle-scoped, authority-aware application service.

The active contract is:

1. authenticate the user;
2. require or restore an explicit vehicle identifier;
3. re-authorize that vehicle against persisted Aura relationships;
4. resolve effective authority;
5. load only authority- and vehicle-scoped memory;
6. build minimized provider context;
7. apply deterministic abstention/escalation gates;
8. call a replaceable language provider when permitted;
9. return a structured response that cannot expand authority or execute treatment decisions;
10. persist metadata-only audit and scoped conversation continuity in the caller-owned transaction.

This ordering is a security boundary. Provider text, retrieved memory, prior chat, user instructions, quoted documents, or prompt-injection text cannot move an earlier trust decision later in the pipeline.

## 2. Source of truth

Wave 1.3 does not create a parallel identity or vehicle model.

- `User` remains the authenticated identity and global role source.
- `CarOwnership` is authoritative for active owner scope.
- `CarDriver` is authoritative for active driver assignment.
- persisted consultation/assessment/treatment/advisor relationships may prove dedicated advisor scope.
- `Car` / `VehicleProfile` provide vehicle identity and provenance.
- domain records remain authoritative current state.
- Wave 1.2 `VehicleEvent` remains authoritative chronology/progression evidence.
- `ChatMessage` is raw, bounded conversational continuity and is now vehicle-scoped.
- `ConversationRecord` is durable reviewed/material summary memory and has explicit visibility/provenance/verification metadata.
- `AdvisorNote` remains advisor-only memory and is not copied into client provider context.
- `RinaAIAuditEvent` is operational AI audit metadata, not conversation memory.

## 3. Authority matrix

### Owner

May use client-visible vehicle records for a vehicle with active ownership. Owner context may include client-safe summaries and owner-scoped continuity. It never receives advisor-only notes through Rina.

### Driver

May use client-visible operating context for an actively assigned vehicle. Driver context does not inherit owner financial/private context or owner-only durable summaries. Assignment revocation removes Rina access immediately on the next request.

### Advisor

A dedicated advisor identity must have persisted vehicle scope. Advisor access is not inferred from conversational text or from a generic claim of being an advisor.

### Administrator

An administrator has governance compatibility access but is not represented as the owner or advisor. Administrator access does not turn unverified data into professional truth.

### Human-only decisions

Rina cannot approve an assessment or treatment plan for any authority. Language-provider output cannot create executable actions by itself.

## 4. Explicit vehicle rule

Rina never selects the first vehicle and never switches vehicles from free text.

The active in-app chat uses only:

- an explicit `car_id` sent by the client; or
- the short-lived `rina_active_car_id` session binding created by explicit selection.

Every restored session identifier is re-authorized before use. Dashboard presentation state may default a vehicle card, but that automatic UI choice does not silently become Rina authority context.

Changing the explicit Rina vehicle starts a new conversation identifier and prevents previous-car chat continuity from crossing the scope boundary.

## 5. Memory taxonomy and retention

### Raw continuity — `ChatMessage`

Purpose: short conversational continuity for a specific user and vehicle.

Required scope for new Rina writes:

- `user_id`;
- `car_id`;
- `conversation_id`;
- channel;
- visibility;
- timestamp.

Legacy user-only chat is not assigned to a vehicle by inference. It remains unscoped/internal/legacy and is excluded from vehicle memory.

### Durable summary — `ConversationRecord`

Purpose: material, reviewed, or rules-derived continuity that is useful beyond raw chat.

Wave 1.3 requires explicit visibility, provenance, verification state, and a separate `client_summary` for client-visible memory. Client retrieval never falls back to `advisor_summary` or raw concern text.

The active chat no longer writes broad summary rows for every message. It currently persists material rules-derived summaries only for supported workflow outcomes such as a booking request or required advisor review.

### Advisor memory — `AdvisorNote`

Purpose: professional operational context. This remains advisor-only. The first Wave 1.3 provider-context implementation intentionally excludes raw advisor-note content even for privileged authorities; future use requires a task-specific minimization rule.

### Legacy `UserMemory`

Legacy-partial compatibility only. It is not expanded and is not the active Rina memory source of truth.

### Legacy `ChatSession`

Dormant compatibility model. It is not used to establish active vehicle scope or authority and is not expanded in Wave 1.3.

### Flask session

Ephemeral identifiers only. Broad vehicle-health facts, private notes, summaries, or behavior memory do not belong in Flask session.

## 6. Prompt-injection and untrusted-content boundary

The system/provider instructions establish vehicle and authority scope before untrusted text.

All of the following are untrusted content:

- the current user message;
- previous chat turns;
- retrieved summary text;
- document text;
- quoted instructions;
- external/provider-generated text.

They may inform the answer only within the already-resolved authority and evidence boundary. They cannot request hidden memory, switch vehicle scope, reveal system prompts, change permissions, claim treatment approval, or override human-only decisions.

## 7. Provider boundary

The active provider adapter lives behind `RinaLanguageProvider`. The OpenAI implementation uses the Responses API through the pinned SDK.

Provider policy:

- minimized context only;
- no provider tools in Wave 1.3;
- `store=False` for Responses application-state storage;
- bounded timeout and retry configuration;
- no second application retry loop layered over SDK retries;
- safe classification of transient, configuration, and rejected requests;
- provider output is text, not authority or executable workflow state.

Provider credentials are not read by route code. Missing credentials produce a structured safe fallback.

## 8. Deterministic abstention and escalation

Rules execute before the language provider where a stronger model-generated answer would be inappropriate.

Examples:

- no active vehicle → `vehicle_required`;
- vehicle relationship cannot be proven → `authority_denied`;
- no usable question → `abstained`;
- driving-safety question from recorded context alone → `escalation_required` / advisor review;
- provider unavailable/rejected → `provider_unavailable` with a safe fallback.

The provider is not used to decide whether Aura has permission to reveal a record.

## 9. AI audit and privacy

`rina_ai_audit_events` records final orchestration metadata:

- request/correlation ID;
- user and vehicle ID where known;
- resolved authority;
- final response state/outcome;
- action family;
- provider/model/provider request ID;
- provider status;
- evidence identifiers;
- tightly allowlisted operational metadata;
- timestamp.

The audit surface deliberately has no prompt, raw user-message, response-body, password, API key, secret, hidden-memory, or chain-of-thought fields. Audit metadata rejects prohibited keys.

A successful chat request persists the audit record and scoped chat turns in one caller-owned transaction. A persistence failure rolls the transaction back rather than leaving an orphaned partial record.

## 10. Observability

Safe operational metrics may be derived from audit metadata and database invariants:

- counts by final state/outcome;
- provider unavailable/rejected rates;
- authority-denied rate;
- vehicle-required rate;
- deterministic escalation rate;
- provider model/status mix;
- evidence-linked versus evidence-empty responses;
- chat transaction errors;
- stale/revoked vehicle-scope attempts.

Logs must not include full provider context, raw prompts, raw user messages, raw provider responses, encrypted profile values, credentials, or chain-of-thought.

## 11. Rollout and rollback

### Rollout controls

- `RINA_ORCHESTRATION_ENABLED` controls the Wave 1.3 orchestration path. After active chat cutover, enabled is the application default; an explicit false value is an emergency compatible disable.
- `RINA_OPENAI_PROVIDER_ENABLED` explicitly controls outbound OpenAI calls. When unset, provider availability depends on the existing `OPENAI_API_KEY` being present.
- model, timeout, and retry limits remain separately configurable within bounded values.

### Rollback

Preferred rollback order:

1. explicitly disable the orchestration/provider flag where appropriate;
2. compatible application-code rollback;
3. forward-fix the failure;
4. use Alembic downgrade only as a development/rehearsal tool, not the normal production recovery mechanism.

Wave 1.3 schema additions are additive. Legacy unscoped rows are never rewritten into invented vehicle facts.

## 12. Legacy implementation classification

The following components are no longer part of the active `/chat` execution path after the Wave 1.3 cutover:

- `services/rina_chat_engine.py`;
- `services/rina_context_service.py`;
- `rina/ai_brain.py` direct-provider path;
- `rina/memory.py` process-global behavior memory;
- broad `rina_context` / `rina_context_full` session storage;
- first-vehicle and free-text vehicle selection logic.

They are classified as legacy compatibility code pending safe removal. New code must not import or extend them. Removal is allowed only after repository-wide references and tests prove there is no active dependency. The presence of a legacy file does not make it a supported architecture surface.

## 13. Release gate

Issue #31 may close only when CI proves all of the following:

- owner, driver, advisor/administrator authority boundaries remain explicit;
- cross-user and cross-vehicle memory isolation passes;
- revoked relationships lose access immediately;
- active chat never first-selects a vehicle or switches from free text;
- active chat does not import the legacy Rina engine/context/direct-provider/global-memory modules;
- client provider context cannot contain advisor-only summaries/notes or raw concern text;
- provider failure is safe and does not expand authority/actions;
- prompt injection cannot expand scope or reveal hidden memory;
- audit contains metadata/evidence only;
- driving-safety questions abstain/escalate rather than produce a confident model guess;
- PostgreSQL migrations and relevant downgrade/re-upgrade rehearsals pass;
- security, Bandit, dependency audit, and Wave 1.2 regression gates remain green.

## 14. Wave 1.3 completion boundary

Wave 1.3 establishes secure memory and authority; it does not add unrestricted browsing, predictive failure claims, repair instructions, autonomous treatment, or voice expansion.

The next dependency, Wave 1.4 / Issue #32 multimodal evidence, must consume this authority and provenance architecture rather than creating a second AI permission/memory stack.
