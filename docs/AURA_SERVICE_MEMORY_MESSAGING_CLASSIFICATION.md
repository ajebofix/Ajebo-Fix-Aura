# Aura Service, Memory and Messaging Classification

**Issue:** #29  
**Parent epic:** #28  
**Pull request:** #34  
**Status:** Final audit decision record  
**Scope:** Documentation only; no production behaviour changes

## 1. Executive conclusion

Aura already has enough service, memory and communication components to support Wave 1. The immediate risk is not missing capability; it is overlapping ownership, direct provider calls, dormant structures and route-owned side effects.

The following decisions are now locked for Issue #29:

- keep one canonical vehicle-access resolver based on `security.access`;
- extend `VehicleEvent` rather than create another event table;
- retain `ChatMessage` for raw conversation turns, but vehicle-scope it before broader Rina expansion;
- retain `ConversationRecord` as the reviewed durable operational/clinical summary layer;
- classify `ChatSession` as dormant and `UserMemory` as legacy-partial pending a dedicated cleanup/migration PR;
- prohibit process-global Rina behaviour memory from production personalisation;
- consolidate all transactional email onto `services.email_delivery` and Resend HTTPS;
- retain `services.whatsapp` as the canonical outbound WhatsApp adapter, but add delivery audit, correlation and webhook status handling before expanding it into a full conversational channel;
- keep domain models authoritative for current state and use canonical events for append-oriented progression history.

## 2. Service classification

| Service / module | Current responsibility | Side effects | Classification | Decision |
|---|---|---|---|---|
| `security.access` | owner/driver/advisor vehicle access | aborts/returns authority context | **Keep — canonical** | All future vehicle-scoped tools and Rina authority must consume this policy layer. |
| `services.owner_driver_management` | owner-driver assignment management | database writes, invitation/delivery coordination | **Keep — canonical candidate** | Keep as service-owned workflow; remove remaining route duplication in later PRs. |
| `services.vehicle_intelligence` | current health calculation | reads vehicle/care records | **Keep — canonical calculation service** | Must remain non-diagnostic and provenance-aware. |
| `services.intelligence_hooks` | assessment compatibility hook | read-only assessment result | **Compatibility-only** | Keep alias until callers migrate; do not treat it as an event or action engine. |
| `services.vehicle_health_snapshot` | persist calculated health snapshots and re-evaluate signals | database write, alert evaluation | **Keep, fix before expansion** | Canonical snapshot writer candidate; correct call contracts and transaction boundary in a dedicated PR. |
| `services.vehicle_health_snapshot_service` | overlapping snapshot responsibility | likely duplicate/legacy side effects | **Merge/deprecate** | Compare callers, merge unique behaviour into one snapshot service, then remove later. |
| `services.health_alert_service` | create/resolve care signals | database writes and commit | **Keep, redesign lifecycle contract** | Keep care-signal ownership; align trajectory keys and recurrence constraints. |
| `services.health_trend_service` | derive trajectory from snapshots | read-only analysis | **Keep — canonical candidate** | Standardise one public method and result schema; remove duplicate route expectations. |
| `services.conversation_logger` | durable conversation summary logging | database writes | **Keep — canonical candidate** | Route all reviewed `ConversationRecord` creation through one service with vehicle scope and visibility metadata. |
| `services.rina_context_service` | build vehicle/user context for Rina | database reads | **Keep, harden** | Must use canonical vehicle authority, explicit vehicle selection and privacy-safe context. |
| `rina.ai_brain` / provider integration | OpenAI response generation | external API call, logging | **Keep behind orchestration boundary** | Centralise policy, timeout, quotas, structured output and safe fallback. |
| `rina.brain` | legacy context/memory/chat helper | database reads/writes, console printing | **Deprecate after migration** | Contains invalid/legacy vehicle queries and duplicates active chat/context logic. |
| `services.rina_action_suggestions` | suggest possible next actions | read-only interpretation | **Keep, advisory-only** | Suggestions must never execute material actions without authority and confirmation. |
| `services.rina_explainability_engine` | explain why guidance was produced | read-only | **Keep — supporting service** | Link explanations to provenance and recorded evidence. |
| `services.email_delivery` | Resend HTTPS transactional email | external API call | **Keep — canonical** | All account and transactional email must use this adapter. |
| `auth.routes.send_password_reset_email` | Gmail SMTP password reset | direct SMTP call | **Replace/deprecate** | Move password reset onto `services.email_delivery`; remove SMTP credentials after production verification. |
| `services.whatsapp` | Meta WhatsApp outbound templates/text | external API call | **Keep — canonical outbound adapter** | Add durable delivery attempts/status, correlation IDs, webhook receipts and retry policy before expansion. |
| `services.reminder_engine` | reminder coordination | likely message delivery and scheduling decisions | **Keep, audit before automation expansion** | Must call channel adapters rather than provider APIs directly. |
| `services.report_builder` | vehicle/client report generation | read-only/render output | **Keep** | Preserve report boundary; evidence links should reference canonical records. |
| `services.assessment_report_builder` | assessment report generation | read-only/render output | **Keep** | Canonical for assessment output once duplicate assessment routes are consolidated. |
| `services.priority_scoring` | prioritisation score | derived calculation | **Review/rename** | Ensure it is operational triage, not client/driver gamification or unsupported urgency. |
| `services.reminder_engine` | maintenance/follow-up reminders | message/channel side effects | **Keep with governance** | Require idempotency, consent, preferred channel and event correlation. |

