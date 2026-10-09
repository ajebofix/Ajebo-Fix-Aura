# Gulf One-Pay: Flexible Workforce Capacity and Booking Contract

**Created:** 2026-10-09  
**Status:** approved business input; implementation design, NOT deployed  
**Related architecture:** `AURA_GULF_ONE_PAY_PILOT_OPERATIONS_CONTRACT_2026-10-08.md`  
**Owner of service decisions:** Ajebo Fix operations / authorised advisor

## Current operating reality — confirmed by Ajebo Fix
- Updated capacity statement (9 Oct): Ajebo Fix can complete **6–7 oil changes across a day**, including during reduced staffing, provided actual workshop resources and sequential job windows support the workload. With most qualified technicians present (including suitably supervised interns), it can work on **up to 4 oil changes simultaneously**. With reduced staffing it can work on **2 or 3 simultaneously**. These are observed capabilities, not guaranteed bookable slots.
- Technicians are **not salaried or guaranteed onsite employees**; they work flexibly on commission.
- Technicians may already be serving other workshop jobs or carrying out home/mobile services.
- There are usually technicians at the workshop, but that general observation is **not a time-slot acceptance**.
- Saturday staffing may be unreliable due to personal commitments.
- Ebice holds Gulf oil and filters in Yaba and dispatches them to Ajebo Fix on demand.
- Gulf partner terms, final labour rates, filter supplier, signed contract and operational service windows remain pending.

## Booking principle
**Two independent limits must be enforced:** number of Gulf jobs across the working day, and number of overlapping oil changes in the same service window (including oil changes not sponsored by Gulf). Default pilot planning cap: **6 TOTAL workshop oil changes/day with 2 at once**, including all standard Ajebo Fix oil-change services, not six Gulf-only places. An authorised operator may configure up to **7 total oil changes per day**, and raise simultaneous capacity to **3** with verified reduced-crew resources or **4** with verified full-team resources. Operator may also lower either limit. Do not infer staffing, available work positions, or demand-driven capacity from the upper bound. These numbers must not be advertised as guaranteed availability.

Daily quota counts **completed oil changes, confirmed appointments and unexpired holds from both Gulf and regular Ajebo Fix oil-change work** for that calendar day, without double-counting a single job; cancelled/released or expired holds no longer count. A service request may always enter a waitlist. Only a specific appointment window supported by a qualified technician, a usable workshop bay, an agreed service duration and no overlapping obligations can become a tentative hold. **Final customer confirmation additionally requires the voucher, human technical eligibility, correctly supplied oil/filter received and accepted, customer acknowledgment and persisted slot assignment.**

The pure scheduling evaluator `partnerships/capacity.py` is advisory. Database-level revalidation and atomic booking locks will be essential when implementation reaches the reservation layer. A successful capacity evaluation alone never confirms a customer booking.

## Days and appointment strategy
- **Monday–Friday:** planning maximum **6 Gulf completed/allocated jobs per day**, extendable to **7 by an audited authorised decision** supported by actual availability; concurrent oil-change cap **2 by default**, **3 or 4 only after separately verifying capable assigned technicians, supervision arrangements and work positions**. Actual future availability may be lower than both caps.
- **Saturday:** **manual staffing confirmation required** before any hold. If no qualified technician explicitly accepts and an available bay is confirmed, the day offers zero slots. This rule does not mean all Saturdays are closed.
- **Sunday:** no automated availability; requires an explicit decision to operate plus the same qualified technician and bay evidence.
- **Hours and slot length:** not yet agreed. Do not invent fixed clock-time slots or 30/45-minute service durations. Record an estimated duration by service class after initial real pilot measurements; leave buffer for delays. Make windows configurable.
- **Existing private-client jobs take priority.** Other Ajebo Fix jobs and mobile visits must block overlapping technician/bay windows in the planner.
- **Per-day throughput and parallel capacity are separate:** even with 2 concurrent working positions, 6–7 across the day may be possible using sequential sessions. Daily counts include completed services, confirmed bookings and unexpired holds across Gulf and regular Ajebo Fix oil changes; concurrency counts overlapping Gulf and non-Gulf oil-change reservations and in-progress jobs. Other Ajebo Fix work that uses a technician or work position blocks that resource even if it is not an oil change. Only one worker may be assigned as the primary qualified technician per job; interns are supplemental support, not unsupervised lead technicians.
- Waitlist requests are permitted; do not confirm a waitlist as a booked job or request customers travel before explicit readiness.

