AURA V2 — ADMIN CONSOLE

Ajebo Fix Automotive Health Platform

This is not:

a mechanic dashboard

This is:

an automotive clinical operations console

The Admin Console is where Aura becomes operationally scalable.

Without this system:
	•	everything becomes reactive
	•	advisor workload becomes chaotic
	•	risk visibility collapses
	•	continuity breaks
	•	growth becomes stressful

This console exists so you can calmly manage:
	•	50 vehicles
	•	100 vehicles
	•	eventually thousands

without mental overload.

⸻

1. CORE PHILOSOPHY

Most admin dashboards are:
	•	cluttered
	•	noisy
	•	reactive
	•	metric obsessed

Aura’s admin console should feel:
	•	calm
	•	high-trust
	•	controlled
	•	clinical
	•	operationally intelligent

Think:

private medical operations center

NOT:

workshop management software

⸻

2. PRIMARY OBJECTIVE

The Admin Console should answer:

"What needs my attention right now?"

immediately.

Not:
	•	endless menus
	•	giant tables
	•	random analytics overload

Clarity first.

⸻

3. CORE MODULES

Main Sections

/admin
    /overview
    /vehicles
    /consultations
    /treatment-plans
    /priority
    /alerts
    /clients
    /advisors
    /reports


⸻

4. ADMIN OVERVIEW DASHBOARD

The first screen matters massively.

Should immediately show:

Widget                                          Purpose

Vehicles Requiring Attention                Risk visibility
Active Consultations                        Operational load
Escalated Concerns                          Immediate review
Priority Queue                              Protected clients
Monitoring Queue                            Ongoing observation
Recent Activity                             Timeline awareness
Advisor Load                                Team balancing
Treatment Progression                       Operational flow



⸻

5. UI FEEL

The dashboard should feel:

stable
quiet
controlled

Avoid:
	•	blinking alerts everywhere
	•	“red danger dashboard”
	•	panic interfaces

Aura guides calm decision-making.

⸻

6. VEHICLE OPERATIONS PANEL

/admin/vehicles

Should allow:
	•	vehicle lookup
	•	health overview
	•	concern history
	•	escalation visibility
	•	treatment status
	•	monitoring state

This becomes:

the vehicle command center

⸻

7. CONSULTATION QUEUE

/admin/consultations

This is one of the MOST important operational systems.

Queue structure:

Critical
Priority
Monitoring
Standard
Deferred

This prevents:
	•	overload
	•	randomness
	•	advisor confusion

⸻

8. CONSULTATION CARD DESIGN

Each consultation should show:

Field                           Purpose

Vehicle                         identity
Client                          ownership
Concern Summary                 quick understanding
Urgency Level                   operational priority
Escalation State                clinical awareness
Last Interaction                continuity
Assigned Advisor                accountability
Current Plan State              progression



⸻

9. TREATMENT PLAN CONTROL

/admin/treatment-plans

Advisors/admins should:
	•	create plans
	•	transition states
	•	add updates
	•	monitor progression
	•	escalate concerns

Clients should NEVER directly modify treatment plans.

⸻

10. ALERT MANAGEMENT

/admin/alerts

Purpose:
	•	unresolved concerns
	•	recurring patterns
	•	telemetry escalation
	•	monitoring failures

Aura should surface:

progression

NOT just:

events

Example:

✅

Steering instability concern repeated 3 times in 7 days.

NOT:

❌

Warning alert triggered.


⸻

11. CLIENT MANAGEMENT

/admin/clients

Should show:
	•	linked vehicles
	•	active care plans
	•	escalation history
	•	consultation frequency
	•	monitoring state
	•	communication continuity

This becomes:

relationship intelligence

⸻

12. ADVISOR MANAGEMENT

/admin/advisors

Purpose:
	•	workload balancing
	•	consultation assignments
	•	escalation ownership
	•	continuity control

You’ll need this later when Ajebo Fix grows.

Build for it NOW.

⸻

13. PRIORITY ACCESS OPERATIONS

/admin/priority

Should show:
	•	active priority vehicles
	•	emergency review requests
	•	fast-track queue
	•	monitoring status

This is where scarcity becomes operational.

⸻

14. REPORTING SYSTEM

/admin/reports

Should generate:
	•	vehicle risk reports
	•	advisor workload reports
	•	unresolved concern trends
	•	monitoring analytics
	•	escalation patterns

Important:
Aura analytics should focus on:

continuity + progression

NOT vanity metrics.

⸻

15. ADMIN ACCESS CONTROL

Roles

Role                            Access

advisor/                    limited operational
senior_advisor              escalation authority
admin                       full control
super_admin                 system-level authority


This becomes critical later.

⸻

16. FILE STRUCTURE

/app/admin
│
├── routes.py
├── dashboard.py
├── consultations.py
├── vehicles.py
├── treatment_plans.py
├── alerts.py
├── reports.py
├── permissions.py
└── templates/


⸻

17. ADMIN TIMELINE VIEW

Very important.

Each vehicle should have:

Timeline View

Example:

May 2
Concern reported.

May 3
Priority review requested.

May 5
Treatment plan approved.

May 9
Monitoring stabilized.

May 14
Treatment completed.

This becomes:

operational continuity memory

⸻

18. CLINICAL AI BOUNDARY

Rina should support:
	•	awareness
	•	escalation
	•	summaries
	•	continuity

Rina should NEVER:
	•	override admin decisions
	•	auto-close treatment plans
	•	auto-diagnose
	•	autonomously approve treatment

The advisor remains the authority.

Always.

⸻

19. NOTIFICATION SYSTEM

Admin notifications should feel:
	•	calm
	•	operational
	•	useful

Good:

✅

2 vehicles entered elevated monitoring state.

Bad:

❌

URGENT ALERT!!!!!

Aura should never feel emotionally unstable.

⸻

20. FUTURE SCALING SUPPORT

This architecture already supports:
	•	multiple advisors
	•	multiple workshops
	•	regional operations
	•	fleet management
	•	telemetry ingestion
	•	predictive AI systems
	•	advisor specialization
	•	enterprise clients

without rebuilding later.

⸻

21. MOST IMPORTANT RULE

The Admin Console should reduce:

cognitive load

not increase it.

Every screen should answer:
	•	what matters?
	•	what changed?
	•	what needs action?

immediately.