## 3. Memory ownership decision

| Store | Actual observed use | Final classification | Ownership rule |
|---|---|---|---|
| `ChatMessage` | active raw user/assistant turns | **Keep and evolve** | Raw conversational record; add vehicle/session scope, retention rules and privacy-safe access. |
| `ChatSession` | model exists; no active route/service consumer found | **Dormant** | Do not expand in place until a session contract is designed. Keep table for compatibility; deprecate or repurpose only via dedicated migration. |
| `UserMemory` | read by legacy `rina.brain`; no confirmed active canonical write path | **Legacy-partial** | Do not use as trusted production personalisation. Migrate valid preferences into client-safe profile/preferences or a reviewed future memory layer. |
| Flask `session['rina_context_full']` and related session state | active short-lived context | **Keep for ephemeral state only** | Never use as durable memory or cross-device history. |
| `ConversationRecord` | active vehicle-scoped durable summaries | **Keep — durable reviewed memory candidate** | Store material, reviewed, vehicle-scoped summaries with authority, provenance and visibility. |
| `rina.memory._user_behavior` | process-global dictionary | **Prohibit and remove** | Must not be used for production personalisation because it can mix users and disappears across workers/restarts. |
| `AdvisorNote` | internal advisor continuity | **Keep separate** | Never expose or inject into client-facing Rina context by default. |
| `ClientProfile` care/preferences | explicit client-managed preferences | **Keep — client-safe preference source** | Use only approved fields and preserve encryption/access boundaries. |

### Memory architecture direction

```text
Raw turns                 → ChatMessage (vehicle/session scoped)
Temporary context         → Flask/session or bounded cache
Client-safe preferences   → ClientProfile / explicit approved preference store
Reviewed durable summary  → ConversationRecord
Vehicle progression       → canonical VehicleEvent
Internal advisor context  → AdvisorNote (restricted)
```

No new generic `RinaMemory` table is approved under Issue #29.

## 4. Messaging architecture audit

### 4.1 Resend email

Current path:

```text
verification trigger
    → security/email verification workflow
    → services.email_delivery.send_transactional_email
    → Resend HTTPS API
    → provider message ID returned to caller
```

Strengths:

- HTTPS avoids Railway SMTP-network restrictions;
- provider configuration is centralised;
- deterministic idempotency keys are available;
- timeouts and provider error codes are normalised;
- secrets remain outside routes.

Gaps:

- no durable email-delivery-attempt model;
- no webhook processing for delivered/bounced/complained status;
- provider message IDs are not consistently correlated to users, consultations or events;
- retry policy is caller-dependent;
- password reset bypasses the adapter.

Decision: `services.email_delivery` is the single canonical outbound email adapter. Password reset must migrate to it in a separate security PR.

### 4.2 Gmail SMTP

Current path:

```text
forgot-password route
    → auth.routes.send_password_reset_email
    → direct Gmail SMTP/TLS
```

Risks:

- Railway may block or fail outbound SMTP;
- credentials and transport policy remain in route code;
- delivery behaviour differs from verification email;
- there is no provider message ID or delivery audit;
- generic exception handling reduces observability.

Decision: replace with Resend HTTPS, preserve anti-enumeration behaviour, then remove SMTP-specific configuration after successful production verification.

### 4.3 Meta WhatsApp

Current path:

```text
booking/driver/admin workflow
    → services.whatsapp function
    → Meta Graph API
    → accepted/rejected response returned to caller
```

Strengths:

- central adapter;
- runtime configuration validation;
- recipient normalisation;
- template-specific payload building;
- explicit timeout and safe provider-error normalisation;
- avoids malformed requests when configuration is missing.

Gaps:

- accepted-by-Meta is not the same as delivered/read;
- no durable delivery-attempt/status model;
- no webhook ingestion/correlation contract;
- no shared idempotency key;
- no retry/backoff policy;
- no unified consent and preferred-channel enforcement;
- free-form admin text depends on an open service window;
- driver invitation share links remain separate from the Cloud API adapter in some paths.

Decision: keep `services.whatsapp` as the canonical outbound adapter. Before “Rina inside WhatsApp,” add message-attempt records, webhook verification/receipt processing, correlation to subject/event/user, idempotency and channel-consent policy.