## Technician model (lightweight roster, not payroll)
Create restricted `PartnerTechnician` (or references to an existing approved Ajebo OS workforce entity if available) with:
- staff/contractor identity and contact; **contractor**, not full-time employee by default;
- eligible service classes verified by Ajebo Fix (standard / specialist); ability to refuse unfamiliar makes/models; interns may assist only in approved tasks with a named qualified supervising technician and cannot independently approve oil/filters or sign off the service;
- active/inactive status; no promise of daily attendance;
- operator-entered availability **for dated time windows only**, with who contacted worker, time and response provenance (e.g. recorded WhatsApp/phone confirmation);
- other job/mobile service busy windows that cannot overlap Gulf assignments;
- optional ranked backup pool and reassignment history;
- agreed commission **snapshot per assigned Gulf job**, decided before work; amount or percentage (plus basis), actual amount, paid/unpaid status, dates. No commission-rate guessing.

Technicians do not require Aura accounts in the first pilot. Ajebo Fix's authorised operator assigns manually and records acceptance; no external technician access to all customer data, finance records or Ebice commercial terms. Do not build full HR/payroll tooling.

## Operator-only workflow
1. **Request received:** capture voucher, contact and verified-as-available vehicle facts; status is NOT a booking.
2. **Voucher/technical validation:** wait for Ebice verification and authorised human approval of exact grade and filter.
3. **Candidate window check:** operator considers bay capacity, qualified workers, existing work and day cap.
4. **Contact technician:** proposed worker accepts dated slot; Aura records acceptance and possible backup. No response = NOT available.
5. **Tentative reservation:** atomically reserve candidate technician + bay + window for a bounded operator-defined hold while dispatch is arranged. The customer is explicitly told **not yet confirmed**.
6. **Request Yaba dispatch:** Ebice supplies exact approved oil SKU/quantity and filter part number. Capture requested, dispatched, received, inspected and accepted milestones.
7. **Final confirmation:** recheck live technician acceptance and slot conflicts, voucher, product receipt and client response. Issue a confirmed appointment only if every gate passes. Otherwise reschedule or release hold.
8. **Day-of check:** operator reviews technician presence, weather/travel/other jobs if applicable, and product-at-bay. If a technician becomes unavailable, attempt qualified reassignment; if none, inform customer early and offer rescheduling. Preserve an audited change log.
9. **Service/handover:** record exact technician, actual start/finish, oil/filter, evidence, no free diagnosis, customer confirmation.
10. **Commercial closure:** track Ebice labour claim **separately** from technician commission owed/paid. Do not assume commission must await Ebice settlement or vice versa.

## What the operator sees
**Today's capacity:** total oil-change quota 6 across Ajebo Fix and Gulf (operator may authorise 7), oil-change parallel limit 2 (operator may authorise 3/4), counted Gulf completions/confirmed/holds, currently simultaneous Gulf and other oil changes, and candidate qualified technicians/work positions. Show these *separately* so total capacity is not mistaken for free booking slots. This is illustrative only, not a live count.

**Queues:** New enquiries; Need technician response; Waiting for Ebice; Ready for confirmation; Today's arrivals; Unfilled/cancelled/reassignment; Service evidence review; Partner claims; Contractor commissions.

**Each job card:** requested service class, preferred window, technician status, bay status, voucher verification, oil/filter match and dispatch, booking state, next actor, next action, deadline, Ebice claim amount, contractor commission state. No client phone number in an Ebice settlement export unless lawfully necessary.

