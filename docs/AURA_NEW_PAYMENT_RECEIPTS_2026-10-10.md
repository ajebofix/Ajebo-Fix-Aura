# Aura — New Payment Receipts After Invoice Email

Date: 10 October 2026
Scope: Administrator Billing workflow, with Billing as financial source of truth.

## Confirmed reproduction

In the live advisor invoice delivery screen for `JOB-2026-003`, the invoice
was marked **Already submitted**. This is correct for the *same invoice ID*,
but the screen didn't distinguish the already-emailed invoice from new payment
receipts. The client paid ₦200,000 on 9 October and ₦400,000 on 10 October;
both payments were recorded against the ₦665,000 partially paid invoice. Neither
existing payment had an individual receipt document at the time of inspection.
The remaining invoice balance was ₦65,000.

## Root cause

- Email duplicate protection keys on the original document ID. An invoice and
  each receipt are different documents; the protection already supports this.
- The original Billing application requires the administrator to generate a
  separate individual receipt from an existing payment record.
- Aura's delivery page displayed a dead-end **Already submitted** heading with
  generic prose. It had no per-payment receipt-creation status or workflow CTA.
- This is not evidence that Resend or the invoice payment sync failed.

## Fix

1. Extend the **signed, admin-only** billing-document inventory projection to
   return bounded source-backed payment receipt state, for invoices belonging to
   the verified vehicle and linked job. Exclude bank accounts, client finance
   notes and private payment reference details.
2. Validate payment IDs, invoice relationship, positive amounts and receipt
   references in the Python bridge. This projection is unavailable to owners
   and drivers.
3. The vehicle Billing workspace lists unreceipted transfers separately.
4. The already-submitted invoice screen clarifies that only the earlier
   **invoice email** is locked; new receipts are independent. It lists
   payments requiring individual receipts and links to Billing → Payments.
5. Once a receipt is generated in the native Billing app, refresh the vehicle
   workspace to see the new receipt with independent **Review & Publish Receipt**
   and **Review & Send Receipt** actions.
6. Preserve duplicate-email protection for each individual receipt. One
   payment should result in at most one linked individual receipt; the native
   `generate_billing_receipt` Billing function reuses an existing linked
   receipt rather than recording an additional payment.

## Important safety distinction

**Do not** use legacy `convert_billing_invoice_to_receipt` for an already
recorded transfer: that older routine inserts a *new payment* and would
double-count it. Use the Payments page's individual `generate_billing_receipt`
flow against the recorded payment ID instead. A consolidated **final** receipt
is appropriate only once the invoice is fully settled. The ₦65,000 balance
must not be written off or reported paid.

## Manual acceptance checks

- Open invoice delivery: see two payments with no individual receipts.
- Create only the ₦400,000 individual receipt via original Billing app
  **Payments**; confirm the total recorded payments stays ₦600,000.
- Refresh the Aura vehicle workspace: new issued Receipt appears.
- Review and publish the receipt; then and only then explicitly send it.
- Confirm first invoice remains Already submitted and the new receipt is sent
  once under its own unique document ID. Do not test by sending a live email
  without the administrator's approval.

No client email, publication, ledger write or receipt creation is performed by
installing this user-interface fix.
