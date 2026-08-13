# Aura Wave 1.3 — Rina Memory and Authority Design

**Issue:** #31  
**Depends on:** #29 architecture audit, #30 canonical event/progression contracts  
**Status:** implementation contract

## 1. Decision

Wave 1.3 extends Aura's existing authentication, vehicle-access and record models rather than creating a parallel identity or chatbot permission system.

Rina must resolve authority and an explicit vehicle scope before retrieving memory, composing provider context, choosing tone or allowing an action.

The order is mandatory:

1. authenticated user identity;
2. global account role;
3. explicit active vehicle;
4. persisted relationship to that vehicle;
5. effective Rina authority;
6. record visibility and tool permissions;
7. memory retrieval;
8. prompt/policy composition;
9. provider invocation;
10. audited response/fallback.

No provider output may expand authority established by steps 1–6.

## 2. Existing components to extend

Wave 1.3 keeps these existing structures and clarifies their ownership:

- `User` remains authenticated account identity and global role source;
- `CarOwnership` remains owner proof;
- `CarDriver` remains driver-assignment proof;
- existing consultation/assessment/treatment/advisor relationships may prove advisor scope where a persisted vehicle-linked assignment exists;
- `security.access.resolve_vehicle_authority()` remains the application object-access helper for existing routes;
- `ChatMessage` remains raw conversational continuity, but must become vehicle/session scoped before Rina uses it as durable history;
- `ConversationRecord` remains the durable reviewed/operational conversation-summary concept and must gain explicit visibility/provenance semantics rather than being replaced by a generic Rina memory table;
- `VehicleEvent` remains progression history and is read as evidence, not duplicated into memory;
- Flask session remains short-lived navigation/conversation context only;
- `UserMemory` is legacy/partial preference state and must not be expanded into a general clinical memory store;
- `ChatSession` is compatibility-only until replaced or safely scoped by a later migration;
- `rina.memory` process-global behavior counters are prohibited for user-specific behavior because they mix users and workers.

## 3. Global role versus vehicle authority

Aura must not confuse a global account role with authority over a particular vehicle.

Current global roles are `user`, `driver`, and `admin`.

Wave 1.3 Rina authorities are:

- `owner` — proven by active `CarOwnership` for the selected vehicle;
- `driver` — proven by active `CarDriver` for the selected vehicle;
- `advisor` — proven by a persisted vehicle-linked professional assignment when such an assignment exists;
- `administrator` — an active global `admin` account acting under governance/operations authority.

An administrator may retain broad existing application access for compatibility, but Rina must still preserve the distinction between administrator governance context and advisor clinical context. Administrator status is never treated as vehicle ownership.

When a user has more than one relationship, Rina resolves deterministically in this order for a vehicle-specific conversation:

1. administrator;
2. explicitly scoped advisor;
3. owner;
4. driver.

This precedence is a policy choice for Rina only and does not silently rewrite route authorization elsewhere.

## 4. Explicit active vehicle rule

Rina must never silently choose the first vehicle.

A request must contain an explicit `car_id`, or a previously bound short-lived session context may supply the same vehicle only when:

- the session is valid;
- the user is still authorized for that vehicle;
- no different vehicle identifier was supplied in the request;
- no ambiguity exists.

If no vehicle can be proven, Rina returns a `vehicle_required` abstention state rather than guessing.

If a different vehicle is requested, Aura re-runs authority resolution before any memory from the new vehicle is loaded.

Vehicle names mentioned in free text are hints only. They cannot switch authority or memory scope by themselves.

## 5. Authority matrix

### Owner

May read:

- client-visible vehicle identity and verified/provenance-labelled Vehicle Intelligence;
- client-visible concern progression;
- own vehicle-scoped chat history;
- client-visible conversation summaries;
- client-visible consultation/assessment/treatment summaries;
- client-safe preferences relevant to communication/care continuity.

May not read:

- advisor notes;
- internal assessment deliberation;
- internal conversation summaries;
- other owners' or vehicles' memory;
- secrets, raw prompts, provider traces.

May request permitted client actions but Rina may not autonomously approve assessment/treatment decisions.

### Driver

May read:

- assigned vehicle identity and operating/safety context required for duties;
- client-visible concern/progression information appropriate to the driver channel;
- own vehicle-scoped chat history;
- driver-visible operating instructions that Aura has explicitly approved.

May not read:

- owner financial information;
- owner private profile/preferences unrelated to operation;
- advisor notes/internal summaries;
- owner approval/treatment authority;
- other vehicles after assignment revocation.

### Advisor

May read vehicle-scoped clinical/operational records required by the persisted advisor assignment, including advisor-visible memory. Advisor authority does not imply administrator governance authority.

### Administrator

May access governance/operations context allowed by existing application policy. Rina must label this authority as `administrator`, not `owner`, and must not convert administrator access into an assertion of clinical truth.

## 6. Memory taxonomy

### Layer A — raw conversation turns

Canonical structure: `ChatMessage`.

Retention purpose: short conversation continuity and auditability.

Required Wave 1.3 scope fields:

- `car_id`;
- `conversation_id` (opaque correlation identifier);
- channel/source;
- visibility;
- timestamp.

Raw messages are retrieved by both user and vehicle. User-only retrieval is prohibited after migration.

### Layer B — durable operational/clinical summaries

Canonical structure: `ConversationRecord`.

Purpose: reviewed continuity, not a transcript dump.

Required semantics:

- vehicle scope;
- source user;
- summary visibility (`client`, `advisor`, `internal`);
- provenance (`rules`, `provider`, `advisor`);
- verification state;
- optional correlation to conversation/request;
- no chain-of-thought.

The existing `emotional_state` field is legacy and must not be used as professional fact. Future retention should prefer observable communication state over inferred psychology.

