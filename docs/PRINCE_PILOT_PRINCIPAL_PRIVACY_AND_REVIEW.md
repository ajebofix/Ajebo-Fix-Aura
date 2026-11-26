# Prince × Aura — customer privacy and equal-principal approval controls

**Branch:** feature/prince-pilot-foundation · **Status:** synthetic-only Prince staging, not approved for real customer records.

## Access model
- **Operator A** is a fictional account representing Seun's intended operator role. **Operator B** is a second fictional account for separation testing.
- **Dad** (Demo Workshop Principal) and **Stephen** (Demo Stephen Principal) each have an independent login, independent audit identity and the exact same `principal` membership on the Prince tenant. Neither needs the other's password.
- Staff can create a customer **full name** and a vehicle/job record; the intake JSON schema rejects `phone_number`, `email`, `home_address` even if an operator tampers with the client form. Names are still personal data.
- A **principal-only** GET/PATCH customer/private route reads and changes phone, email and home address under encryption and records `private_contact_accessed` and `private_contact_updated` without storing those values in audit metadata. Private fields are not initially rendered in HTML; principals click to unlock and can lock/clear after editing.
- Pilot administrators are **not** workshop principals. Technical admin memberships cannot read/correct contacts, approve estimates, verify payments or attest service. Global Aura administrator roles don't bypass Prince membership checks.
- Staff continue to see only their assigned job record. Principals see every Prince job; neither account is allowed to access another tenant's customers.
- Private customer contact details are **not** available to operators through customer intake, job responses, job history, private-contact endpoints or the UI. Operators are additionally excluded from principals' free-text review notes.
- Contact details never belong in job description, review notes, evidence captions or diagnostic fields. Text filtering is only a secondary defense; a person can still remember or photograph data seen elsewhere.

## Principal review of work
- `PrinceEntryReview` is **separate, append-only** evidence of one principal's decision about a previously recorded `PrinceJobEntry`.
- Decision options: `acknowledged`, `needs_evidence`, `not_confirmed`; `work_confirmed` is available **only** for an entry of category `reported_work`.
- `work_confirmed` needs a specific evidence basis: `direct_inspection`, `documented_evidence` or `technician_demonstration`, plus a substantive written attestation. The system **does not independently validate evidence** at this stage; it records the principal's signed-in declaration.
- Every review records principal actor ID, date/time and audit event. A second attempt is rejected. Wrong decisions need a future correction/supersession workflow rather than silent rewriting.
- The original technician's note and `review_state=unverified` are not modified. A principal attestation is displayed separately; it does not automatically mark the job complete, approve money, generate invoices/receipts or send any statement into Aura vehicle history.
- Operators can see the outcome, not the principal's free-text evidence basis/notes. Only principals may inspect the full review.

## Real-use and succession boundaries
- These are fictional stage accounts, **not yet actual Stephen, Dad or Seun staff credentials**.
- Giving Stephen equal operational access prepares for continuity but **does not legally transfer a registered business, ownership, estate assets, bank accounts or compliance obligations**. Those require independent authority and appropriate legal/accounting arrangements.
- The Access Bank login/PIN is never copied into Aura or Railway. Workshop account payment verification remains manual by an authorized human.
- Before real use: privacy notice and consent, audit and breach response, device/account recovery/MFA, staff offboarding, role delegation, immutable audit retention, supervised rollout and principal-approved safe data migration.

## Acceptance tests
1. Seun/Operator A cannot enter or post private fields. Neither Operator A nor B sees protected contact inputs or can call private endpoints.
2. Both principals independently read and edit the same synthetic customer contacts, without exposing them on initial page load.
3. Operator A cannot view Operator B's job; neither operator can review entries.
4. Dad or Stephen can separately review an assigned/unassigned Prince job; `work_confirmed` only accepts `reported_work` with a non-empty evidence basis and an attestation note.
5. The review does not change the technician statement and a duplicate review is rejected.
6. Run SQLite + disposable PostgreSQL migrations and tests; rehearse synthetic seeding of **four** accounts and existing training records; stage only if green.