## 5. CI coverage and high-risk gaps

### Covered today

- security foundation;
- rate limits;
- email verification;
- encrypted client profile model/routes;
- session registry and revocation;
- owner-driver management authorisation;
- WhatsApp delivery adapter tests;
- Rina provider-failure handling;
- SQLite migration rehearsals and schema reconciliation;
- Ruff and Bandit for selected security paths.

### High-risk gaps

1. No PostgreSQL service job for fresh migration and `d42e7a1c9b50 → proposed head` rehearsal.
2. No integration tests for canonical event emission across concern, consultation, assessment, treatment, DTC, driver, stewardship and health workflows.
3. No tests proving chat history isolation by vehicle.
4. No tests proving one authority resolver is used by route, Rina context and action tools.
5. No delivery-webhook tests for Resend or Meta WhatsApp.
6. No durable idempotency/retry tests for outbound communication.
7. No route tests for the identified stewardship and health-trajectory defects.
8. No database constraint test for one active owner per vehicle, recurring alerts or daily driver check-ins.
9. No production-like Redis/shared rate-limit test.
10. No structured redaction test preventing PII/vehicle context from appearing in logs.

## 6. Final keep / merge / deprecate matrix

| Component | Decision | Timing |
|---|---|---|
| `VehicleEvent` | **Keep and extend** | Wave 1.2 additive migrations |
| `EventAuditLog` | **Keep as compatibility event audit** | Do not generalise; later favour correction events |
| `security.access` | **Keep and make canonical** | Wave 1.3 authority work |
| `CarFault` | **Keep under legacy table name** | No rename in Wave 1 |
| `VehicleHealthSnapshot` | **Keep** | Fix writer contracts first |
| `VehicleHealthAlert` | **Keep and repair recurrence contract** | Dedicated integrity PR |
| duplicate health trajectory routes | **Merge to one route/service contract** | Dedicated cleanup PR |
| `routes/assessments.py` | **Classify as inactive/legacy test stub; deprecate** | Dedicated cleanup PR |
| `cars/modules/assessments.py` | **Keep client assessment download** | Preserve until route consolidation |
| `admin/modules/assessments.py` | **Keep advisor assessment download** | Preserve until route consolidation |
| broad `admin/routes.py` assessment logic | **Keep temporarily, extract later** | Incremental service refactor |
| `ChatMessage` | **Keep and vehicle-scope** | Wave 1.3 |
| `ChatSession` | **Dormant; compatibility-only** | Review in memory migration PR |
| `UserMemory` | **Legacy-partial; do not expand** | Migrate valid fields later |
| `ConversationRecord` | **Keep as reviewed durable summary** | Wave 1.3 |
| Flask Rina session context | **Keep ephemeral only** | Harden retention and scope |
| global behaviour dictionary | **Remove/prohibit** | Rina hardening PR |
| `rina.brain` legacy helper | **Deprecate after active callers migrate** | Rina consolidation PR |
| `services.email_delivery` | **Keep canonical** | Immediate standard |
| Gmail SMTP password reset | **Replace/deprecate** | Separate security PR |
| `services.whatsapp` | **Keep canonical outbound adapter** | Add audit/webhooks before expansion |
| direct WhatsApp/share-link logic in routes | **Merge into governed channel workflow** | Communication phase |
| SQLite-specific JSON declarations | **Do not repeat; clean cautiously** | PostgreSQL migration hardening |
| driver score/gamification | **Deprecate product use** | Separate no-gamification cleanup |

## 7. Final Issue #29 architecture decisions

1. `VehicleEvent` will be extended; no parallel progression table is approved.
2. Domain models remain authoritative for current state; events provide append-oriented history.
3. Wave 1.2 migrations must be additive, staged, PostgreSQL-rehearsed and feature-gated.
4. One event-emission service will own taxonomy, actor authority, subject, visibility, correlation, evidence and correction semantics.
5. One vehicle-authority resolver will serve routes, Rina and future tools.
6. Rina memory will be layered rather than represented by one unrestricted memory table.
7. All transactional email will converge on Resend HTTPS.
8. WhatsApp remains an adapter until delivery status, consent, webhook and audit contracts exist.
9. Predictive health remains blocked until sufficient structured longitudinal data and validation exist.
10. Production rollback will preserve additive schema/data and use code rollback or forward fixes, not destructive downgrade.

## 8. Exit decision

Issue #29 has met its documentation objectives:

- current architecture mapped;
- route and progression behaviour traced;
- migration lineage and rollback boundaries documented;
- production revision verified as `d42e7a1c9b50`;
- service ownership classified;
- memory ownership decided;
- communication paths audited;
- CI gaps catalogued;
- keep/merge/deprecate decisions recorded;
- `VehicleEvent` extension decision locked.

PR #34 is ready for review. Issue #30 may proceed to design only after PR #34 is merged.
