# Aura Current-System Architecture Audit

**Issue:** #29  
**Parent epic:** #28  
**Status:** Draft v0.1 — repository inventory in progress  
**Scope:** Documentation and architecture decisions only  

## 1. Purpose

This audit maps Aura as it exists before Wave 1.2 introduces canonical progression intelligence.

It exists to prevent sideways development: duplicate models, parallel route families, competing memory stores, inconsistent authority checks and speculative AI features that bypass Aura's advisor-led, non-diagnostic identity.

No production behaviour, migration, route or model change belongs in this audit pull request.

## 2. Executive position

Aura already contains substantial foundations across identity, vehicles, ownership, drivers, clinical care, Vehicle Intelligence, health, communication and Rina.

The problem is not an absence of components. The problem is that several components overlap, retain legacy names or fields, or enforce related responsibilities through different contracts.

### Current decision on Issue #30

Issue #30 remains design-blocked until this audit is complete.

The initial evidence suggests that `VehicleEvent` is a credible foundation for a canonical event layer because it already has vehicle scope, ownership scope, source, fingerprint-based duplicate protection, flexible data and an audit companion.

However, it does not yet provide the full Wave 1 contract:

- immutable occurrence timestamp;
- actor identity and actor authority;
- subject type and subject ID;
- schema version;
- previous and new state;
- progression direction;
- visibility policy;
- correlation and causation identifiers;
- explicit evidence links;
- correction semantics;
- portable JSON definition;
- a consistent emission contract across care workflows.

**Preliminary recommendation:** extend `VehicleEvent` rather than create a parallel event table, unless route/service tracing reveals an incompatible responsibility or unsafe migration path. This recommendation is not final until the audit is reviewed.

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

| Blueprint | Prefix / route family | Current responsibility | Audit status |
|---|---|---|---|
| `auth_bp` | `/auth` | signup, login, logout, password reset | canonical, review delivery split |
| `advisor_bp` | `/admin` | advisor compatibility/auth routes | needs overlap review |
| `email_verification_bp` | security-owned | verification and resend | canonical |
| `session_registry_bp` | security-owned | active sessions and revocation | canonical |
| `cars_bp` | cars-owned | client vehicle workflows | canonical candidate |
| `dashboard_bp` | dashboard-owned | role-aware home/dashboard | canonical candidate |
| `driver_bp` | `/driver` | driver dashboard, concerns and check-ins | canonical candidate |
| `profiles_bp` | `/profile` | secure client profile and privacy centre | canonical |
| `admin_bp` | `/admin` | advisor/admin operations | broad; requires route decomposition |
| `chat_bp` | `/chat` | Rina conversation and history | active but unsafe to extend unchanged |
| `treatments_bp` | `/treatments` | treatment records/actions | requires lifecycle review |
| `audit_bp` | `/audit` | event audit views/actions | requires event-contract review |
| `intelligence_bp` | `/intelligence` | VIN/DTC intelligence routes | canonical candidate |
| `health_bp` | `/health` | health surfaces/services | requires overlap review |
| `notices_bp` | `/clinical_notices` | clinical notices | purpose/ownership pending |
| `health_trajectory_bp` | `/health_trajectory` | health trend/trajectory | overlaps historical trend modules |
| `stewardship_bp` | `/stewardship` | ownership/stewardship lifecycle | canonical candidate |
| `concerns_bp` | concern routes | reported concerns | canonical client-facing concept |
| `assessments_bp` | assessment routes | advisor assessment workflow | canonical candidate; duplicate modules pending |

### Confirmed route-family overlap to classify

The repository contains historical or parallel locations including:

- `health/trend_routes.py`;
- `routes/health_trends.py`;
- `routes/assessments.py`;
- `cars/modules/assessments.py`;
- `admin/modules/assessments.py`.

Only registered blueprints are runtime-active, but every duplicate file must receive one of:

- keep as canonical;
- compatibility-only;
- merge;
- deprecate;
- delete in a later dedicated PR.

## 5. Canonical domain model catalogue

### 5.1 Identity, people and authority

| Model | Responsibility | Current classification | Findings |
|---|---|---|---|
| `User` | account identity, password hash and global role | canonical | roles are `user`, `admin`, `driver`; advisor authority is represented by `admin` |
| `CarOwnership` | vehicle-to-owner relationship and care-plan context | canonical | active-owner integrity requires database review |
| `CarDriver` | vehicle-scoped driver assignment | canonical | active assignment integrity requires constraints review |
| `AccessCode` | invitation/access-code lifecycle | legacy-sensitive | stores plaintext code; atomic/hash hardening must be confirmed before expansion |
| `ClientProfile` | one-to-one personal/care preferences | canonical | protected fields encrypted at application layer |
| `ProfileAuditEvent` | privacy-safe profile mutation audit | canonical | stores metadata/field names, not plaintext submitted values |
| `UserSession` | authenticated device/session registry | canonical | security-owned dynamic model |

