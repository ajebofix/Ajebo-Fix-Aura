# Aura invoice discount visibility fix — 10 October 2026

## Original report
An owner-authorised Aura invoice omitted the documented discount adjustment from its client-facing display. The itemised service lines added to the original amount, but the preview only presented the final total, leaving the concession invisible.

## Billing source of truth (verified live before release)
- Job: `JOB-2026-003`
- Invoice: `AJF-INV-2026-1009-001`, status `partially_paid`
- Gross services subtotal: ₦685,000
- Original Billing adjustment: `Discount on Service charge`, fixed ₦20,000 reduction
- Current invoice total: ₦665,000
- Two payments recorded: ₦200,000 and ₦400,000, total ₦600,000
- Remaining amount due: ₦65,000

The older accepted estimate remains ₦685,000; its history is not retroactively rewritten by the invoice discount. The client's requested ₦600,000 settlement price is **not** an agreed final invoice amount.

## Cause
The signed Supabase `aura-billing-bridge` projected only `kind:"line"` entries from the native Billing `sections.rows`; it dropped `kind:"adjustment"` rows. The Python bridge and the Jinja financial-document template consequently had no discount data to display.

## Remedy
1. The signed source gateway additionally reads gross subtotal, adjustment total and VAT and projects only genuine negative `discount` adjustment rows with bounded label and amount.
2. Validate that discount rows reconcile with Billing's authoritative adjustment and final total before showing a breakdown.
3. Client bridge independently validates nonnegative, bounded discount data and strips internal source `note` fields.
4. Owner-authorised and advisor-preview document templates show **Services subtotal before discount**, **Discount on Service charge —₦20,000**, **Total after discount**, and the current recorded-payment / outstanding lines.
5. If no valid discount exists, display the prior original Total layout. No adjustment is invented.
6. No changes to amounts, invoices, payment records, publication grants, terms, notification/email state or owner permissions.

## Regression and rollout
- Source-route test for ₦685,000 gross, ₦20,000 discount, ₦665,000 final, ₦600,000 paid and ₦65,000 outstanding.
- Assert internal bargaining note never reaches rendered owner HTML.
- Invalid/excessive discount projection must fail closed.
- CI for Billing owner bridge and Aura Security, then deploy signed Supabase Edge gateway before merging the backward-compatible Aura frontend.
- Independently check production `/healthz` commit and the authenticated advisor invoice preview. **No email or publication action is triggered by the fix.**
