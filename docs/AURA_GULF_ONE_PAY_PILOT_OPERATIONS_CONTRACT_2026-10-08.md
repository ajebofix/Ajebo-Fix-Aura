# Aura — Gulf One-Pay Pilot Operations Contract
**Date:** 2026-10-08  
**State:** design proposal; not deployed; partner agreement/pricing not signed  
**Owner:** Ajebo Fix Operations  
**Pilot:** Ebice Hoses Limited / Gulf One-Pay — Surulere  
**Architecture placement:** Domain E (Advisor Operations), F (Ajebo OS), G (Commercial/Enterprise), with existing Vehicle Intelligence, Evidence and VehicleEvent services.

## Updated workshop operating model (9 October 2026)
Ajebo Fix can potentially serve **4+ Gulf vehicles/day** but has no full-time technicians; technicians are flexible commission-paid contractors and may have other workshop or home-service commitments. General presence at the workshop does NOT constitute an accepted shift or an allocated Gulf slot. Saturdays require explicit staffing confirmation; Sundays require explicit opening approval. Start with a **4 per-day allocation cap including active holds**, then permit staff-approved adjustments based on real resources. No hard-coded appointment hours or service durations are assumed.

**Detailed source of truth:** [Gulf flexible workforce booking contract](AURA_GULF_FLEXIBLE_WORKFORCE_BOOKING_CONTRACT_2026-10-09.md). Its capacity policy is additive to all voucher, spec/product, evidence and settlement safety rules below. Internal contractor commission and Ebice sponsor settlement must remain separate records.

## Purpose and decisive rules
Prepare Aura to run the Gulf pilot without WhatsApp becoming the system of record. This document is an additive design contract, not authorisation to go live. The current Ebice PDF is a proposal, not a signed SLA. Meeting of 2026-10-08 supersedes the PDF's consignment assumption: **oil and filters stay in Yaba with Ebice and are dispatched per validated booking to Ajebo Fix**. Ajebo Fix is sole proposed Surulere campaign service partner, while Ebice works with others in Ikeja/Lekki. Oil-filter supplier/specification brief, commercial tariffs, signed agreement, authorisation and partner identity evidence are pending.

Do not model this as a new parallel CRM, full inventory ERP or AI diagnostic service. Build a partner-generic fulfilment domain with a Gulf campaign configuration. Customer does not need an Aura login to request campaign service. No public partner portal, auto-redeem, automatic diagnosis, workshop-level stock custody, or direct customer charging is approved for V0.

## Primary operational goals
- One searchable job per voucher/service request with exact next action, accountable actor and timestamp.
- No service or customer appointment confirmation until voucher, VIN/vehicle-specific oil/filter compatibility, product readiness, and bay/technician capacity pass the appropriate gates.
- Capture inspection observations as observations, not diagnoses; never auto-publish into Rina or treatment records.
- Keep sponsor payables and any independently authorised Ajebo Fix work separate; never double invoice the customer for campaign labour.
- Preserve vehicle service history only after completion and provenance review; no guessed VIN, ownership, mileage or maintenance interval.
- Resolve common blockers without Stephen personally coordinating each via scattered chats.

## Operator screens — V0
**/admin/partners/gulf/dashboard** (admin only; hidden behind default-off flag until release)
- Top metrics: requests today; waiting on Ebice; awaiting technical approval; product ready; appointments today; awaiting documentation; claims due; unsettled amount.
- 5 action queues: New requests, Awaiting Ebice, Ready to schedule, In service, Needs settlement.
- Columns: internal request ID, voucher masked, plate masked, brand/model, requested appointment, partner contact/status, next action, owner, due-by, age.
- Job detail timeline shows manually entered partner confirmations, product receipt, technical approvals, work evidence, handover and settlement.
- Controls: request missing details, record voucher verification, approve/reject technical fitment, request dispatch, confirm received items, schedule, start service, record checklist, mark handover, submit claim, reconcile settlement.
- Every blocked card shows a reason and responsible party; no misleading automatic claims or push alerts until enabled.

## Independent state contracts (not one giant status field)
1. Voucher: unverified / verifying / verified / rejected / expired / reserved / redeemed. One voucher can produce at most one active service; redemption is transactional with external-partner acknowledgement.
2. Technical eligibility: pending / needs_information / approved / rejected; requires exact oil SKU/approval and filter part-number verification for this vehicle by an authorised human.
3. Products: not_requested / requested / dispatched / received / discrepancy / accepted. Yaba is the custody origin. No 'in Aura stock' projection.
4. Appointment: request_received / tentative / confirmed / checked_in / cancelled / no_show / completed. A tentative time is not a guaranteed booking.
5. Service: not_started / in_progress / paused / completed / handover_confirmed. Completed requires real technician evidence.
6. Partner settlement: not_eligible / ready / submitted / disputed / partially_paid / paid. Never infer paid from submitted, and support immutable payment/adjustment entries.
Compute top-level queue and blocker from these states, not from a second independently mutable status.

