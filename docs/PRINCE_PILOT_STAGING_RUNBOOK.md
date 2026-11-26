# Prince Automobile Clinic × Aura — Private staging runbook

**Status (21 November 2026):** isolated Railway staging infrastructure deployed; app and PostgreSQL status SUCCESS, **no public domain**, Prince API flag disabled, no real customer data or workshop staff accounts provisioned.  
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

- **Provisioned:** a separate private Railway project `Prince Aura Pilot Staging` with its own PostgreSQL service and persistent data volume. Both services report successful deployment. A public staging URL has deliberately **not** been generated. This infrastructure may incur usage charges while running.
- **Provisioned:** branch `feature/prince-pilot-foundation` pinned to audited commit `6549c9406a6b81c72e4ba6a4a386faba7eea36ec`; changes to `main` will not automatically deploy to this service.
- **Provisioned:** independent staging `SECRET_KEY` and `PROFILE_ENCRYPTION_KEY` held in Railway variables, not in GitHub. These are not production secrets.
- **Configured:** `APP_ENV=staging`, `AURA_PRIVATE_ACCESS_ONLY=true`, `AURA_PRINCE_PILOT_API_ENABLED=false`, and no public domain. Do not enable the pilot API until private access, membership provisioning and endpoint auditing are verified.
- **Deployment startup succeeded** with repository `railway.json` migration/healthcheck configuration. The new PostgreSQL service needed `PGDATA=/var/lib/postgresql/data/pgdata` to avoid the volume mount root's `lost+found` directory. Independently confirm `prince_*` schema and migration state before enabling the API; deployment SUCCESS alone is not proof of every row/permission.
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

## Railway staging infrastructure checkpoint (2026-11-21)

- Project: `Prince Aura Pilot Staging`, standalone `staging` environment.
- Services: `Prince Pilot App` and `Postgres`; both reported terminal `SUCCESS` after the database mount fix.
- PostgreSQL: dedicated 1 GB persistent volume mounted at `/var/lib/postgresql/data`, `PGDATA` points to the `pgdata` child directory. No database TCP proxy provisioned.
- Application: source is pinned to commit `6549c9406a6b81c72e4ba6a4a386faba7eea36ec`. No Railway-generated or custom public domain. Pilot API disabled.
- Current onboarding status: **not activated**. No genuine customers or W221 records, no staff login invitation, no approved real privacy notice.
- Cost note: Railway resources are usage billed while running. Recheck spending and shut down staging when not actively needed.
- Remaining launch prerequisites: provision a synthetic principal/Seun-test operator with scoped memberships, run direct API smoke tests in the isolated database, verify no legacy route access for staff, protect the eventual staging URL with a separate access layer, and ensure the safety and privacy review is complete. Do not enable the public URL or use real customer data yet.
