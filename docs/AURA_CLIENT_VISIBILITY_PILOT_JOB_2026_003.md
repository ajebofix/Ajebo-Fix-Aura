# Aura Client Visibility — Care, Repair Journey and Commercial Records

**Decision for JOB-2026-003 / 2013 GLK pilot, 8 Oct 2026**

## One owner account, independently authorised care and commercial records
- Aura is authoritative for the vehicle identity, **active owner**, care plan/Treatment Action state and signed-in client visibility.
- Ajebo Fix Billing is authoritative for invoices, receipts, payments, commercial job membership and amendments.
- A correct VIN or Billing mapping does **not** grant publication permission. Access requires independently verified owner/car + scope-specific publication gates.

## Client may see
| Surface | Allowed information | Authority / release gate |
| --- | --- | --- |
| Vehicle | Make, model, VIN, recorded mileage and active ownership | Authenticated owner |
| Health / monitoring | Reviewed, non-diagnostic observations, confirmed concerns and supported next steps | Existing owner-scoped safety filters |
| Treatment | Professional plan title and client summary; actual proposal, authorization, scheduled, started, monitoring or completed state | Existing TreatmentPlan/TreatmentAction client visibility and owner authorization |
| Repair Journey | Short advisor-written update, accurately labeled milestone and record date | Explicit per-source publication to that owner's account; can revoke |
| Reviewed evidence | Client-approved review, safe captions/links and source history without private storage keys | Existing evidence review/ownership gate |
| Historical Billing | Issued, nonrevoked, current-version invoices/receipts approved for that same vehicle and client, with ledger amounts/partial payments | Separate owner + document publication grants in Billing |
| Current repair Billing | Only real, separately approved, job-linked documents | Never infer from historical vehicle invoices |
| Rina | Explanations grounded in records already authorised to the client | No access to raw advisor repair progress or unapproved Billing facts |

## Client must **not** see
- Raw WhatsApp exports, unreviewed advisor notes, raw structured repair-progress JSON/tags, internal discussions, vendor negotiations/margins, free-text private assessments, privileged evidence or internal prompts;
- Draft, revoked or superseded invoices or estimates, undisclosed change orders, other owners' payment records;
- A fabricated repair state, client authorization, completed outcome, verified diagnosis or payment inferred from a milestone.

## Pilot progress finding
On 8 Oct a manual progress note recorded: **“The parts have been delivered and dismantling has commenced.”** The correct milestone is *Dismantling*; receipt of parts is **not** vehicle delivery. The older keyword-based classifier also tagged `delivered`, which is corrected in PR #294 without rewriting the original record. An acceptable reviewed owner summary is:
> Replacement parts have arrived, and dismantling has commenced.

This is not automatically released. The advisor must use **Review and publish to client** in Repair Progress. The approved summary gets a separate durable record with a source-note digest and account binding. Revocation leaves the internal record unchanged.

## Billing pilot approvals
Owner approval on 8 Oct authorised these existing historical GLK documents (not collision-job records):
- `AJF-INV-2026-0809-001` — partially paid (₦500,000 total, ₦490,000 recorded as paid);
- `AJF-RCP-2026-0725-001` — issued receipt;
- `AJF-RCP-2026-0810-001` — issued receipt.

The revoked `AJF-RCP-2026-0805-001`, drafts and all JOB-2026-003 documents are excluded. The current job remains a draft with zero directly linked documents.

## Release validation
- Owner #5 account active and email verified, Aura vehicle #3 active owner, VIN matched independently.
- Owner and three document publication grants approved in existing Billing database.
- Require tests for other owner, driver, admin, email unverified, ownership transfer, unapproved progress and revoked/superseded financial records.
- After passing CI and deploying owner UI, enable `AURA_BILLING_CLIENT_VIEW_ENABLED=true`; observe client login and no cross-owner disclosure.
- No impersonated real-client session is acceptable as proof of an end-to-end user test.
