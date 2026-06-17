AURA V2 — ROUTE MAP

Ajebo Fix Automotive Health Platform

This defines:
	•	endpoints
	•	responsibilities
	•	access control
	•	system flow
	•	AI boundaries

This is what stops the backend from turning into chaos later.

⸻

1. ROUTING PHILOSOPHY

Aura routes should feel:
	•	clinical
	•	structured
	•	controlled
	•	role-aware

NOT:
	•	random API dumping
	•	generic CRUD sprawl
	•	chatbot playground architecture

⸻

2. CORE ROUTE GROUPS

/auth
/dashboard
/vehicles
/consultations
/treatment-plans
/chat
/care
/priority
/admin
/api


⸻

3. AUTH ROUTES

Prefix

/auth


⸻

Routes

Route                           Purpose

/auth/login                 Login
/auth/logout                Logout
/auth/register              Register
/auth/forgot-password       Reset
/auth/verify                Verification


⸻

4. DASHBOARD ROUTES

Prefix

/dashboard


⸻

Owner Dashboard

Route                                   Purpose

/dashboard/                         Main overview
/dashboard/health                   Vehicle health overview
/dashboard/activity                 Recent vehicle activity
/dashboard/consultations            Consultation history



⸻

Driver Dashboard

Route                                       Purpose

/dashboard/driver                   Operational vehicle view
/dashboard/status                   Current vehicle state
/dashboard/report-concern           Report concern



⸻

Admin Dashboard

Route                                   Purpose

/dashboard/admin                    Physician console
/dashboard/admin/queue              Consultation queue
/dashboard/admin/risks              Active risk tracking
/dashboard/admin/vehicles           Fleet overview


5. VEHICLE ROUTES

Prefix

/vehicles


⸻

Routes

Route                                   Purpose

/vehicles                           Vehicle list
/vehicles/<id>                      Vehicle profile
/vehicles/<id>/health               Health details
/vehicles/<id>/history              Timeline/history
/vehicles/<id>/alerts               Active alerts
/vehicles/<id>/concerns             Concern history
/vehicles/<id>/consultations        Linked consultations
/vehicles/<id>/documents            Reports/files



⸻

6. CONSULTATION ROUTES

Prefix

/consultations


⸻

Client Routes

Route                               Purpose

/consultations/new              Request consultation
/consultations/<id>             Consultation details
/consultations/history          Consultation records



⸻

Admin Routes

Route                                       Purpose

/consultations/admin/queue              Pending queue
/consultations/admin/<id>               Clinical review
/consultations/admin/<id>/approve       Approve
/consultations/admin/<id>/defer         Defer
/consultations/admin/<id>/complete      Complete



⸻

7. TREATMENT PLAN ROUTES

Prefix

/treatment-plans


⸻

Routes

Route                                       Purpose

/treatment-plans/<id>                   View plan
/treatment-plans/<id>/status            Current state
/treatment-plans/admin/create           Create plan
/treatment-plans/admin/<id>/update      Update
/treatment-plans/admin/<id>/complete    Complete



⸻

Client Visibility Rule

Clients see:
	•	state
	•	timeline
	•	summary

Clients DO NOT see:
	•	repair workflow
	•	internal notes
	•	technician procedures

⸻

8. CHAT / RINA ROUTES

Prefix

/chat


⸻

Routes

Route                               Purpose

/chat                           Main Rina endpoint
/chat/history                   Conversation history
/chat/context                   Current AI state
/chat/reset-context             Reset active context



⸻

9. RINA INTERNAL FLOW

User Message
    ↓
Intent Detection
    ↓
Role Detection
    ↓
Vehicle Resolution
    ↓
Context Assembly
    ↓
Health Awareness Injection
    ↓
Rina AI Response
    ↓
Conversation Logging
    ↓
Topic Tracking
    ↓
Session Memory Update


⸻

10. CARE PLAN ROUTES

Prefix

/care


⸻

Routes

Route                           Purpose

/care/plans/                Available plans
/care/active                Active subscription
/care/upgrade               Upgrade flow
/care/access                Access overview



⸻

11. PRIORITY ACCESS ROUTES

Prefix

/priority


⸻

Routes

Route                           Purpose

/priority                   Locked overview
/priority/request           Emergency request
/priority/status            Request tracking



⸻

Important UX Rule

Non-members should:
	•	SEE priority access
	•	but NOT access it

This creates:

controlled exclusivity

without aggressive selling.

⸻

12. ADMIN / PHYSICIAN CONSOLE ROUTES

Prefix

/admin


⸻

13. ADMIN CORE ROUTES

Route                               Purpose

/admin                          Main console
/admin/vehicles/                Vehicle management
/admin/clients                  Client management
/admin/consultations            Consultation center
/admin/treatments               Treatment tracking
/admin/alerts                   Active alert monitoring
/admin/assessments              Assessment queue
/admin/logs                     Audit/access logs



⸻

14. API ROUTES (INTERNAL)

Prefix

/api


⸻

Purpose

Used for:
	•	async updates
	•	dashboard refresh
	•	AI calls
	•	live status updates

⸻

Example Routes

Route                                   Purpose

/api/vehicle/<id>/health            Health JSON
/api/chat/context                   AI context
/api/admin/queue                    Queue data
/api/alerts/live                    Active alerts



⸻

15. ACCESS CONTROL MATRIX

Route Group             Ownership           Driver          Admin

Dashboard View          YES                 YES             YES
Vehicle View            YES                 YES             YES
Treatment Edit          NO                  NO              YES
Consultation Request    YES                 YES             YES
Admin Console           NO                  NO              YES
Advisor Notes           NO                  NO              YES



⸻

16. ROUTE RESPONSIBILITY RULES

Routes should NOT:
	•	contain AI logic
	•	contain business intelligence
	•	contain large calculations
	•	contain memory orchestration

⸻

Routes SHOULD:
	•	validate request
	•	load service
	•	return response
	•	enforce access

⸻

17. SERVICE LAYER RESPONSIBILITY

Layer                           Responsibility

Routes                          HTTP handling
Services                        Business logic
AI Brain                        Response generation
Context Service                 Awareness assembly
Models                          Data structure
Memory Layer                    AI continuity



⸻

18. FUTURE REAL-TIME ROUTES (V2.1)

/ws/alerts
/ws/consultations
/ws/vehicle-health

Purpose:
	•	live monitoring
	•	advisor updates
	•	real-time escalation

⸻

19. CRITICAL SYSTEM RULES

RULE 1

Rina never directly modifies records.

Only services do.

⸻

RULE 2

Routes remain thin.

⸻

RULE 3

Admin authority remains centralized.

⸻

RULE 4

Clinical states come from DB, not AI memory.

⸻

20. CURRENT BUILD ORDER

Now completed:
✅ PRD
✅ ERD
✅ Route Map

Next:
	1.	Folder Structure
	2.	Service Architecture
	3.	Consultation Workflow
	4.	Treatment Flow
	5.	Admin Console
	6.	Care Plan Access Layer