## Product/disruption rules
- Every accepted technician window is bounded to an explicit date/time; historic familiarity or being *usually present* never counts as acceptance.
- Wrong or late oil/filter causes a blocker, not technician reassignment or fictional product receipt.
- Travel to a home-service job and workshop work cannot be double-booked. Explicit holds count toward available capacity until released/expired.
- Never treat an offered time as confirmed, nor put a client into 'arrived' before confirming on-site status.
- Technician declines or does not answer → request another qualified technician or move the customer to the waitlist.
- Technician cancels after customer confirmation → backup or early rescheduling with a recorded reason; do not conceal the change.
- Multiple workers can process vehicles in parallel only when workshop bay, skills, signed acceptance and materials allow it.
- Cancellation releases resources and creates a voucher-release task to Ebice; never automatically infer voucher invalidation.
- Manual capacity overrides must record operator identity, reason and available resources; overrides cannot waive product/voucher/technical safety gates.

## Commission versus campaign revenue
`PartnerSettlementLine` represents what Ebice owes Ajebo Fix. A separate restricted `PartnerTechnicianCommission` represents what Ajebo Fix owes the specific worker. Their rates, accrual triggers and payment timing are independent, subject to written/recorded internal terms. Record job-level contribution as: settled/contracted labour revenue less directly attributable commission and other variable fulfilment costs; do not treat *revenue* as company profit or expose internal margin to the partner.

## Staging acceptance scenarios
- Planning cap 6 with parallel cap 2, but no technician accepts → **no bookable slot**.
- Technician accepts but is committed to another workshop or mobile job → **conflict**.
- Standard technician offered specialist car → **not eligible**.
- Six Gulf jobs counted (completed + confirmed + unexpired holds), seventh requested → **daily cap** unless authorised increase to seven.
- Seven combined Gulf and non-Gulf oil-change jobs counted, eighth requested → **daily cap** even if concurrent capacity is free.
- Two overlapping oil changes, third requested in same window → **concurrent cap** even when daily bookings are below six.
- Operator verifies three simultaneously serviceable positions/qualified technicians and approves three → third tentative allocation may proceed but fourth in same window fails.
- With fully available team and four safe working positions explicitly approved, four simultaneous jobs may proceed, but fifth overlapping oil change is blocked.
- Six or seven vehicles can be served sequentially under the total daily cap without requiring six or seven technicians present at once. Existing Ajebo Fix oil-change reservations subtract from Gulf's available daily allocation.
- Intern without qualified supervisor cannot be assigned as lead technician or counted as an independent concurrent slot.
- Saturday without recorded staffing check → **manual hold**.
- Saturday with check but no accepted technician → **no slot**.
- Sunday without explicit opening approval → **no slot**.
- Bay unavailable because of existing Mercedes work → **no slot**.
- Oil/filter delayed after tentative hold → customer never receives false booking confirmation.
- Technician cancels after booking → reassignment or reschedule; no hidden double booking.
- Completed job generates contractor commission payable and Ebice labour receivable **as distinct records**, never double-paid.
- Verify that simultaneous writers cannot reserve the same technician or bay (transactional integration test required).
- All times are in `Africa/Lagos` zone and booking checks use the same local day.

## Implementation sequencing
A. Pure appointment-capacity evaluator and tests (no runtime registration).  
B. Partner technician roster/acceptance record and day-cap config; audited, admin-only.  
C. Database-backed time holds, non-Gulf oil-change concurrency count, and conflict locks across all Ajebo Fix and Gulf work.  
D. Link final confirmation to voucher/spec/product receipt controls.  
E. Day-of assignment, reassignment and separately recorded contractor commissions.  
F. Pilot measurement: actual concurrent service volume, completed services/day, timeslots per technician, missed staff commitments, average actual service duration, delays attributable to Yaba supply, customer reschedules, technician earnings, net contribution and displacement of existing work.

**Capacity safety clarification:** 6–7 is total oil-change capability for the workshop, NOT six or seven Gulf-exclusive places on top of normal Ajebo Fix work. The scheduling evaluator takes separate Gulf daily jobs and non-Gulf daily oil-change jobs, adds them for the total daily quota and counts all overlapping oil changes for the concurrent quota. A second workshop repair that occupies a technician or work position must still appear as a time conflict.

**No production activation, database migration, notifications, or live partner/customer access is authorised by this contract.**
