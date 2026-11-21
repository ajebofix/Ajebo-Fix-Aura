# Prince pilot — structured, unverified workshop records

**Status:** development/staging only; real workshop use blocked until consent, staff privacy, audit and safety acceptance gates are met. All existing Prince demo roles and accounts remain fictional.

## Why this change

A legacy `reported_concern` text field could contain a complaint, a technician observation or even a workaround. Updating that field overwrote the earlier content and made it difficult to tell what was reported versus done. The new version leaves **all legacy text untouched**, shows it as **Earlier job note · Unclassified**, and writes new statements as append-only entries.

## Entry types

| Internal category | Display meaning | What it does **not** prove |
| --- | --- | --- |
| `customer_complaint` | Customer complaint (reported) | An established diagnosis |
| `technician_observation` | Technician observation | That a causal fault is confirmed |
| `diagnostic_finding` | Diagnostic finding (pending review) | That an assessment has been signed off |
| `temporary_intervention` | Temporary intervention / workaround | That the vehicle is permanently repaired or safe |
| `reported_work` | Work reportedly performed (not verified) | That the workshop principal verified completion |
| `outstanding_concern` | Outstanding concern | That another repair has been authorized |
| `next_action` | Recommended next action | That the work is approved or scheduled |

Each entry receives a server-side time and authenticated author user ID; `review_state` is fixed to `unverified` and cannot be changed through the operator API. No payment/receipt, approval or service-history completion is generated. Future principal verification must be separately designed and reviewed.

## Security and persistence

- SQLAlchemy `PrinceJobEntry` and Alembic `c84e91f6b207` add a `prince_job_entries` table with tenant/job composite foreign key, allowed category and required unverified state constraints.
- `POST /pilot/workshops/<slug>/jobs/<job_id>/entries` appends a new category/body after tenant, staff membership and assigned-job authorization. A closed job cannot be modified.
- `GET .../entries` returns only the authenticated principal's tenant jobs or the operator's assigned job entries. Data is redacted for potential contact text exposure, and the write endpoint rejects phone/email patterns.
- Mobile operator page displays prior entries and a specific category selector; the browser adds the server-confirmed entry using safe text nodes, not HTML interpolation.
- The old `PATCH .../concern` endpoint now returns **410 Gone** after access checks: stale screens must reload, and old content is never overwritten.
- Data is append-only at the API surface; the database schema and credentials still require a broader security audit before real client use. Administrative DB users could still edit records, so database-level immutability enforcement remains future work.

## Staging-only controls

- The staging login restriction remains active and only permits the exact authorized Railway project/environment.
- New entries in this training workspace must begin with `Demo:` or `Synthetic:`; normal private phone/email details are rejected.
- The old demo W221's earlier free-form note is **not automatically reclassified**. It may be based on a real vehicle case and must not be treated as actual service-history evidence in this fictional staging environment.
- Demonstrate two distinct **fictional** events: `Demo: customer reports a no-start symptom` (Customer complaint) and `Demo: temporary feed tested by technician; outcome not confirmed` (Temporary intervention).
- Real workshop records, parts, customer contact data, VINs, bank statements and repair decisions must not be entered into this staging demo.

## Acceptance gates

1. Verify existing API and UI tests pass on disposable SQLite and PostgreSQL, including direct URL tenant/assignment checks, privacy redaction, CSRF, no cross-operator records and closed jobs.
2. Rehearse additive Alembic migration `c84e91f6b207` against PostgreSQL with existing Prince demo rows. Confirm old free-form notes are unchanged.
3. Deploy only to the isolated Prince staging Railway project pinned to the reviewed commit. Do not merge to `main` or deploy production.
4. Verify on actual iPhone: notes are readable, selector and save work, entered category persists on reload, earlier unclassified note remains unchanged, and other operator does not see it.
5. Before real use: stronger PII text checks, principal review/approval workflow, evidence provenance and attachment policy, backfill/correction process, immutable audit retention, tenancy review across old Aura endpoints and written data-processing/consent arrangements.

