# Prince Automobile Clinic × Aura — implementation foundation (draft)

Status: **pre-integration / not deployed**. Branch: `feature/prince-pilot-foundation`.
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
- The new `partnerships/prince_access_policy.py` contains **pure, unconnected policy primitives only**, not production enforcement.

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