### 5.2 Vehicle identity and stewardship

| Model | Responsibility | Current classification | Findings |
|---|---|---|---|
| `Car` | canonical vehicle identity | canonical | owns relationships to events, concerns and intelligence |
| `VehicleProfile` | decoded VIN-derived enrichment | canonical | one-to-one with `Car` |
| `CarOwnership` | owner continuity and active care relationship | canonical | plate/is-active uniqueness does not by itself guarantee one active owner per car |
| `CarDriver` | active driver assignment | canonical | should feed one authority resolver |

### 5.3 Vehicle Intelligence

| Model | Responsibility | Current classification | Findings |
|---|---|---|---|
| `DiagnosticCodeDefinition` | reusable verified/cached DTC knowledge | canonical | generic/manufacturer uniqueness is correctly separated |
| `VehicleDTC` | vehicle-specific DTC occurrence | canonical with legacy fields | legacy description/system/severity snapshots remain temporarily |
| `VehicleRecall` | vehicle-scoped recall record | active foundation | definition/occurrence split not yet implemented |
| `MaintenanceSchedule` | vehicle-scoped due/completed maintenance | active foundation | no OEM template layer yet |

Vehicle Intelligence must remain provider-backed, provenance-aware and non-diagnostic. Missing definitions must abstain rather than fabricate.

### 5.4 Automotive care and advisor authority

| Model | Responsibility | Current classification | Findings |
|---|---|---|---|
| `CarFault` | client/driver Reported Concern | canonical concept under legacy table name | do not create a replacement merely to rename it |
| `Consultation` | consultation lifecycle and advisor assignment | canonical candidate | status contract requires enforcement review |
| `VehicleAssessment` | authoritative consultation-linked assessment | canonical | one assessment per consultation; finalisation boundary exists |
| `VehicleAssessmentRisk` | advisor-authored assessment risk | canonical | internal professional knowledge |
| `VehicleAssessmentTreatmentOption` | assessment treatment options | canonical | separate from final treatment-plan progression |
| `TreatmentPlan` | managed treatment progression | canonical candidate | state machine and action/event linkage require review |
| `AdvisorNote` | internal client/vehicle relationship intelligence | canonical and advisor-only | must never enter client-safe memory by default |
| `ConversationRecord` | durable clinical-style conversation summary | canonical candidate | vehicle-scoped but authority/visibility/provenance need expansion |

### 5.5 Health, events and alerts

| Model | Responsibility | Current classification | Findings |
|---|---|---|---|
| `VehicleEvent` | generic vehicle timeline event | canonical candidate | strongest base for Wave 1.2, but contract incomplete |
| `EventAuditLog` | create/edit/delete audit snapshots | legacy-compatible | canonical events should prefer additive correction rather than silent mutation |
| `VehicleHealthSnapshot` | point-in-time calculated health state | canonical evidence source | already indexed by car/time |
| `VehicleHealthAlert` | active health notice/escalation | canonical candidate | comment still lists `predicted_failure`; predictive semantics must remain disabled until #33 |
| `DriverCheckIn` | daily operational observation | canonical evidence source | duplicate prevention is currently application-level, not database-enforced |

### 5.6 Rina and conversation structures

| Model / store | Intended responsibility | Current state | Decision needed |
|---|---|---|---|
| `ChatMessage` | raw chat turns | active | currently user-scoped, not vehicle-scoped |
| `ChatSession` | conversation session lifecycle | apparently dormant | route does not use it directly; verify consumers |
| `UserMemory` | durable identification/preferences | apparently dormant or partial | separate from active Flask-session memory |
| Flask `session['rina_context_full']` | short-lived conversation context | active | appropriate only for temporary state |
| `ConversationRecord` | durable operational/clinical summary | active | should become reviewed, vehicle-scoped durable memory layer |
| `rina.memory._user_behavior` | behavioural tendency counter | unsafe global process memory | shared across users and workers; must not become production personalisation |

No new `RinaMemory` table should be created until these layers have explicit ownership, retention and visibility rules.

## 6. Current authority model

### Global roles