### Hard guards
- Any unsigned pilot agreement keeps partner campaign INACTIVE and public booking disabled.
- External voucher verification must record verification channel, verifier, time and reference; no guessing validity from code format alone.
- Lock duplicate voucher/service claims and flag same plate within 30 days for operator review (not silent irreversible rejection).
- 'Confirmed' appointment requires voucher verified, technical approval, accepted oil/filter available at Ajebo Fix, reserved bay capacity, a qualified technician's accepted dated slot, approved contractor compensation terms and explicit customer confirmation; exceptions require an audited supervisor override and customer warning.
- Wrong oil, wrong filter, insufficient litres, product seal anomalies, unverified spec, existing mechanical danger or workshop overload must pause/reject the job rather than improvise.
- Only after actual service and handover confirmation can a labour-fee claim become eligible. Evidence gaps create a review/dispute queue rather than silently erasing labour.
- External Ebice confirmation and paid amounts must be explicit, not assumed from promised weekly settlement.
- Vehicle ownership/access must be independently verified before connecting a campaign record to an existing Aura Car, CarOwnership or client-visible timeline.
- Existing User, BookingIntent and Car objects MUST NOT be manufactured from an unverified plate or fictitious VIN.

## Data contracts (proposed new domain tables; migration design required)
**CommercialPartner:** verified contracting identity, business documentation checks, approved contacts, current activation/hold.
**PartnerCampaign:** partner ID, terms-version, territory, pilot duration, eligible packs, daily cap, payment cadence, agreed rate card when signed, feature availability.
**PartnerServiceRequest:** immutable internal ID; voucher keyed digest plus encrypted/display-restricted value; source channel; vehicle snapshot (plate, make/model/year, VIN if known, preferred date, contact with explicit lawful processing); optional verified car_id and user_id; owner/operator; timestamps and blocking reasons.
**PartnerVerification:** voucher communication/ref, verifier, decision, evidence reference, reviewed time; separate technical eligibility decision with specific vehicle manual/approval source, oil SKU/grade, filter maker/part number, quantities, adviser identity.
**PartnerDispatch:** request ID, Yaba origin, oil SKU, filter identifier, expected quantities, dispatch requested/sent/received times, courier proof, lot/batch/seal condition, discrepancy reason.
**PartnerServiceFulfilment:** technician, oil lot/qty, filter exact code, arrival/service timing, actual odometer + evidence, ten-point observations, drain/refill/level/leak/reset outcomes, advisor release, customer confirmation and service-record link.
**PartnerSettlementLine:** service ID unique, contractual rate + adjustment history, weekly statement identifier, invoice or billing-platform reference, submitted/disputed/paid dates and allocated amounts; do not reproduce accounting ledgers if Ajebo Fix billing system is authority.
**PartnerOperationEvent:** append-only business audit events (actor/source/timestamp/decision, minimal metadata). DO NOT create a second car progression envelope: verified vehicle service facts append to existing VehicleEvent only when car linkage and provenance are established.
**PartnerTechnician / PartnerTechnicianCommission (proposed):** limited staff/contractor identity, service qualifications, date-bounded accepted availability, existing job conflicts, assignment acceptance, agreed per-job commission snapshot and separately audited worker payment; do not confuse with sponsor receivable or treat contractor as a guaranteed employee.
**PartnerCapacityHold (proposed):** tentative time window, bounded expiry, technician and bay assignment with atomic conflict protection, operator decision and release/reschedule reason; active holds count against daily limits.
**PartnerConsent:** narrow consent/notice record for fulfilment and separately explicit opt-in for Ajebo Fix marketing/Aura invitation. Opt-in is never inferred from voucher redemption.

Follow the existing schema migration conventions and security controls. Decide exact normalization, encryption and FK/nullability after inspection; these are design-level entities, not migrations yet.

## Source of truth / permissions
- Ebice: voucher purchase, voucher status, stock and product dispatch and its customer campaign prices.
- Ajebo Fix/Aura: eligibility acceptance, on-site service work, service evidence, worker allocation, submitted labour claim.
- Ajebo Fix Billing: published invoicing/receipt/payment truth where connected; reconciliation must be explicit and idempotent.
- Existing Car/CarOwnership: only verified internal vehicle-owner identity. Don't force public customer signup.
- Aura VehicleEvent: canonical longitudinal events from verified service, not booking intent or unverified claim.
- Photos: use Aura's existing private evidence store; do not put raw image data in audit text or public URL.
- Staff: current authenticated admin/advisor access until future roles are separated; Ebice has no Aura admin access, future partner view will expose only approved minimal fields.
- Rina may later summarise approved operational facts; cannot approve oil specification, mark work complete, diagnose, redeem voucher, or release invoice.

## V0 field intake
Campaign voucher code; name and phone (minimal, protected); plate; vehicle make/model/year; VIN if available; engine/fuel variant if relevant; known manufacturer oil requirement; preferred service date; photos/references optional at intake; supplier verification method and note. Staff contact and customer contact must not be casually exposed to external partner.

