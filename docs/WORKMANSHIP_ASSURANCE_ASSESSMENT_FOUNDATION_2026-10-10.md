# Aura — Workmanship Assurance Assessment, Foundation and Delivery Gates

**Prepared:** 10 October 2026  
**Business direction:** Approved in principle by Ajebo Fix founder, subject to Nigerian legal review.  
**Policy version:** `AJEBO-WA-PROPOSED-2026-10-10`  
**Release status:** **INTERNAL FOUNDATION ONLY — NOT A CLIENT GUARANTEE; NOT DEPLOYED.**

## Decision

Ajebo Fix will not provide blanket 90-day, six-month or mileage-based additional guarantees. Additional voluntary contractual workmanship assurances will be considered per job using price, approved scope, vehicle condition, material and parts provenance, diagnostic uncertainty, quality control and foreseeable external risks. Competent workmanship and statutory remedies are not negotiable and remain available regardless of the additional warranty decision.

A short Tokunbo seller return deadline is **not** an Ajebo Fix installation-warranty limit. Equally, installing a used owner-supplied component does not oblige Ajebo Fix to indemnify the owner for an inherent, unrelated internal defect. Distinguish cause, scope and responsibility fairly.

### What this draft PR implements

- Pure internal evidence validation in `services/workmanship_assurance_policy.py`.
- Exactly four **proposed**, advisor-reviewed outcomes: no additional guarantee, limited individually specified assurance, tailored extended assurance, or decline/redesign unsafe scope.
- Verified vehicle and advisor identifiers and a nonempty job reference.
- Written scope, agreed labour amount, baseline and prior damage, supplier and parts provenance, documented seller testing window, materials and process, technical risk, QC evidence, client choices, environmental aftercare and advisor reasons.
- Explicit scope, conditions and individually chosen start/end dates required for any *proposed extra coverage*. **No automatic duration or mileage limit.**
- No default additional coverage, no price-based grant, no blanket Tokunbo rejection, no automatic environmental exclusion, no warranty on individual invoices by mere virtue of payment.
- Typed result always declares `additional_contractual_assurance_issued=False`, `external_publication_permitted=False`, `requires_manual_approval=True`, `statutory_rights_unaffected=True`.
- Automated regression cases: anonymised collision repair, owner-supplied Tokunbo installation, well documented funded service; incomplete proof, invalid period, missing job/advisor and hidden coverage claims.

### What this draft PR intentionally DOES NOT implement

- No migration, persistence table, public endpoint, owner access, email, publication, automatic warranty, renewal, receipt adjustment, insurance or legal terms activation.
- No production rollout or client communication. This is an internal validator and documented contract for the upcoming persistence/UI implementation, not a functioning warranty-issuance system.

## Planned persisted workflow — next implementation phase

**Link each assurance review to an existing Aura `car_id` and exact Billing job UUID or job number**, and optionally to the authorised Treatment Plan and completed Treatment Action(s). Verify the linked job actually belongs to the same vehicle/owner; do not trust free-form job numbers as authority.

Proposed tables (to be created in separately reviewed migration after source-of-truth analysis):

1. `workmanship_assurance_reviews` — immutable review identity; linked car, original job and treatment action, advisor creator, policy version, terms-version snapshot, evidence references, condition and provenance, financial scope, risk details, client options, revision/current state, timestamps. Advisor-only until deliberate publication.
2. `workmanship_assurance_decisions` — additive versioned human decisions linked to a review; exact covered scope, exclusions grounded in causation and client consent where applicable, no-additional-guarantee reason, proposed extra start/end and conditions when relevant, reviewing advisor, QC status, approval timestamp. Never silently overwrite earlier accepted terms.
3. `workmanship_assurance_claims` — incoming concern, evidence, relationship to prior work, findings and reasoned outcome, remediation, cost/provenance audit; claim right to review is not automatically lost when the voluntary period ends.

A durable record can move through `draft → evidence_ready → advisor_reviewed → legal_terms_verified → client_offer_approved → issued_or_none → closed`. Only a human administrator may approve external offer and issuance after policy publication. Consider separate owner acknowledgement, digital signature and change-history mechanisms; receipt of an email is not proof of acceptance. Corrections should be additive.

## Safety and access requirements

- Client/driver must not view financial risk score, labour margin, technician accountability notes, third-party supplier claims, or internal negotiations.
- No Rina/AI automated promises or claim rejections. AI may assemble facts and propose evidence to review.
- No automatic “warranty void” for client-supplied parts, flood, potholes, missed aftercare or third-party work; examine actual causation.
- Unsafe jobs must be redesigned or declined, not performed with a waiver that defeats statutory duties.
- Any client-facing document must give a job-specific covered scope, exclusions, assurance period **only if deliberately granted**, parts manufacturer/seller limitations and complaint procedure, and preserve statutory rights.
- Never backdate or retrospectively apply terms to earlier agreed jobs. Anonymise pilot scenarios.
- Require legal review and executive release approval before adding public URLs, PDFs or auto-filled wording to signed client documents.

## Pilot acceptance scenarios

### A. Prior-accident collision repair with owner-supplied parts and budget paint

Evidence: previous collision/repairs, severity and structure baseline, panel origin and fit, material tier, paint substrate, prior filler/rust, recommended vs accepted scope, QC/finish/handover photos, aftercare instructions. Expected draft recommendation may be no extra voluntary assurance or narrow documented workmanship; never auto-exclude poor paint application or promise the repaired car is original-crash safe.

### B. Client-supplied Tokunbo engine

Evidence: seller proof and independently confirmed short test window, component provenance, pre-install diagnostics, installation requirements, what owner declined, test timing. Expected draft may have limited optional scope or no extra written guarantee; Ajebo Fix remains accountable for incorrect fitting, not automatically for old internal defects. Do not set the workshop coverage to the supplier's 3–5-day anecdote without independent agreement.

### C. Properly priced routine service with controlled new parts

Evidence: original scope, genuine part invoices, technician checklist and QA, service conditions. Advisor may propose a narrowly defined additional assurance with bespoke dates after written approval. No automatic duration.

## Related gated releases

- Official website PR #102: approved conceptual approach, *draft pending Nigerian lawyer review*. The service terms route must be live before links are activated.
- Aura PR #315: invoice/receipt/settlement and policy link improvements, held until official website publication and release approval.
- The specific job acceptance is always the source of truth for price and commercial obligations; policy changes are not retroactive.

## Definition of done for full Aura feature

Working admin-only form, versioned persisted reviews and human decisions, role isolation, clear qualified source-job binding, legal/policy gates, complaint intake, no-client-leak tests, migration verification and rollback plan, mobile form tests, and advisor-confirmed anonymous pilot tests, all passing in CI and staging before any production activation.