| Role value | Product meaning | Current global authority |
|---|---|---|
| `user` | client/owner account | ordinary authenticated access |
| `driver` | assigned vehicle operator | driver dashboard and assigned-vehicle workflows |
| `admin` | advisor/administrator | broad advisor/admin access |

Aura currently does not represent advisor and administrator as distinct role values. Wave 1 must decide whether that distinction is necessary before adding role-specific AI tools.

### Vehicle-scoped access helper

`security.access.require_vehicle_access` is the clearest current reusable object-level authorisation contract.

It can resolve:

- active owner through `CarOwnership`;
- active driver through `CarDriver`;
- advisor through `User.is_admin` when explicitly allowed.

### Authority gaps to resolve

- `RinaContextService.resolve_viewer_role` defaults any non-admin, non-driver user to `owner` without independently proving an active ownership relation.
- `RinaChatEngine.get_user_role_context` performs a separate role-resolution implementation.
- the chat route implements its own ownership/driver loading and vehicle selection.

These three paths should not evolve independently. Wave 1.3 should consume one canonical vehicle-authority resolver.

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

1. **Chat history is not vehicle-scoped.** `ChatMessage` queries filter only by user ID, so recent turns from one vehicle may enter another vehicle's context.
2. **Ambiguous vehicle scope auto-selects the first vehicle.** This is convenient but unsafe for a future authority-sensitive assistant.
3. **Driver context is lost inside `RinaContextService.build`.** The service reloads only owner `CarOwnership` rows; a driver-selected car may not become the active vehicle context.
4. **Behaviour memory is global.** `_user_behavior` is one module-level dictionary shared across all users handled by the process.
5. **Sensitive context is printed.** `RinaChatEngine` prints message, role and the complete context; this can expose personal and vehicle data in production logs.
6. **Prompt language conflicts with locked product boundaries.** The current system prompt tells Rina to speak as if observing the vehicle live, continuously monitoring it, and perceiving deterioration. This can overstate evidence and create an implied live-diagnostics claim.
7. **Provider orchestration is direct.** `rina.ai_brain` creates the OpenAI client directly; there is no central provider policy, timeout/quota contract, structured response schema or material-action audit layer.
8. **User name resolution is inconsistent.** Context requests `first_name`, while the canonical `User` model stores `name`.
9. **Chat errors return HTTP 200.** This may be suitable for UI continuity but weakens operational observability unless errors are separately structured and measured.

These findings confirm that Wave 1.3 must harden authority and memory before voice, emotional calibration or more powerful tools.

## 8. Event-producing systems inventory

The following workflows already produce durable progression evidence even when they do not emit `VehicleEvent` today:

| Source | Evidence produced | Current event emission status |
|---|---|---|
| Reported Concern | reported, reviewed, monitoring, resolved | pending trace |
| Consultation | scheduled/requested, started, completed | pending trace |
| Assessment | draft, finalised | pending trace |
| Treatment Plan | approved and later states | pending trace |
| Vehicle DTC | detected, interpreted, cleared | pending trace |
| Recall | open/closed | pending trace |
| Maintenance Schedule | upcoming/due/overdue/completed | pending trace |
| Health Snapshot | score/status change | service-generated snapshot |
| Health Alert | created/acknowledged/resolved | alert-owned |
| Driver Check-In | operational observation | direct row, no confirmed canonical event |
| Ownership | transfer/start/end | stewardship-owned |
| Conversation Record | material concern/urgency/escalation summary | direct row |
| Advisor Note | internal continuity note | should not automatically become client-visible event |

The audit must trace every mutation route/service and identify whether it writes:

- only the domain row;
- a `VehicleEvent`;
- a health snapshot;
- an alert;
- an audit record;
- more than one of these;
- none.

## 9. Communication and external integration inventory

| Integration | Current responsibility | Status / risk |
|---|---|---|
| Railway | Flask runtime and PostgreSQL | canonical Aura production runtime |
| OpenAI | Rina response generation | active, direct integration; governance incomplete |
| Resend | verification email over HTTPS | active and tested |
| Gmail SMTP | password-reset delivery | still present; creates split delivery architecture and Railway network risk |
| Meta WhatsApp Cloud API | booking/admin alerts and driver invitations | active; template/config diagnostics recently hardened |
| NHTSA vPIC | VIN decoding | active provider abstraction |
| Aura verified DTC knowledge | local trusted DTC lookup/cache | active foundation |
| future licensed DTC/OEM providers | deeper knowledge | deferred pending commercial rights |
| Cloudflare | future edge/storage/security support | not Aura's current application runtime |

