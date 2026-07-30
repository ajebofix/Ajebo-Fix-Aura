# Aura Current-System Architecture Audit

**Issue:** #29  
**Parent epic:** #28  
**Status:** Final — ready for review  
**Scope:** Documentation and architecture decisions only  

## 1. Purpose

This audit maps Aura as it exists before Wave 1.2 introduces canonical progression intelligence.

It exists to prevent sideways development: duplicate models, parallel route families, competing memory stores, inconsistent authority checks and speculative AI features that bypass Aura's advisor-led, non-diagnostic identity.

No production behaviour, migration, route or model change belongs in this audit pull request.

## 2. Executive position

Aura already contains substantial foundations across identity, vehicles, ownership, drivers, clinical care, Vehicle Intelligence, health, communication and Rina.

The problem is not an absence of components. The problem is that several components overlap, retain legacy names or fields, or enforce related responsibilities through different contracts.

The audit is now complete. The final decision is to **extend `VehicleEvent` additively** into Aura's canonical append-oriented progression envelope while keeping domain models authoritative for current state.

No parallel event table is approved.

### Issue #30 gate

Issue #30 may proceed to design after PR #34 is merged. It must preserve the decisions in this audit and the Aura Master Architecture.

## 3. Confirmed runtime architecture

```text
Client / Driver / Advisor browser
            ↓
Flask application factory (`create_app`)
            ↓
Blueprint routes and security middleware
            ↓
Domain services
            ↓
SQLAlchemy models
            ↓
Railway PostgreSQL
```

Production entry point remains:

```text
gunicorn app:app
```

The module-level `app = create_app()` compatibility object must remain until Railway's start command changes deliberately.

### Registered runtime controls

The application factory currently initialises:

- Flask-Login with strong session protection;
- CSRF protection;
- rate limiting;
- email-verification gates;
- session registry and revocation;
- application-layer profile encryption configuration;
- security headers and production HSTS;
- `/version` runtime identity;
- `/healthz` database/schema readiness.

## 4. Registered blueprint catalogue

| Blueprint | Prefix / route family | Current responsibility | Final classification |
|---|---|---|---|
| `auth_bp` | `/auth` | signup, login, logout, password reset | canonical; migrate password reset delivery to Resend |
| `advisor_bp` | `/admin` | advisor compatibility/auth routes | compatibility-sensitive; review overlap with `admin_bp` |
| `email_verification_bp` | security-owned | verification and resend | canonical |
| `session_registry_bp` | security-owned | active sessions and revocation | canonical |
| `cars_bp` | cars-owned | client vehicle workflows | canonical candidate |
| `dashboard_bp` | dashboard-owned | role-aware home/dashboard | canonical |
| `driver_bp` | `/driver` | driver dashboard, concerns and check-ins | canonical candidate |
| `profiles_bp` | `/profile` | secure client profile and privacy centre | canonical |
| `admin_bp` | `/admin` | advisor/admin operations | active but too broad; extract services incrementally |
| `chat_bp` | `/chat` | Rina conversation and history | active; unsafe to extend unchanged |
| `treatments_bp` | `/treatments` | treatment records/actions | active; lifecycle must move to canonical event emission |
| `audit_bp` | `/audit` | event audit views/actions | compatibility event-audit layer |
| `intelligence_bp` | `/intelligence` | VIN/DTC intelligence routes | canonical candidate |
| `health_bp` | `/health` | health surfaces/services | active; route/service overlap must be consolidated |
| `notices_bp` | `/clinical_notices` | clinical notices | active health-alert view |
| `health_trajectory_bp` | `/health_trajectory` | health trend/trajectory | active but contract is broken/inconsistent |
| `stewardship_bp` | `/stewardship` | ownership/stewardship lifecycle | canonical candidate; blocking defects identified |
| `concerns_bp` | concern routes | reported concerns | canonical client-facing concept under legacy route names |
| `assessments_bp` | assessment routes | advisor assessment workflow | active; duplicate modules classified below |

