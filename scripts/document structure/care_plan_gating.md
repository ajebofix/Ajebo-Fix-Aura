AURA V2 — CARE PLAN GATING

Ajebo Fix Automotive Health Platform

This is one of the most important monetization systems in Aura.

But the goal is NOT:

“locking features behind payment”

The goal is:

structuring responsibility, monitoring, and operational access

That distinction changes the entire emotional feel of the platform.

Aura should NEVER feel:
	•	cheap
	•	aggressive
	•	salesy
	•	manipulative

It should feel:
	•	controlled
	•	clinical
	•	professional
	•	operationally intentional

⸻

1. CORE PHILOSOPHY

Traditional upsell model:

Pay more → unlock premium features

Aura model:

Vehicles under active care receive elevated operational access

Huge difference psychologically.

One feels:
	•	commercial

The other feels:
	•	protective

⸻

2. PRIMARY OBJECTIVE

Create:
	•	monetization
	•	exclusivity
	•	continuity
	•	recurring revenue

WITHOUT:
	•	“upgrade now” energy
	•	discount psychology
	•	pressure tactics

⸻

3. CARE PLAN STRUCTURE

Core Plans

Plan                                    Purpose

Standard Access                     Base platform access
Active Monitoring                   Ongoing oversight
Preventive Coverage                 Structured continuity
Priority Access                     Elevated operational response


Notice:
No:
	•	silver
	•	gold
	•	platinum
	•	premium plus nonsense

Aura language should feel institutional.

⸻

4. WHAT GETS GATED

Gate Operational Privileges

NOT:
	•	basic safety
	•	essential awareness

Aura should NEVER punish users for not paying.

That’s critical.

⸻

5. STANDARD ACCESS

Included

Access                          Allowed

Vehicle dashboard               ✅
Basic chat with Rina            ✅
Health overview                 ✅
Treatment visibility            ✅
Consultation requests           ✅


This creates trust.

⸻

6. ACTIVE MONITORING

Additional Access

Feature                             Purpose

Elevated observation            ongoing tracking
Monitoring continuity           progression awareness
Advisor follow-up               continuity
Enhanced timeline tracking      historical awareness


This should feel:

reassuring

NOT:

premium upsell

⸻

7. PREVENTIVE COVERAGE

Additional Access

Feature                                         Purpose

Scheduled review continuity                 preventive oversight
Priority monitoring                         elevated observation
Reduced operational delays                  smoother workflow
Long-term tracking                          continuity


This becomes:

automotive healthcare

not:

repairs

⸻

8. PRIORITY ACCESS

Additional Access

Feature                                 Purpose

Emergency review request            escalation handling
Accelerated scheduling              operational priority
Fast-track review queue             urgency management
Advisor continuity                  relationship trust


This should feel:

reserved

not:

marketed

⸻

9. GATING PHILOSOPHY

Aura should gate:
	•	speed
	•	continuity
	•	oversight
	•	monitoring depth
	•	escalation priority

NOT:
	•	safety awareness
	•	basic guidance
	•	essential care visibility

Very important distinction.

⸻

10. UI PHILOSOPHY

Locked areas should feel:

professionally restricted


NOT:

sales-blocked


⸻

11. GOOD LOCKED COPY

✅

Priority review access is reserved for vehicles currently under active care coverage.


⸻

12. BAD LOCKED COPY

❌

Upgrade now to unlock premium support!

Never.

Aura is not SaaS marketing theater.

⸻

13. DATABASE MODEL

class CarePlan(db.Model):
    id

    user_id
    vehicle_id

    plan_type

    active_monitoring
    preventive_coverage
    priority_access

    status

    started_at
    expires_at


⸻

14. ACCESS CONTROL LOGIC

Example

if not care_plan.priority_access:
    deny_priority_route()

Simple.
Controlled.
Predictable.

⸻

15. FEATURE FLAGS

Recommended System

care_plan.can_access_priority
care_plan.can_request_emergency_review
care_plan.can_use_monitoring

This prevents:
	•	messy conditionals later
	•	scattered monetization logic
	•	entitlement chaos

⸻

16. ROUTE GATING

Examples

/priority/*
/care/monitoring
/care/preventive

Middleware should enforce access cleanly.

⸻

17. RINA’S ROLE

Rina may:
	•	reference care limitations
	•	suggest monitoring value
	•	reinforce continuity importance

Rina may NOT:
	•	aggressively upsell
	•	pressure users
	•	create fear-based conversion

Good:

✅

"This type of progression is usually handled through active monitoring continuity."

Bad:

❌

"You should upgrade immediately."


⸻

18. MONETIZATION PHILOSOPHY

Aura monetizes:
	•	continuity
	•	oversight
	•	operational priority
	•	reduced uncertainty
	•	structured care

NOT:
	•	panic
	•	diagnostics
	•	repair fear

That’s what keeps the brand premium.

⸻

19. CARE PLAN STATUS STATES

active
expired
grace_period
suspended
cancelled


⸻

20. VEHICLE-FIRST MODEL

IMPORTANT:

Care plans should belong to:

vehicles

NOT only users.

Why?
Because:
	•	one owner may have multiple vehicles
	•	different vehicles may need different monitoring levels
	•	fleets become possible later

Critical long-term decision.

⸻

21. FILE STRUCTURE

/app/care
│
├── routes.py
├── services.py
├── plans.py
├── entitlements.py
├── gating.py
├── middleware.py
└── templates/


⸻

22. CLIENT EXPERIENCE GOAL

The client should feel:

"My vehicle is under structured care."

NOT:

"I bought a subscription."

That emotional framing changes retention dramatically.

⸻

23. FUTURE EXPANSION

This architecture already supports:
	•	enterprise fleets
	•	advisor assignment tiers
	•	insurance integrations
	•	telemetry-based gating
	•	predictive monitoring
	•	regional service priority
	•	automated escalation tiers

without rewriting later.

⸻

24. MOST IMPORTANT RULE

Aura should never feel like:

a monetized chatbot

It should feel like:

a controlled automotive healthcare system

That difference is the entire brand moat.