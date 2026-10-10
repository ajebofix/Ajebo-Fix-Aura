# Aura and Billing policy / settlement classification

Date: 10 October 2026

## Why
The commercial document workflow must distinguish an accepted estimate, one continuing invoice, each verified partial payment, an individual payment receipt and final settlement confirmation. It must link clearly to the official Ajebo Fix website's service policies without silently modifying historical agreements.

## Verified baseline
- Native Billing is the authoritative payment ledger, original invoice and receipt source.
- `generate_billing_receipt(p_invoice_id, 'payment', p_payment_id, ...)` issues a payment-backed receipt without inserting a new payment and reuses a previous linked receipt. Always choose this newer function, NOT legacy `convert_billing_invoice_to_receipt` (which inserts a payment).
- `generate_billing_receipt(..., 'consolidated', ...)` checks for full settlement before issuing a final receipt and links all original payments, not a new payment.
- Existing Aura advisor controls separately approve publication and email; duplicate Resend submissions for the same document stay blocked.
- The website already had /terms, /privacy-policy and /disclaimer; a new /service-terms page has been prepared by official-website PR #101.

## Implementation in Aura
- Signed Billing bridge exposes `receipt_kind` from source for each receipt and independently checks the linked original invoice's amount paid and remaining balance.
- For a consolidated receipt, the bridge rejects a stale/partially-paid invoice or a receipt whose total differs from the invoice total. The safe client-side projection rechecks that a final receipt is not attached to a positive invoice balance.
- Owner/advisor views explain: estimate vs invoice; invoice balance and paid amounts; individual payment receipt with the **live source invoice balance**; or verified final settlement confirmation. Original document-specific terms remain visible.
- Policy URLs are deliberately **not** included in this pilot release. Source-specific accepted job terms remain visible, and no proposed website clause is imposed.
- Administrator register identifies individual receipts and consolidated final settlement separately, without creating any new financial record.

## Not silently automated
- Individual receipt creation remains an explicitly authorised operation inside the original Billing Payments screen at this rollout. The current Aura advisor workspace flags unreceipted, already-recorded payments and offers a manual path to create the corresponding document.
- Aura does not autonomously issue receipts, make financial writes, publish documents, email customers or mark jobs completed. Preparing an unreceipted-payment item for advisor action is automatic and read-only. A future server-side preparation RPC requires separate idempotency, provenance and operator-approval review.
- The present feature does not generate or send documents for the live partially-paid customer example.

## Website and Billing templates follow-up
- In a future, independent, expressly approved policy release (after /service-terms is live), add the short website link to **new** native Billing documents only. No current Billing settings or accepted documents change in this release.
- Existing website Terms remain solely website-use conditions. The new Service Terms should receive Nigeria-qualified legal review, particularly on refunds, client-supplied parts, warranties, cancellation, vehicle release and consumer-protection law.
- Until Cloudflare publishes the service terms route, do not send newly generated documents relying on the non-live reference.

## Regression goals
1. A receipt with source invoice balance ₦65,000 is displayed/sent as a **partial payment**, not final settlement.
2. A consolidated receipt requires linked invoice balance precisely zero and a matching settled invoice amount.
3. Invoice status and payment totals are unchanged by preview/send logic.
4. Owner permissions, separate publication/email approval and duplicate submission protection are unchanged.
5. No current previews or messages link to the unpublished website page; all source-specific existing terms are retained.

## Release split — 10 October 2026 owner-authorised pilot deployment

To ship financial improvements ahead of the unpublished website policy, PR #315 deliberately removes *all* client-preview, advisor-preview and outgoing Resend links to `https://ajebofix.com/service-terms`. Existing per-job terms remain visible; nothing is retroactively imposed. The proposed future policy-default SQL is excluded entirely from this release; no Billing settings update is applied. The website PR #102 and Aura Workmanship PR #316 remain independent and held.

Only the separate account statement email, payment receipt classification and final settlement checks are in scope. No payment, receipt, email, legal acceptance or change in financial amount is created by deployment. Preserve the previous production gateway code/versions for rollback. Perform post-release smoke checks on health, Billing inventory and a real partially-paid invoice preview, without sending any emails or generating payments.