### Duplicate route-family decisions

| Component | Decision |
|---|---|
| `health/trend_routes.py` and `routes/health_trends.py` | merge to one registered route and one service result contract |
| `routes/assessments.py` | inactive/legacy test stub; deprecate in dedicated cleanup PR |
| `cars/modules/assessments.py` | keep client assessment download |
| `admin/modules/assessments.py` | keep advisor assessment download |
| broad assessment logic in `admin/routes.py` | keep temporarily; extract into service-owned workflow incrementally |

## 5. Canonical domain model catalogue

### 5.1 Identity, people and authority

| Model | Responsibility | Final classification | Findings |
|---|---|---|---|
| `User` | account identity, password hash and global role | canonical | roles are `user`, `admin`, `driver`; advisor/admin remain one role value |
| `CarOwnership` | vehicle-to-owner relationship and care-plan context | canonical | database does not enforce one active owner per vehicle |
| `CarDriver` | vehicle-scoped driver assignment | canonical | should feed one authority resolver |
| `AccessCode` | invitation/access-code lifecycle | legacy-sensitive | plaintext code and lifecycle hardening remain separate work |
| `ClientProfile` | one-to-one personal/care preferences | canonical | protected fields encrypted at application layer |
| `ProfileAuditEvent` | privacy-safe profile mutation audit | canonical | stores metadata/field names, not submitted plaintext |
| `UserSession` | authenticated device/session registry | canonical | security-owned dynamic model |

### 5.2 Vehicle identity and stewardship

| Model | Responsibility | Final classification | Findings |
|---|---|---|---|
| `Car` | canonical vehicle identity | canonical | owns relationships to events, concerns and intelligence |
| `VehicleProfile` | decoded VIN-derived enrichment | canonical | one-to-one with `Car` |
| `CarOwnership` | owner continuity and active care relationship | canonical | future partial unique index required |
| `CarDriver` | active driver assignment | canonical | future active-assignment integrity review required |

### 5.3 Vehicle Intelligence

| Model | Responsibility | Final classification | Findings |
|---|---|---|---|
| `DiagnosticCodeDefinition` | reusable verified/cached DTC knowledge | canonical | generic/manufacturer uniqueness is correctly separated |
| `VehicleDTC` | vehicle-specific DTC occurrence | canonical with legacy fields | legacy snapshots stay until backfill and dedicated migration |
| `VehicleRecall` | vehicle-scoped recall record | active foundation | definition/occurrence split not yet implemented |
| `MaintenanceSchedule` | vehicle-scoped due/completed maintenance | active foundation | no OEM template layer yet |

Vehicle Intelligence must remain provider-backed, provenance-aware and non-diagnostic. Missing definitions must abstain rather than fabricate.

### 5.4 Automotive care and advisor authority

| Model | Responsibility | Final classification | Findings |
|---|---|---|---|
| `CarFault` | client/driver Reported Concern | canonical concept under legacy table name | do not create a replacement merely to rename it |
| `Consultation` | consultation lifecycle and advisor assignment | canonical candidate | state transitions need one event emission contract |
| `VehicleAssessment` | authoritative consultation-linked assessment | canonical | one assessment per consultation; finalisation boundary exists |
| `VehicleAssessmentRisk` | advisor-authored assessment risk | canonical | internal professional knowledge |
| `VehicleAssessmentTreatmentOption` | assessment treatment options | canonical | separate from final treatment-plan progression |
| `TreatmentPlan` | managed treatment progression | canonical candidate | state machine and action/event linkage require hardening |
| `AdvisorNote` | internal client/vehicle relationship intelligence | canonical and restricted | must never enter client-safe memory by default |
| `ConversationRecord` | durable clinical-style conversation summary | keep as reviewed durable memory candidate | vehicle-scoped but authority/visibility/provenance must expand |