### Immediate communication inconsistency

Email verification uses the shared Resend HTTPS delivery service, while password reset still opens a Gmail SMTP connection in `auth/routes.py`.

This is a confirmed incomplete capability and should become a separate small security/operations PR after the audit, not part of the documentation PR.

## 10. Confirmed conflicts, legacy structures and risks

### High priority

1. **Rina vehicle-memory isolation:** raw history is user-scoped rather than vehicle-scoped.
2. **Rina authority duplication:** route, context service and chat engine each resolve role/vehicle authority differently.
3. **Production logging exposure:** full Rina context and messages are printed.
4. **Overclaiming prompt:** live observation/continuous monitoring language exceeds recorded-data boundaries.
5. **Global behaviour memory:** one process-wide dictionary combines behaviour across users.
6. **Password-reset delivery split:** SMTP remains after verification moved to Resend HTTPS.
7. **Driver gamification drift:** `driver_score` changes during check-ins, conflicting with Aura's no-gamification product rule.
8. **Concern state mismatch:** driver reports create `CarFault.status='reported'`, while `CarFault.is_active()` recognises only `under_review` and `monitoring`.

### Database and migration risks

1. `models.py` imports `JSON` from `sqlalchemy.dialects.sqlite`, which requires portability review for PostgreSQL.
2. `CarOwnership` uniqueness on `(plate_number, is_active)` does not clearly guarantee one active owner per car and behaves poorly when plates are null or change.
3. daily driver check-in uniqueness is checked in route code rather than enforced by a durable database constraint.
4. several workflow states are free-form strings without database checks.
5. `VehicleEvent.event_date` is a date while `created_at` is a datetime; canonical occurrence semantics are ambiguous.
6. `EventAuditLog` supports edit/delete snapshots, while the future progression system requires additive correction and historical integrity.
7. DTC legacy snapshots remain intentionally; removal must wait for verified backfill and a dedicated migration.

### Structural duplication pending classification

- multiple health-trend route modules;
- multiple assessment modules;
- broad `admin/routes.py` alongside specialised admin modules;
- `advisor_bp` and `admin_bp` sharing `/admin` territory;
- `UserMemory`, `ChatSession`, `ChatMessage`, Flask session memory and `ConversationRecord`.

## 11. Proposed Wave 1 domain ownership map

| Domain | Canonical ownership direction |
|---|---|
| identity/session security | `security/` plus `auth/` |
| vehicle object access | one policy service built from `security.access` |
| owner/driver lifecycle | `ownership/`, `driver/`, `services.owner_driver_management` |
| reported concerns | `CarFault` plus concern service/routes |
| consultations/assessments/treatment | clinical/advisor service boundaries, not route-owned logic |
| Vehicle Intelligence | `intelligence/` provider/service architecture plus canonical models |
| progression | extend `VehicleEvent` if final audit confirms safe evolution |
| raw conversation turns | vehicle-scoped chat-message storage |
| durable Rina memory | reviewed `ConversationRecord`/progression records and client-safe preferences |
| temporary Rina context | Flask session or equivalent short-lived store |
| outbound email | one HTTPS delivery service through Resend |
| WhatsApp | one channel adapter with template and delivery audit contracts |

## 12. Work remaining before this audit can be final

- [ ] trace every registered route and mutation endpoint;
- [ ] trace all `VehicleEvent` creation/update/deletion call sites;
- [ ] trace snapshot and alert generation;
- [ ] classify every service module;
- [ ] inspect all migrations and identify the active head;
- [ ] compare workflow-state definitions across models, routes and templates;
- [ ] classify duplicate assessment and health modules;
- [ ] confirm use or dormancy of `UserMemory` and `ChatSession`;
- [ ] verify DTC provenance/verification fields in current migration state;
- [ ] map WhatsApp and Resend delivery audit behaviour;
- [ ] catalogue CI workflows and uncovered high-risk routes;
- [ ] produce a keep/merge/deprecate matrix;
- [ ] issue the final `VehicleEvent` extend/replace architecture decision;
- [ ] define migration and rollback constraints for Wave 1.2.

## 13. Interim rule

Until this document reaches reviewed final status:

- no new event table;
- no new Rina memory table;
- no predictive-health implementation;
- no duplicate vehicle-context resolver;
- no migration that renames or removes a working legacy structure;
- no AI feature that bypasses advisor authority, vehicle scope, provenance or abstention.

Critical security and production bug fixes may continue in separate, narrowly scoped pull requests.
