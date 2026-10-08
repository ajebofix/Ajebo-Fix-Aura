# Aura × Ajebo Fix Billing — controlled owner bridge (PR #292, Issue #291)

## Current architecture (October 2026)
- **Aura** (Railway/PostgreSQL) owns the authenticated owner account, active CarOwnership, VIN, care record and Treatment lifecycle.
- **Billing** (existing Supabase project) remains the source of truth for commercial jobs, estimate/invoice versions, partial payments and receipts.
- A dedicated Supabase Edge Function at `/functions/v1/aura-billing-bridge` is the **only data retrieval boundary** used by Aura. It has no published anonymous information and permits only read operations.
- The Edge Function owns the existing Supabase service key; **Aura never receives a service-role credential**.
- Aura presents published commercial information at `/cars/<car_id>/billing` only for authenticated active vehicle owners whose email **in Aura** is verified. This does not require that the Billing client's email be present.
- The read-only integration is disabled in production until secure credential provisioning and owner publication are independently verified.

## Authorisation model
A successful client-side session alone does not grant financial access.

1. Aura verifies the signed-in user's role is `user`, their email is verified and there is one active `CarOwnership` for the requested car.
2. Aura sends only `{car_id, owner_user_id, vin}` server-to-server to Billing. Never send the bridge credential or owner identity in the browser.
3. Billing accepts requests **only** with a 256-bit, opaque, private bearer token whose SHA-256 digest matches an active stored credential. Wrong/missing bearer → 401.
4. Billing requires an explicit row in `aura_billing_publication_grants` matching **both Aura owner ID and car ID** with `published=true`, `revoked_at IS NULL`, and verified Billing vehicle/client UUIDs.
5. Billing additionally checks the existing `billing_vehicles.aura_vehicle_ref` equals Aura's car ID, its Billing client ID agrees with the grant and its full 17-character VIN matches.
6. Every document must also have its own active grant in `aura_billing_published_documents`; draft, superseded, revoked, unapproved or cross-client documents are excluded even if the vehicle grant exists.
7. Payments are retrieved only for visible invoices; no bank account, private note, contact detail, service token, personal payment reference, AI suggestion or supplier-cost fields are returned.
8. Retiring an Aura owner or revoking either publication grant immediately denies subsequent access. Client payment state and repair-work state are **independent**.

**Do not populate a client publication grant from name/VIN matching alone.** The pilot identity was manually reviewed, but the active Aura owner's numeric user ID and account activation must still be verified inside production Aura before the publication workflow is approved.

## Deployed Billing infrastructure (non-public data)
Billing database objects:
- `public.aura_billing_bridge_credentials` — hashed token, active status, RLS on.
- `public.aura_billing_publication_grants` — explicit owner-car-Billing binding, `published=false` by default, RLS on.
- `public.aura_billing_published_documents` — per-document publication, RLS on.
- Public/anon/authenticated permissions revoked; service_role may read from within the protected Edge Function only.
- Edge Function `aura-billing-bridge` uses custom bearer-token authentication, no CORS opt-in, POST only and no-store responses. This is why the Supabase function is deployed with `verify_jwt=false`: it **does** implement its own bearer verification.

Currently all publication tables are empty and client release remains off. The vehicle for JOB-2026-003 has `aura_vehicle_ref='3'` and an audited association but no public commercial records.

## Provisioning (required; never put secrets in GitHub)
1. In Supabase's private SQL editor, generate and rotate the bridge token into its hashed table:
```sql
WITH minted AS (SELECT encode(gen_random_bytes(32), 'hex') AS token),
upserted AS (
  INSERT INTO public.aura_billing_bridge_credentials(name, token_sha256, active)
  SELECT 'primary', encode(digest(token, 'sha256'), 'hex'), true FROM minted
  ON CONFLICT(name) DO UPDATE
     SET token_sha256=excluded.token_sha256, active=true, created_at=now()
  RETURNING name
)
SELECT token FROM minted WHERE (SELECT COUNT(*) FROM upserted)=1;
```
2. Copy the one-time token directly from the private SQL editor result into **Railway → Aura → production → Variables → `AURA_BILLING_BRIDGE_TOKEN`**. Do not paste it into issues, ChatGPT, source files, screenshots or client messages.
3. Confirm `AURA_BILLING_BRIDGE_URL=https://odtctmjhkcphyaozpcup.supabase.co/functions/v1/aura-billing-bridge`. Keep `AURA_BILLING_CLIENT_VIEW_ENABLED=false` while testing.
4. Inspect the Supabase function logs for a signed private request, verify 401 on missing/wrong credentials, 200 with `not_published` for a correctly authenticated but unpublished account, 503 for provider outages.
5. Only **after owner verification and client document publication**, set `AURA_BILLING_CLIENT_VIEW_ENABLED=true`; re-test other owners, drivers, revoked owners and cross-document references.

## Job 2026-003 readiness
- 2013 Mercedes-Benz GLK 350. Aura vehicle #3 and Billing VIN match exactly.
- Billing client and Aura displayed owner identity reviewed.
- Billing vehicle's Aura reference set to `3`, with activity audit entry.
- Billing job remains a draft. Job-linked invoice/receipt/payment count: zero.
- Aura client setup screen shows **incomplete**; no owner access to financial data should be enabled until the account is actually verified.
- No `aura_billing_publication_grants` record has been created. Work history/Treatment Actions are unaffected.

## Test contract
```bash
python -m pytest -q tests/test_billing_client_bridge.py
```
Assertions: forbidden drivers/advisors/other owners; revoked ownership; verified owner works despite missing Billing email; unpublished mapping releases no money; only approved invoices/receipts; partial payments and precise invoice balance; outage and invalid data fail closed; no privileged Supabase key in Aura.

GitHub CI tests are **not** a substitute for an authenticated, real end-to-end production test. Such a test additionally requires an activated owner, a published verified grant, production credential, and a real approved document. Never invent those test conditions or publish a draft just to pass a test.