### 5.5 Health, events and alerts

| Model | Responsibility | Final classification | Findings |
|---|---|---|---|
| `VehicleEvent` | generic vehicle timeline event | **keep and extend** | approved base for Wave 1.2 canonical event envelope |
| `EventAuditLog` | create/edit/delete audit snapshots | compatibility-only event audit | do not generalise into a floating audit table |
| `VehicleHealthSnapshot` | point-in-time calculated health state | canonical evidence source | already indexed by car/time |
| `VehicleHealthAlert` | active health notice/escalation | keep and repair lifecycle | uniqueness contract blocks clean recurrence history |
| `DriverCheckIn` | daily operational observation | canonical evidence source | database uniqueness missing; driver score is product drift |

### 5.6 Rina and conversation structures

| Store | Final classification | Decision |
|---|---|---|
| `ChatMessage` | keep and evolve | raw turns must become vehicle/session scoped |
| `ChatSession` | dormant / compatibility-only | no active consumer found; do not expand yet |
| `UserMemory` | legacy-partial | read by legacy `rina.brain`; do not trust for new personalisation |
| Flask Rina session context | keep ephemeral only | never treat as durable cross-device memory |
| `ConversationRecord` | keep as reviewed durable summary | add authority, provenance and visibility |
| `rina.memory._user_behavior` | prohibited | process-global memory can mix users and disappears across workers |
| `AdvisorNote` | keep restricted | never inject into client-facing Rina by default |
| `ClientProfile` preferences | keep client-safe | use only approved fields and preserve encryption boundaries |

No new generic `RinaMemory` table is approved.

## 6. Current authority model

### Global roles

| Role value | Product meaning | Current global authority |
|---|---|---|
| `user` | client/owner account | ordinary authenticated access |
| `driver` | assigned vehicle operator | driver dashboard and assigned-vehicle workflows |
| `admin` | advisor/administrator | broad advisor/admin access |

Aura currently does not represent advisor and administrator as distinct role values. That distinction must be designed before role-specific AI tools are expanded.

### Canonical direction

`security.access.require_vehicle_access` is the clearest current reusable object-level authorisation contract.

It resolves:

- active owner through `CarOwnership`;
- active driver through `CarDriver`;
- advisor through `User.is_admin` when explicitly allowed.

The chat route, `RinaContextService` and `RinaChatEngine` currently resolve authority separately. Wave 1.3 must replace those parallel paths with one canonical resolver.

## 7. Current Rina execution path

```text
POST /chat
    ↓
save raw user message
    ↓
resolve owner/driver vehicle list
    ↓
auto-select active vehicle
    ↓
write ConversationRecord
    ↓
calculate vehicle health
    ↓
RinaContextService.build
    ↓
RinaChatEngine.respond
    ↓
rina.ai_brain.generate_rina_response
    ↓
OpenAI API or safe fallback
    ↓
save assistant ChatMessage
```

### Critical findings

1. Chat history is user-scoped rather than vehicle-scoped.
2. Ambiguous vehicle scope auto-selects the first vehicle.
3. Driver context can be lost inside `RinaContextService.build`.
4. Behaviour memory is process-global.
5. Sensitive context/message logging has existed in production paths.
6. Prompt language has overstated live observation and continuous monitoring.
7. Provider orchestration is direct and lacks one governed policy boundary.
8. User-name resolution is inconsistent.
9. Chat errors may return HTTP 200 without structured operational telemetry.

These findings confirm that authority and memory must be hardened before voice, emotional calibration or tool execution.

## 8. Event-producing systems inventory