### Layer C — progression memory

Canonical structure: `VehicleEvent` and progression services from Wave 1.2.

Rina reads evidence-backed progression; it does not copy progression into a separate memory table.

### Layer D — client-safe preferences

Canonical sources: client profile/preferences and narrowly-scoped legacy `UserMemory` until migrated.

Only preferences necessary for service continuity may enter provider context.

### Layer E — advisor-only operational memory

Canonical sources: `AdvisorNote`, internal consultation/assessment/treatment fields and advisor/internal `ConversationRecord` rows.

Never included for owner/driver requests.

### Layer F — short-lived session context

Canonical source: signed Flask session/session registry.

May contain identifiers and navigation state such as active vehicle and conversation correlation ID. It must not become the sole source of durable facts or private summaries.

## 7. Retention and minimisation policy

Wave 1.3 follows minimisation rather than indefinite provider-style memory.

- raw chat: retained under Aura account/data policy; retrieval window is bounded and vehicle scoped;
- durable summaries: retained as operational records according to vehicle/client record policy;
- advisor notes: internal retention policy, never provider memory by default;
- session context: expires with session lifecycle and revocation;
- provider request payloads: not persisted wholesale;
- prompts, API keys, chain-of-thought and provider internal traces: never persisted as memory.

Deletion/revocation must immediately remove retrieval authority even where durable records remain for legitimate operational retention.

## 8. Structured request contract

Every Rina orchestration request must resolve to a structure equivalent to:

```text
RinaRequest
- request_id
- user_id
- car_id
- authority
- channel
- message
- conversation_id
- context_version
- memory_policy
- allowed_actions
- denied_actions
```

`authority`, `car_id`, permissions and memory policy are produced by Aura, never by the model.

## 9. Structured response contract

Rina orchestration returns a structure equivalent to:

```text
RinaResponse
- request_id
- car_id
- authority
- state
- message
- uncertainty
- escalation
- actions
- evidence_refs
- provider_status
```

Allowed `state` values for the first implementation:

- `answered`;
- `abstained`;
- `vehicle_required`;
- `authority_denied`;
- `escalation_required`;
- `provider_unavailable`.

Provider errors must never change the authority, vehicle or memory scope.

## 10. Tool/action policy

Aura owns a deny-by-default action registry. Initial action families include:

- `read_client_vehicle_context`;
- `read_client_progression`;
- `read_chat_history`;
- `read_client_summary`;
- `read_advisor_memory`;
- `read_owner_financial_context`;
- `request_consultation`;
- `approve_assessment`;
- `approve_treatment`;
- `admin_governance`.

`approve_assessment` and `approve_treatment` remain denied to Rina for every authority. Human workflows own these decisions.

Driver authority always denies owner financial context and approval actions.

## 11. Prompt-injection boundary

User text, retrieved records and provider-sourced vehicle data are untrusted content.

They may not:

- change `user_id`, `car_id` or authority;
- request hidden memory outside the resolved visibility set;
- add tools/actions not already permitted;
- override system safety rules;
- reveal system prompts, credentials or internal notes.

Free-text instructions such as "ignore previous rules" are processed only as user content.

## 12. Vehicle Intelligence provenance

Vehicle Intelligence entering Rina context must carry source and verification state where available.

Rina language must distinguish:

- user reported;
- provider sourced/unverified;
- Aura rules-derived;
- advisor verified.

Unknown or disputed intelligence must remain explicitly uncertain. Provider data is never silently promoted to advisor truth.

## 13. Provider boundary

OpenAI remains a replaceable provider behind Aura orchestration.

Routes/templates may call only Aura orchestration services. Provider adapters receive a minimized structured context after authority and memory filtering.

Required failure behavior:

- bounded timeout;
- conservative retry only for transient failures;
- no retry loop on authentication/policy errors;
- safe `provider_unavailable` fallback;
- privacy-safe operational logging;
- no raw prompt or full record logging.

## 14. Audit contract

Material AI actions may write privacy-safe audit metadata such as:

- request/correlation ID;
- user ID;
- vehicle ID;
- resolved authority;
- action family;
- outcome (`allowed`, `denied`, `abstained`, `provider_failed`, `answered`);
- provider name/model family when applicable;
- evidence record IDs;
- timestamp.

Audit records must not store chain-of-thought, API keys, full prompts or unnecessary raw messages.

## 15. Migration sequence

Wave 1.3 should merge in independent gates:

1. this authority/memory design and policy contracts;
2. pure authority + explicit vehicle-context resolver and tests;
3. additive chat/conversation scoping migration on PostgreSQL;
4. vehicle-scoped memory retrieval + visibility filtering;
5. structured orchestration and provider adapter/fallback;
6. chat-route cutover eliminating silent vehicle selection and process-global behavior memory;
7. audit/observability and closure hardening.

Each gate must keep existing UI/routes compatible until the cutover PR explicitly changes behavior.

## 16. Rollback

- policy/service-only changes roll back by code deployment;
- additive schema fields remain compatible with old code until cutover;
- provider orchestration is feature-gated;
- production rollback prefers feature disable/code rollback/forward-fix over destructive migration downgrade;
- revoked access is never restored merely as a rollback shortcut.

## 17. Definition of done

Wave 1.3 is complete when automated tests prove that Rina:

- resolves authority before context/personality;
- never silently changes vehicle scope;
- retrieves chat/memory by authorized vehicle;
- separates client and advisor/internal records;
- denies driver financial/approval authority;
- survives prompt injection without permission expansion;
- represents uncertain intelligence honestly;
- falls back safely when the provider fails;
- loses access after session/relationship revocation;
- audits material actions without storing secrets or chain-of-thought.
