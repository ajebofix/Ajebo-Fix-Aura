# Prince Automobile Clinic × Aura — Private staging runbook

**Status:** code-only pilot; not deployed; no real customer data or staff credentials provisioned.  
**Source branch:** `feature/prince-pilot-foundation`; **PR:** #351 (draft).

## What currently exists

- Disabled-by-default `/pilot/workshops/<slug>/operator` phone-friendly intake and assigned-job page.
- `POST /pilot/workshops/<slug>/intake`: accepts limited customer/vehicle data after a matching approved privacy-notice version and a recorded participation checkbox. It encrypts phone, email and address fields, and returns only customer ID, full name and draft job ID.
- `GET /pilot/workshops/<slug>/jobs`, `GET /pilot/workshops/<slug>/jobs/<id>`, and `PATCH /pilot/workshops/<slug>/jobs/<id>/concern`: member/tenant and job-assignment scoped.
- The app has a separate tenant, membership, customer, vehicle, job and audit schema, additive migration, rate limit for intake, and automated SQLite/PostgreSQL security tests.
- The page displays the original supplied Prince brand logo, resized for the compact header; no third-party trackers, hosted fonts or client data.

## Never do this

1. **Do not** deploy or enable this branch on the production Aura service.
2. **Do not** repurpose the existing Railway "Aura Signature UI Staging" service: it uses an unrelated branch and database for separate work.
3. **Do not** reuse a real owner's contact details, messages or vehicle identifiers in test cases.
4. **Do not** seed the workshop using a global `admin` role as a substitute for explicit `PrinceMembership`.
5. **Do not** copy Access Bank login credentials, PINs, statements or payment confirmations into source code, environment variables, API requests or screenshots.
6. **Do not** turn on the API without a private, isolated environment and a completed security/retention/consent review.

## Safe private demo prerequisites

- A dedicated staging service with **its own empty PostgreSQL database**, separate from all Aura live and other staging databases; restrict staging URL access with the hosting provider or proxy access controls. New paid infrastructure must be approved before creating it.
- Branch `feature/prince-pilot-foundation` pinned to an inspected commit SHA, with no auto-deploy from `main`.
- Production-like secure `SECRET_KEY` and `PROFILE_ENCRYPTION_KEY` in staging secrets storage, not in GitHub. No production secrets may be cloned.
- `APP_ENV=staging`, `AURA_PRINCE_PILOT_API_ENABLED=true` only after migration, private access, membership and endpoint audit are verified. Set `AURA_PRIVATE_ACCESS_ONLY=true`.
- Run `flask db upgrade` against the isolated staging database, then verify `prince_* ` tables exist.
- Provision a **synthetic** workshop tenant (slug `prince`), with `active=true`, `intake_enabled=true` only in the isolated test database, and an explicitly approved *synthetic-demo-only* privacy-notice version. This is NOT authorization to collect real customer information.
- Create synthetic, individually authenticated accounts with role `user` for two operators and a principal, plus explicit tenant memberships. Never set a global administrator role to enable partner access.
- The site must not be indexed; its mere noindex header is not access control. Require login + infrastructure-level private access protection.

## Demo script using fake records

1. Authenticate as synthetic Operator A and load `/pilot/workshops/prince/operator` on a mobile browser.
2. Enter `Demo Customer A`, fake-only phone/email, Mercedes-Benz S-Class W221 (fictional exercise), and `Cranks; technician assessment pending`. Record synthetic notice acknowledgement.
3. Save; the page clears contact fields and displays only the name, vehicle, reported concern and assigned job.
4. Try direct URL access to unassigned job, other workshop slug, contact fields, raw attachments, CSV export and main Aura owner/admin endpoints. All inappropriate access must be denied; any leak blocks the pilot.
5. Authenticate as synthetic Operator B: Operator A's assigned job must not appear.
6. Authenticate as synthetic principal: all *Prince-only* jobs are visible, but no automatic repair or payment confirmation occurs.
7. Check server logs, response bodies, caches and browser back navigation for contact data, then verify the data is encrypted in the staging database.
8. Disable the test feature flag and verify the pilot routes return 404.

## Tests and current limitations

- Automated: `pytest -q tests/test_prince_access_policy.py tests/test_prince_pilot_api.py tests/test_prince_pilot_operator_ui.py`; `node --check static/js/prince_pilot_operator.js`; `ruff check` on the modified Prince modules. Dedicated CI also rehearses a fresh Alembic migration on disposable PostgreSQL.
- Not yet implemented: registered workshop staff provisioning UI, protected file/evidence upload, estimate/invoice/receipt workflows, vehicle-to-Aura verified service history bridging, customer-correction requests, contact-retrieval workflow for the principal, backups/retention/erasure, anti-duplicate intake review, and complete audit of **all existing** Aura global routes.
- Contact-pattern filtering in reported concerns is an additional defense, not proof that arbitrary personal information can never be copied into notes or photographed during intake.
- **Strict launch gate:** no real customer onboarding or Seun account invitation until application/API penetration tests, private staging deployment, legitimate workshop authority, and privacy/compliance checks are completed.

## Pass/fail decision

A green CI run validates code-level tests, not deployment safety. Require independent review, access testing, real device observation and explicit business approval before changing production. If testing fails, leave `AURA_PRINCE_PILOT_API_ENABLED` false, do not expose the site, and remediate in the feature branch.