| Source | Evidence produced | Current event emission status |
|---|---|---|
| Reported Concern | reported, reviewed, monitoring, resolved | domain row only |
| Consultation | scheduled/requested, started, completed | domain row only |
| Assessment | draft, finalised | domain row only |
| Treatment Plan | approved, in progress, completed, deferred | domain row only |
| Vehicle DTC | detected, interpreted, cleared | domain row only |
| Recall | open/closed | domain row only |
| Maintenance Schedule | upcoming/due/overdue/completed | domain row only |
| Health Snapshot | score/status change | snapshot row |
| Health Alert | created/acknowledged/resolved | alert row |
| Driver Check-In | operational observation | direct row only |
| Ownership | transfer/start/end | ownership row plus broken audit/snapshot attempts |
| Conversation Record | concern/urgency/escalation summary | direct row |
| Advisor Note | internal continuity note | restricted row; should not auto-emit client-visible event |
| Service/Treatment record | service/treatment progression | currently the main `VehicleEvent` use |

Most progression-bearing workflows mutate only their domain record. Wave 1.2 must introduce one event-emission service rather than duplicate emission logic inside routes.

## 9. Communication and external integration inventory

| Integration | Current responsibility | Final decision |
|---|---|---|
| Railway | Flask runtime and PostgreSQL | canonical production runtime |
| OpenAI | Rina response generation | keep behind governed orchestration boundary |
| Resend | HTTPS transactional email | canonical outbound email adapter |
| Gmail SMTP | password reset | replace and deprecate |
| Meta WhatsApp Cloud API | outbound templates/text | canonical outbound WhatsApp adapter; add audit/webhooks before expansion |
| NHTSA vPIC | VIN decoding | canonical provider abstraction |
| Aura verified DTC knowledge | trusted local lookup/cache | canonical foundation |
| future licensed OEM/DTC providers | deeper knowledge | deferred pending commercial rights |
| Cloudflare | future edge/storage/security support | not Aura's current app runtime |

### Messaging gaps

- no durable email or WhatsApp delivery-attempt model;
- no Resend/Meta webhook status processing contract;
- provider IDs are not consistently correlated to user, subject, consultation or event;
- retry policy is incomplete;
- consent and preferred-channel enforcement are not unified;
- password reset bypasses Resend;
- route-level WhatsApp share links remain outside the canonical adapter in some paths.

## 10. Confirmed conflicts, defects and risks

### High priority

1. Rina vehicle-memory isolation is incomplete.
2. Rina authority is resolved through multiple implementations.
3. Sensitive production logging must remain redacted.
4. Rina must not imply live diagnostics or continuous observation without evidence.
5. Process-global behaviour memory must be removed.
6. Password reset delivery is split between SMTP and Resend.
7. Driver scoring conflicts with Aura's no-gamification rule.
8. Concern state helpers do not fully recognise the `reported` state.
9. Stewardship routes attempt `EventAuditLog(event_id=None)` despite a non-nullable foreign key.
10. Stewardship snapshot invocation uses the wrong keyword argument.
11. The registered health trajectory route calls a nonexistent service method.
12. Trajectory and care-signal services disagree on deterioration keys.
13. Some advisor helpers call the boolean `is_admin` property as a function.

### Database and migration risks

1. SQLite-specific JSON declarations are present while production is PostgreSQL.
2. Current CI rehearses migrations only on SQLite.
3. `CarOwnership` does not enforce one active owner per car.
4. health-alert uniqueness blocks repeated resolved cycles.
5. daily driver check-in uniqueness is application-only.
6. workflow states are mostly free-form strings.
7. `VehicleEvent.event_date` and `created_at` have ambiguous occurrence semantics.
8. `VehicleEvent.mileage` and `created_by` are too restrictive for system events.
9. destructive Alembic downgrade functions make downgrade unsafe as a normal production rollback.
10. DTC legacy snapshots remain intentionally and require verified backfill before removal.

## 11. Migration and production verification

The checked-in Alembic graph is linear and has one repository head:

```text
d42e7a1c9b50
```

Railway production was directly verified with:

```bash
python -m flask db current --verbose
```

The live PostgreSQL database reported:

```text
Rev: d42e7a1c9b50 (head)
Parent: c19f2a8b6d41
```

Repository and production migration heads therefore match.

Wave 1.2 must use staged additive revisions, PostgreSQL rehearsal, feature-gated emitters and code rollback/forward fixes rather than destructive downgrade.

## 12. CI coverage and gaps

### Covered

- security foundation;
- route rate limiting;
- email verification;
- encrypted client profile model/routes;
- session registry and revocation;
- owner-driver authorisation;
- WhatsApp adapter tests;
- Rina provider-failure handling;
- SQLite migration rehearsals and reconciliation;
- Ruff and Bandit on selected paths.

### Required additions for Wave 1.2+

- PostgreSQL fresh migration and production-shaped upgrade jobs;
- canonical event-emission integration tests;
- vehicle-isolated Rina history tests;
- one-authority-resolver tests;
- Resend and Meta webhook delivery-status tests;
- durable idempotency/retry tests;
- stewardship and trajectory regression tests;
- database integrity tests for ownership, alerts and check-ins;
- shared Redis rate-limit test;
- structured log-redaction tests.

## 13. Final keep / merge / deprecate matrix

| Component | Decision |
|---|---|
| `VehicleEvent` | keep and extend additively |
| `EventAuditLog` | keep as compatibility event audit; do not generalise |
| `security.access` | make canonical vehicle-authority service |
| `CarFault` | keep under legacy table name |
| `VehicleHealthSnapshot` | keep; fix writer contracts |
| `VehicleHealthAlert` | keep; repair recurrence constraint |
| duplicate health trajectory routes | merge |
| `routes/assessments.py` | deprecate as inactive stub |
| client/admin assessment download modules | keep |
| broad `admin/routes.py` logic | keep temporarily; extract incrementally |
| `ChatMessage` | keep and vehicle-scope |
| `ChatSession` | dormant / compatibility-only |
| `UserMemory` | legacy-partial; do not expand |
| `ConversationRecord` | keep as reviewed durable summary |
| Flask Rina session context | keep ephemeral only |
| process-global behaviour memory | remove/prohibit |
| `rina.brain` legacy helper | deprecate after callers migrate |
| `services.email_delivery` | keep canonical |
| Gmail SMTP reset flow | replace/deprecate |
| `services.whatsapp` | keep canonical outbound adapter |
| route-level direct/share-link messaging | merge into governed channel workflow |
| SQLite-specific JSON usage | do not repeat; harden cautiously |
| driver score/gamification | deprecate product use |

## 14. Final Wave 1 decisions

1. Extend `VehicleEvent`; do not create a parallel progression table.
2. Keep domain models authoritative for current state.
3. Use one append-oriented event-emission service.
4. Use one vehicle-authority resolver across routes, Rina and tools.
5. Layer Rina memory instead of creating one unrestricted memory table.
6. Converge transactional email on Resend HTTPS.
7. Keep WhatsApp as an adapter until delivery, consent, webhook and audit contracts exist.
8. Keep predictive health blocked until sufficient structured longitudinal data and validation exist.
9. Preserve advisor authority, provenance, abstention and non-diagnostic language.
10. Use code rollback and forward fixes while preserving additive schema/data.

## 15. Exit decision

Issue #29 has met its objectives:

- [x] current runtime and blueprints mapped;
- [x] models and relationships classified;
- [x] authority paths audited;
- [x] Rina execution and memory layers audited;
- [x] route and event behaviour traced;
- [x] service ownership classified;
- [x] communication paths audited;
- [x] migration graph and rollback boundaries documented;
- [x] production revision verified as `d42e7a1c9b50`;
- [x] CI gaps catalogued;
- [x] keep/merge/deprecate matrix completed;
- [x] final `VehicleEvent` extension decision locked.

PR #34 is ready for review. Issue #30 may proceed to design only after PR #34 is merged.
