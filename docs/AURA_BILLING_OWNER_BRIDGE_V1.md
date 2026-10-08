# Aura × Ajebo Fix Billing — signed, published-only owner integration

## Systems and trust
- Aura (Railway PostgreSQL/Flask) owns verified authentication, active vehicle ownership and treatment/care progress.
- Existing Ajebo Fix Billing (Supabase) remains canonical for jobs, estimates, invoices, payments and receipts.
- `aura-billing-bridge` Supabase Edge Function is the only cross-system commercial read API. It **does not** return Billing contact details, administrative notes, vendor details or private attachments.
- Existing Billing RLS does not grant the browser or ordinary authenticated users access to the private bridge/grant tables.
- A Billing client may lack an email. Aura's verified owner identity uses **two numeric IDs (owner and car), an exact 17-character VIN and an explicitly approved owner publication binding**, not matching emails or client names.

## No cross-provider secret transfer
- A fresh, purpose-scoped Ed25519 signing key is derived **inside production Aura** from its already-installed Flask SECRET_KEY via HMAC-SHA256 with a stable, distinct context label.
- Aura exposes only the raw Ed25519 **public key** at `/.well-known/aura-billing-public-key`. The private key, secret seed, and signed financial requests are never sent to browsers.
- Each server-to-server POST contains canonical JSON with owner ID, car ID and VIN, signed together with a Unix timestamp and random 128-bit nonce. Requests expire after 90 seconds.
- The Billing Edge Function retrieves Aura's public key from the pinned HTTPS Railway production domain, validates the key ID, Ed25519 signature, timestamp, payload and single-use nonce.
- A private, RLS-protected `aura_billing_bridge_nonces` table rejects signature replay. No persistent bearer token or Supabase service-role key is configured on Railway.
- The Billing Edge Function reads via its own environment-held service role key. It has no write operations on invoices, payments, jobs or vehicle history; nonce insert is the only runtime write.
- The former bearer-hash credential table is legacy and should be deactivated; it is not consulted by the signature verifier.

## Explicit publication conditions
A Billing vehicle link, including the verified JOB-2026-003 association, is **not** financial publication. Real documents appear only if:
1. Aura route verifies logged-in user, verified email, `role=user`, and their active CarOwnership for this vehicle;
2. an administrator verifies that client identity and enters an exact Aura owner ID/car ID/Billing vehicle UUID/Billing client UUID binding into `aura_billing_publication_grants`, with `published=true` and no revocation;
3. Billing vehicle has matching explicit `aura_vehicle_ref`, client ID and full VIN;
4. an advisor publishes each individual client-safe, current commercial document in `aura_billing_published_documents`;
5. the Billing document itself is not draft, superseded or revoked and belongs to that Billing vehicle/client;
6. invoices and linked payments reflect the actual Billing ledger and are never evidence of repair completion.

A paused/unverified account, unapproved document, revoked owner, missing grant, mismatched VIN or provider outage must fail closed.

## Safe synthetic authentication test
The optional public GET `/internal/health/billing-bridge` is disabled by default behind `AURA_BILLING_SMOKE_TEST_ENABLED=false`.
During a supervised one-time test only, set `AURA_BILLING_SMOKE_TEST_ENABLED=true`. The endpoint makes a signed request for a **hardcoded nonexistent** owner and vehicle; it returns only `{"handshake":"authenticated","publication":"none"}` when the Edge Function validates the signature and reports `not_published`. It never accepts an arbitrary ID or displays any records. Turn this flag back off immediately after testing.

## Production settings (Railway service)
```text
AURA_BILLING_BRIDGE_URL=https://odtctmjhkcphyaozpcup.supabase.co/functions/v1/aura-billing-bridge
AURA_BILLING_CLIENT_VIEW_ENABLED=false
AURA_BILLING_SMOKE_TEST_ENABLED=false
```
No `AURA_BILLING_BRIDGE_TOKEN` or `AURA_BILLING_SUPABASE_SERVICE_ROLE_KEY` is needed.

## Pilot: JOB-2026-003
- 2013 Mercedes-Benz GLK 350, full VIN matched to Aura car #3.
- Client displayed in Aura matches the Billing job's client; Billing vehicle was internally linked to Aura car #3 and the association was audited.
- Billing job is still draft with no job-linked invoices or payments.
- Aura account setup remains incomplete until separately activated. Do not manufacture an invoice, mark work completed, or publish client data to satisfy a test.
- Owner publication and document publication tables start empty; no financial records are shared.
- Future scope: advisor identity/grant UI, versioned commercial acceptance, PDF streaming, Rina explanations of published only commercial records.

## Tests
Run `pytest -q tests/test_billing_client_bridge.py`, test email-absent owner, cross-owner denial, driver/advisor denial, verified Aura email, revoked ownership, false/tampered Ed25519 signature, different nonce per request, gateway outage and zero grants. Green CI is necessary but not a substitute for a live, authorised client end-to-end test.
