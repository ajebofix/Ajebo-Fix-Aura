# Prince Automobile Clinic × Aura — implementation foundation (draft)

Status: **tenant-scoped API and schema drafted; disabled and not deployed**. Branch: `feature/prince-pilot-foundation`.
Owner: Ajebo Fix Ltd / Aura. Partner: Prince Automobile Clinic.
No real customer records, banking credentials, payment references or client contact details belong in this public repository.

## Verified discovery
- Prince and Ajebo Fix occupy the same physical workshop address but must remain distinct tenant/business scopes.
- Prince works on approximately 1–5 vehicles per week, primarily Mercedes-Benz.
- The proprietor approves estimates and verifies bank transfers from his Access Bank business account; Stephen assists with complex quotes and supervises technology.
- Seun is the nominated operator and must be trained; he can create customer intake but must not subsequently retrieve private contact information.
- Estimates are currently prepared manually or in WhatsApp; no regular receipt-generation procedure exists.
- The first candidate is a Mercedes-Benz W221 currently at Prince; reported fuel-delivery/no-start history is **not a confirmed diagnosis**. A W211 is a tentative second candidate.
- Prince registration and tax filing status need independent verification before production invoicing.
- Logo asset supplied separately in the conversation. It is **not** committed to GitHub on this branch.

## Existing Aura architecture risks
- The present `User` model has general `user/admin/driver` roles and a global phone-number field. Do not grant Seun a standard `admin` account.
- `Car.vin` is non-null and globally unique. A Prince vehicle with unknown VIN cannot safely be inserted into that entity by fabricating a VIN.
- Existing vehicle routes, ownership, evidence downloads, profile APIs, searches, report exports, Rina access, and administrative dashboards need tenant-by-tenant review before partner access.
- A shared street address is not a permission boundary.
- `partnerships/prince_access_policy.py` is invoked by the new, feature-gated `partnerships/prince_routes.py` endpoints. It does **not** protect legacy global Aura endpoints; no partner user should be provisioned until the whole route surface is audited.

## Security invariants and acceptance gates
1. **Isolation:** Prince users cannot read or mutate Ajebo Fix records through ID enumeration, direct API calls, search, CSV/PDF export, download keys, Rina or admin endpoints; and vice versa.
2. **Seun:** May submit approved customer intake fields; success response includes only internal ID and full name (plus job ID if assigned). No contact values are returned or retrievable afterward; only assigned jobs are visible.
3. **Sensitive data:** Protect numbers, emails, addresses, private messages, source documents, bank details, and documents containing embedded contact details. Do not embed private data in logs, background tasks or notifications.
4. **Authority:** Seun may draft estimates and report that payment was claimed; only proprietor (or expressly delegated verifier, with audited authorization) approves client-facing estimates, confirms payments and repairs.
5. **Auditability:** Append-only actor, tenant, time, action and target metadata; preserve previous financial/history values; avoid storing contact field values in logs.
6. **No misleading service history:** Keep reported concern, test, technician observation, recommendation, approved work, paid invoice and verified completed work separate. The W221 fuel-line observation remains unconfirmed pending review.
7. **Consent and provenance:** Record lawful basis, privacy notice delivery and evidence/source metadata before storing identifiable real customer data.
8. **Fail closed:** If tenant ID, assignment, authorization, encryption key or migration is missing, deny access; never silently use global users or cars as a fallback.
9. **No PIN/password collection:** The application must not collect, request, persist or proxy Access Bank credentials.
10. **Operational:** Backups, export/exit path, incident procedure, device access, data retention, and Seun's training tested before production.

## Build sequence
- **A — inventory:** Audit current Flask blueprints, middleware, route decorators, `User`/`Car` ownership queries, authorization on every read/export, evidence storage, profile encryption and database migrations. Record findings.
- **B — tenant & staff data model:** Business/tenant, scoped staff membership, assigned jobs, protected contact vault, job intake and append-only audit. Use Alembic migrations, indexes and foreign keys; design tenant-specific identifiers. Do not patch global `users.role` directly as an authorization shortcut.
- **C — secure API:** Staff authentication, create-only intake, redacted assignment-scoped job APIs, independent principal approval, protected attachments and no bulk exports. Add integration tests that exercise real HTTP endpoints and untrusted identifier changes.
- **D — workshop workflow:** Draft estimate → approval → invoice → reported transfer → verified payment → receipt, with revision/audit and separation from service completion.
- **E — Aura health bridge:** Map only verified service events with original source, status, authorizer and date. Do not auto-promote estimate or payment to confirmed repair.
- **F — controlled onboarding:** Test with fictional entities and verify direct API denial before bringing authorized W221 customer records into a private environment.

