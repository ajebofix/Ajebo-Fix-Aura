# Aura × Ajebo Fix Billing — Owner Read-Only Bridge (Issue #291)

## Implemented first slice
- `GET /cars/<car_id>/billing` is an authenticated **owner-only** Aura route.
- Links from the owner's canonical Vehicle Health Record.
- Displays client-publishable accepted estimates, issued/current invoices,
  recorded amounts paid/outstanding, issued receipts and linked payment events.
- Existing commercial data stays in the **Ajebo Fix Billing** Supabase ledger,
  and all Billing reads occur server-side in Aura.
- No creation, editing, migration, synchronization writes, charging, or approval
  occurs in this slice. No claim of work completion is derived from payment.
- Owners see no billing data by default until both feature activation and
  exact identity linkage pass.

## Canonical systems
- Care: Aura PostgreSQL / `CarOwnership`, `TreatmentPlan`, `TreatmentAction`.
- Commercial: Billing Supabase project `ajebo-fix-official-website`,
  tables `billing_vehicles`, `billing_clients`, `billing_documents`,
  `billing_payments`. Billing is NOT recreated or migrated here.
- Current Billing website, payment, amendment and document-generation
  workflows are unchanged.

## Feature activation
Add the following **server-only** environment variables in Railway:
```text
AURA_BILLING_CLIENT_VIEW_ENABLED=true
AURA_BILLING_SUPABASE_URL=https://<verified-billing-project>.supabase.co
AURA_BILLING_SUPABASE_SERVICE_ROLE_KEY=<secret-from-authorized-operator>
```
Do **not** commit real keys to GitHub or expose them in Jinja templates, JS,
public environment variables or customer links. The server-side integration
uses an existing service-role key because present Supabase Billing RLS rules
are administrator-only. Long-term, replace this broad key with a dedicated,
least-privilege server-to-server API or scoped database role. Rotate credentials
after any accidental exposure.

The flag defaults to off. Production deployment alone does **not** publish
Billing records.

## Manual linking and confidentiality gate
For each vehicle, an authorised advisor must first verify:
1. The currently active Aura owner and vehicle ID.
2. The full, genuine 17-character VIN, matching Billing vehicle VIN exactly.
3. The exact billing client's email address, matching the **verified** active
   Aura owner's email address. Delegated billing accounts are **not** supported
   in V1.
4. Correct Billing customer UUID and vehicle UUID.

Only then may an authorised operator set
`billing_vehicles.aura_vehicle_ref` to Aura's decimal `cars.id`, with an
audit trail. Do not bulk-link by names, phone numbers, plate numbers, or
repeated `JOB-2026-003` strings.

If linkage is missing, duplicated, wrong, or incomplete, the portal displays
a neutral "not connected" state with **no financial details**. Ownership
transfers/revocations immediately remove route access. Drivers and advisors
cannot use the owner's financial projection.

## Publication rules
This read-only slice exposes:
- estimates in accepted/issued/sent state;
- invoices in issued/sent/overdue/partially_paid/paid state;
- receipts in issued state;
- verified payments linked to a visible invoice.

It hides drafts, superseded documents, revoked shared documents and internal
fields such as vendor prices, payment bank/account details, tax internals,
free-text administrative notes, AI suggestions and attachments. Portal access
does not imply disclosure of raw WhatsApp exports, privileged advisor notes or
other clients' records. PDFs/attachments and client electronic acceptance are
deliberately out of scope until secure download/approval routes are implemented.

## Tests / release gates
Run:
```bash
python -m pytest -q tests/test_billing_client_bridge.py
```
Before enabling:
1. Confirm no published data appears in an unlinked account.
2. Verify a controlled real vehicle with matching VIN and owner email.
3. Confirm invoice partial paid/outstanding amounts against Billing ledger.
4. Test a different owner, a driver, an ownership transfer and a disabled flag.
5. Confirm no unexpected Billing or care database writes in read-only flows.
6. Record the linkage and publication decision under #291 and pilot #272.
7. Perform privacy/security review before exposing live financial data.

## Next increments
A. Adviser-run explicit linking/approval workflow with immutable provenance
   instead of manually setting a reference.
B. Document PDFs via time-bounded Aura authorisation and server-side streaming;
   avoid reusing public share tokens.
C. Versioned client estimate/change-order acceptance; never infer approval
   from invoice status alone.
D. Rina can explain only the same owner-approved commercial projection, after
   separate role-scoped retrieval and prompt-injection/privacy testing.
E. Once migrated with parity/rollback checks, phase out the separate UI.

## Known limitations
- Existing billing vehicles were not linked at start of implementation.
- First release requires owner billing email equality and verified VIN; other
  legitimate payer / delegated-ownership configurations remain unsupported.
- Monetary amounts are per document and are **not** consolidated across
  currencies. Payment entries are not an independent proof of treatment
  completion.
- No PDFs, attachments, online payment capture, or new invoice creation yet.
