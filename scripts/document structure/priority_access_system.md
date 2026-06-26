AURA V2 — PRIORITY ACCESS SYSTEM

Ajebo Fix Automotive Health Platform

This is NOT:

“VIP upselling”

This is:

controlled access architecture

The goal is psychological:
clients should feel:
	•	protected
	•	prioritized
	•	monitored
	•	professionally supported

NOT:
	•	sold to
	•	pressured
	•	marketed at

This system creates:
	•	scarcity
	•	exclusivity
	•	calm urgency
	•	recurring value

without looking cheap.

⸻

1. CORE PHILOSOPHY

Most businesses say:

"Pay more for premium support."

Aura says:

"Priority access is reserved for actively monitored vehicles."

Huge difference.

One feels:
	•	transactional

The other feels:
	•	clinical
	•	controlled
	•	earned

⸻

2. PRIMARY OBJECTIVE

Create:
	•	structured exclusivity
	•	operational filtering
	•	controlled availability

without:
	•	aggressive sales tactics
	•	discount psychology
	•	“premium package” energy

⸻

3. WHAT PRIORITY ACCESS INCLUDES

Core Access Features

Feature                                     Purpose

Priority Scheduling                     Faster review access
Emergency Review Request                Elevated concern queue
Active Monitoring Queue                 Higher observation frequency
Escalation Fast-Track                   Critical review path
Advisor Continuity                      Same advisor continuity
Direct Review Requests                  Controlled advisor access


These are:

operational privileges

NOT:

marketing perks

⸻

4. ACCESS TIERS

STANDARD

Access:
	•	normal consultation flow
	•	standard monitoring
	•	regular scheduling

⸻

ACTIVE MONITORING

Access:
	•	elevated observation
	•	treatment continuity
	•	periodic review priority

⸻

PRIORITY ACCESS

Access:
	•	emergency review path
	•	accelerated scheduling
	•	escalation fast-track
	•	advisor continuity

This should feel:

protected

not:

luxury flexing

⸻

5. UI PHILOSOPHY

The locked experience matters heavily.

Non-members should SEE:
	•	priority systems
	•	emergency review
	•	fast-track access

BUT:
they should feel:

"This is controlled access."

NOT:

"They're trying to upsell me."

Subtle difference.

Massive impact.

⸻

6. LOCKED SECTION DESIGN

GOOD

Priority Review Access

Reserved for vehicles under active care coverage.


⸻

BAD

Upgrade now for premium support!!!

Never do this.

Aura must stay calm and clinical.

⸻

7. DATABASE MODEL

class CarePlan(db.Model):
    id

    user_id
    vehicle_id

    tier

    active_monitoring
    priority_access

    started_at
    expires_at

    status


⸻

8. ACCESS CONTROL SYSTEM

Middleware Style

if not care_plan.priority_access:
    deny_priority_route()

Simple.
Controlled.
Clean.

⸻

9. ROUTE STRUCTURE

/priority
/priority/request
/priority/emergency
/priority/status


⸻

10. EMERGENCY REVIEW REQUEST

Very important psychologically.

The user should feel:

“If something serious happens, I’m not alone.”

Example UI:

Emergency Review Request

Reserved for vehicles currently under Priority Access monitoring.

That wording matters.

⸻

11. RINA’S ROLE

Rina can:
	•	reference access limitations
	•	suggest escalation
	•	reinforce monitoring importance

Rina CANNOT:
	•	pressure upgrades
	•	sell subscriptions aggressively
	•	guilt users

Example:

✅

"This type of concern is usually handled through priority review monitoring."

NOT:

❌

"You should upgrade now."


⸻

12. SCARCITY SYSTEM

Scarcity should feel:
	•	operational
	•	capacity-based
	•	protective

NOT:
	•	fake marketing urgency

Example:

✅

Priority scheduling capacity is intentionally limited.

NOT:

❌

"LIMITED TIME OFFER"

Aura is not a sales funnel.

It’s an automotive care institution.

⸻

13. ADVISOR PRIORITY QUEUE

Priority clients should surface differently.

Example:

Priority Queue
Critical Queue
Monitoring Queue
Standard Queue

This keeps advisor operations calm at scale.

⸻

14. VEHICLE-FIRST ACCESS

Important:
Priority access should belong to:

the vehicle

NOT just the user.

Because:
	•	different vehicles
	•	different risk levels
	•	different care states

This becomes crucial later for fleets.

⸻

15. FILE STRUCTURE

/app/priority
│
├── routes.py
├── middleware.py
├── services.py
├── access_control.py
├── queue.py
└── templates/


⸻

16. ACCESS STATES

inactive
active
expired
suspended
restricted


⸻

17. PRIORITY REQUEST FLOW

Concern Escalates
    ↓
Priority Eligibility Check
    ↓
Queue Assignment
    ↓
Advisor Review
    ↓
Treatment Progression


⸻

18. CLIENT EXPERIENCE GOAL

The client should feel:

"My vehicle is being professionally watched."

NOT:

"I bought a premium package."

That distinction is EVERYTHING.

⸻

19. RELATIONSHIP TO MONETIZATION

Priority Access monetizes:
	•	speed
	•	continuity
	•	oversight
	•	confidence
	•	reduced uncertainty

NOT:
	•	repairs
	•	parts
	•	panic

This keeps Aura premium.

⸻

20. FUTURE EXPANSION

This architecture supports:
	•	enterprise fleet priority
	•	AI risk escalation routing
	•	advisor assignment systems
	•	regional response queues
	•	telemetry-triggered escalation
	•	emergency dispatch logic

without redesigning later.

⸻

21. MOST IMPORTANT RULE

Priority Access should feel:

medically reserved

NOT:

commercially advertised

That emotional positioning changes the entire platform perception.