## Pilot acceptance tests (mandatory before live onboarding)
- Operator submits a real-shaped intake payload; response and subsequent GET/list/search/export never reveal contact details.
- Operator directly requests an unassigned job ID, cross-tenant car ID, attachment or PDF; all are rejected.
- Operator cannot approve estimates, verify transfers, confirm repair, change role or list all customers.
- Principal can approve a quote but cannot accidentally certify a repair by accepting payment.
- Missing VIN remains unknown without forged placeholder; vehicle identity can be reconciled later with audit.
- Database returns protected fields only to authorized parties; no exposed contact values in exceptions, task logs or cached responses.
- Real customer records are not added until privacy notice, authority and approvals are documented.

## Pilot evaluation
Start with three authorized cars, 1–5 weekly job volume, trained Seun as principal data operator, weekly Stephen quality review, and proprietor approval. Track time to estimate, accuracy, incomplete records, follow-up compliance and whether the W221's unresolved concern history is correctly preserved. No customer-facing Rina or automatic banking integration in initial scope.


## Engineering update: 21 November 2026

A development-only implementation is committed on the Prince feature branch:

- `partnerships/prince_models.py`: separate, tenant-scoped workshop, membership, customer, vehicle, job and audit models. Unknown VIN remains SQL NULL. Encrypted contact fields reuse Aura's versioned profile encryption service.
- `partnerships/prince_routes.py`: JSON endpoints under `/pilot/workshops/<slug>` for create-only intake, assigned job listing/detail and reported-concern update. These endpoints do not use the legacy `Car` or owner `User` as a customer record.
- `migrations/versions/b71e5ca90d24_prince_pilot_isolated_workspace.py`: additive migration; refuses downgrade if any Prince pilot records exist.
- `tests/test_prince_access_policy.py` and `tests/test_prince_pilot_api.py`: synthetic-data policy tests plus Flask HTTP integration checks for redaction, cross-workshop access denial, assignment scoping, intake gate and CSRF.
- `.github/workflows/prince-pilot-ci.yml`: dedicated security test workflow.

### Operating and safety gates

1. `AURA_PRINCE_PILOT_API_ENABLED` is **false by default**, even if migrated tables exist. Do **not** enable in production before the full privacy/security review.
2. No Prince tenant or staff memberships are seeded automatically. `PrinceTenant.active`, `PrinceTenant.intake_enabled` and `PrinceMembership.active` are false by default. Approved privacy notice version is required. Provisioning and training procedures remain to be built and reviewed.
3. Operator API responses allowlist customer ID, full name and authorized job/vehicle metadata. Sensitive contact details are stored encrypted and not served through these endpoints. This is **not** a guarantee against screenshots, memorized information, or contact information inadvertently inserted into free-text job notes.
4. No routes exist yet for bills, payment verification, receipts, customer-contact read, file uploads/downloads or direct live Aura service-history bridging. They must be designed and tested separately; do not infer their availability from the new schema.
5. Migration must pass a fresh-PostgreSQL rehearsal and be reviewed for production compatibility before deployment. Tests in disposable SQLite exercise HTTP access, not the entire production security environment.
6. Partner operators use existing authenticated Aura `User` accounts plus **separate** tenant memberships. A normal Aura `admin` role is not a substitute for a Prince membership.
7. The existing Aura global endpoints still need route-by-route audit to ensure there are no indirect information exposures. No live Seun account, real client records or W221 information have been created through these changes.

### API contract (prototype only)

- `POST /pilot/workshops/prince/intake`: authenticated, active tenant member; approved notice and enabled intake required. Fields: full name, optional phone/email/address, optional brand/model/year/concern, approved privacy-notice version and participation-confirmed flag. JSON response returns only customer ID, full name and optional draft job ID. Backend stores contact ciphertext.
- `GET /pilot/workshops/prince/jobs`: principal/pilot administrator sees tenant-scoped jobs; operator sees only jobs assigned to their authenticated user ID; maximum 50 records.
- `GET /pilot/workshops/prince/jobs/<id>`: enforced tenant scope and job assignment before allowlisted response.
- `PATCH /pilot/workshops/prince/jobs/<id>/concern`: assigned staff can update a reported concern while the job is not closed. Does not confirm diagnosis, repair completion, an estimate or payment.

**Open issues before any real customer onboarding:** compliance review, real privacy notice/authorization evidence procedure, user/device provisioning, operating training, rate and payload limits, sensitive free-text minimization, full endpoint/data-layer security review, protected documents, financial approval rules, and production migration review.