## 10-point service checklist and evidence
Keep original campaign scope: fluids, tyres, wipers, lights, belts, battery terminals and visible leaks; configure exact ten-point list with partner rather than inventing diagnoses. Include verified oil SKU/lot, sealed product photo, old/new filter photo, odometer/dashboard photo, actual litres, leak/level checks, reminder reset (or documented not-applicable), next-service sticker only using documented interval source, client handover proof and internal technician sign-off.
All observations labelled as *observed* and routed for an separately authorised Ajebo Fix assessment if follow-up is desired.

## Settlement workflow
Ebice funds the campaign and customer pays Ebice, not Ajebo Fix. One completed, reviewed service creates at most one eligible labour-fee settlement line using signed car/SUV/exception rate rule. Prepare Monday statement; reconcile against Ebice response; monitor payment within agreed 3-working-day window if that term survives negotiation. Partial payments, withholdings and disputes are represented without changing what service physically happened. Extra oil litre/customer sponsor responsibility stays under the signed campaign rules; any separately authorised Ajebo Fix diagnosis/repairs use their independent billing.

## Pilot gating, rollout and fail-safe
**Before opening:** verified counterparty and distributor authority; signed agreement; rate card; filter-source approval; product/SKU/VIN matching protocol; dispatch lead-time agreement; service scope; operator training; privacy notice; tested staging feature.
**Phase 0 (now):** review this contract; clarify open commercial terms; create implementation backlog; mock admin dashboard.
**Phase 1:** admin-only manually entered voucher intake, verification, vehicle eligibility and product dispatch status; one complete job page and searchable next action.
**Phase 2:** service checklist, photo evidence, technician sign-off, vehicle event linkage with owner permissions, and weekly settlement export.
**Phase 3:** optional secure Ebice imports/API, notification templates, constrained partner dashboard, aggregate insights and client opt-in invitation. No dependency on Phase 3 for pilot launch.

For operational safety, initially cap appointment concurrency to available real workshop capacity, with configurable cap rather than assuming 15/week can be processed. Run 3–5 synthetic customer scenarios end-to-end in staging before live go/no-go.

## Acceptance scenarios
1. Unverified voucher cannot start service or become a claim.
2. Same voucher submitted twice produces conflict/review, never two paid jobs.
3. Second booking for same plate inside 30 days is flagged, not silently overwritten.
4. Wrong oil grade or filter = no technical approval.
5. Oil dispatch requested but not received = booking tentative, clear Ebice blocker.
6. Incorrect delivered filter = discrepancy, no 'ready to serve'.
7. No available bay = no confirmed appointment.
8. Unregistered Gulf customer does not require User, Car or fake VIN.
9. Customer data never leaked through public endpoints, Ebice reports, or Rina.
10. Completed service doesn't auto-publish owner-linked events until identity/ownership confirmed.
11. Service observations do not become diagnoses.
12. Missing proof goes to documentation exception; never invent completion.
13. One service yields at most one partner settlement claim even after retry.
14. Statement submitted != paid; support partial payment and disputed adjustments.
15. Cancelled request can release unused voucher reservation with Ebice confirmation.
16. Excluded vehicles and unsupplied quantities go to supervisor/partner exception, not customer surprise charges.
17. Client health / recall / maintenance projections cannot guess future date or mileage from sticker preference.
18. Disabled feature produces no visible pilot routes for clients/partners and no background jobs.

## Decision log (pending, not assumed)
- Signed rates: car; SUV; specialist exclusion or rate exception.
- Whether 90-day pilot, target 15/week, 48h booking turnaround, Monday/Wednesday settlement, and 30-day exit remain agreed.
- Voucher verification endpoint vs manual WhatsApp/email and who can release voucher after cancellation.
- Exact custody transfer at Yaba dispatch vs on-site receipt; who pays for delay/redelivery.
- Oil-filter distributor, warranty, fitment catalogue, traceability and returns.
- VIN requirements for intake vs service completion, and age/model exclusions.
- Whether campaign customers get 10-point check without free diagnostics; exact checklist to sign.
- Surulere exclusive designation geography and Ajebo Fix's freedom to stock/recommend other oil brands.
- Customer privacy notice, Ebice reporting fields, separate marketing consent and optional Aura invitation.
- Current corporate status/distributor-authority due diligence and pilot launch approval.

## Non-goals for this first implementation
No auto-generating Rina advice, no prediction, no VIN guessing, no fake historical data, no integration with Ebice credentials until formally authorised, no consumer coupon sales inside Aura, no autonomous WhatsApp outreach, no production schema migration or rollout from this document PR.

## Architecture notes from as-built repository
- Aura is Flask/Jinja with SQLAlchemy/Alembic/PostgreSQL, Railway production and administrator-owned operations.
- Existing BookingIntent requires user_id and car_id: not suitable as first-touch voucher booking for someone outside Aura.
- Existing Car requires non-null unique VIN: never create placeholder VIN to satisfy schema.
- Current VehicleEvent remains canonical append-only vehicle chronology; keep separate operational records until facts are verified.
- Evidence storage/private access and maintenance-history normalization already exist and must be reused, not reinvented.
