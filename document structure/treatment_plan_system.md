AURA V2 — TREATMENT PLAN SYSTEM

Ajebo Fix Automotive Health Platform

This is where Aura evolves from:

“chat + vehicle dashboard”

into:

a controlled automotive care environment

The Treatment Plan System is the backbone of:
	•	professionalism
	•	continuity
	•	authority
	•	monetization
	•	calm client experience

This is one of the most important systems in Aura.

⸻

1. CORE PHILOSOPHY

Traditional mechanic workflow:

Problem → Repair → Payment → Disappear

Aura workflow:

Concern → Assessment → Treatment Plan → Monitoring → Continuity

That difference changes:
	•	perceived value
	•	trust
	•	retention
	•	pricing power
	•	brand positioning

⸻

2. WHAT A TREATMENT PLAN IS

A Treatment Plan is NOT:
	•	repair instructions
	•	DIY advice
	•	mechanic notes

A Treatment Plan IS:
	•	controlled care progression
	•	advisor-managed resolution state
	•	client-facing operational visibility

The client sees:

progress

NOT:

technical chaos

⸻

3. SYSTEM OBJECTIVE

The treatment plan system should make the client feel:

"This is being professionally managed."

Not:

"I need to figure everything out myself."

That emotional shift is the product.

⸻

4. CORE TREATMENT STATES

Official States

State                                           Meaning

pending_review                              Awaiting advisor assessment
approved                                    Care path approved
scheduled                                   Service timeline assigned
in_progress                                 Work actively ongoing
monitoring                                  Deferred observation state
completed                                   Treatment completed
escalated                                   Elevated concern
cancelled                                   Closed without progression


These states must feel:
	•	calm
	•	clinical
	•	intentional

NOT:
	•	repair shop chaos

⸻

5. CLIENT EXPERIENCE

Client SHOULD see:

✅
	•	current status
	•	progression
	•	timeline
	•	monitoring state
	•	advisor updates

Client should NOT see:

❌
	•	internal diagnostics
	•	technical repair notes
	•	advisor debates
	•	raw workshop discussions

Aura protects operational clarity.

⸻

6. TREATMENT PLAN FLOW

Concern Detected
    ↓
Advisor Review
    ↓
Treatment Plan Created
    ↓
State Progression
    ↓
Monitoring / Completion

Simple.
Controlled.
Professional.

⸻

7. DATABASE MODEL

class TreatmentPlan(db.Model):
    id

    vehicle_id
    client_id
    advisor_id

    title
    summary

    state

    priority_level

    started_at
    completed_at

    created_at


⸻

8. TREATMENT ACTIONS

Separate model.

class TreatmentAction(db.Model):
    id

    treatment_plan_id

    action
    note

    visible_to_client

    created_by
    created_at

This creates:
	•	timeline continuity
	•	advisor notes
	•	audit history

⸻

9. PRIORITY LEVELS

routine
attention
priority
critical

Used for:
	•	dashboard highlighting
	•	scheduling priority
	•	escalation visibility

NOT fear marketing.

⸻

10. MONITORING STATE (VERY IMPORTANT)

This is one of Aura’s most powerful concepts.

Most mechanics think only in:
	•	repair
	•	replace

Aura understands:

controlled observation

Example:

"Vehicle remains operational while progression is monitored."

This feels:
	•	intelligent
	•	premium
	•	calm

Monitoring becomes a paid value layer.

⸻

11. CLIENT STATUS LANGUAGE

Aura should NEVER say:

❌
	•	“Waiting for mechanic”
	•	“Repair pending”
	•	“Workshop delay”

Instead:

✅
	•	“Under advisor review”
	•	“Monitoring progression”
	•	“Scheduled for assessment”
	•	“Awaiting treatment phase”

Language defines positioning.

⸻

12. AI BOUNDARIES

Rina can:
	•	reference plan state
	•	explain progression calmly
	•	reinforce monitoring

Rina CANNOT:
	•	modify treatment state
	•	approve plans
	•	create repair decisions

Only advisors/admins can.

This is critical.

⸻

13. ROLE ACCESS

Owner

Can:
	•	view status
	•	see summaries
	•	request updates

Cannot:
	•	alter states
	•	edit plans

⸻

Driver

Can:
	•	report symptoms
	•	view limited status

Cannot:
	•	approve anything
	•	access financials
	•	modify plans

⸻

Advisor

Can:
	•	create plans
	•	update states
	•	add actions
	•	escalate

⸻

Admin

Full control.

⸻

14. UI PHILOSOPHY

Treatment plans should feel like:

Hidden from client.

⸻

Client Notes

client_visible_update

Controlled messaging.

Critical distinction.

⸻

19. NOTIFICATION SYSTEM

Clients should receive calm updates.

Example:

✅

Your vehicle remains under active monitoring.

No escalation has been observed since the last review.

NOT:

❌

Repair delayed.


⸻

20. RELATIONSHIP TO MONETIZATION

Treatment plans enable:
	•	ongoing care
	•	continuity billing
	•	monitoring subscriptions
	•	premium access
	•	advisor relationships

This is where recurring revenue becomes natural.

⸻

21. FUTURE EXPANSION

This architecture already supports:
	•	fleet treatment plans
	•	AI risk progression
	•	predictive maintenance
	•	insurance integration
	•	advisor teams
	•	escalation automation
	•	telemetry-triggered monitoring

without rewrites later.

⸻

22. MOST IMPORTANT RULE

Aura does NOT sell repairs.

Aura manages:

automotive health progression

That single framing changes the entire business model.