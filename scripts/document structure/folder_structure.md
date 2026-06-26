AURA V2 — FOLDER STRUCTURE

Ajebo Fix Automotive Health Platform

This structure is designed for:
	•	scale
	•	calm development
	•	clinical separation
	•	AI boundaries
	•	multi-advisor growth
	•	future mobile/API expansion

This is how you avoid:
	•	spaghetti Flask apps
	•	circular imports
	•	AI logic chaos
	•	route duplication
	•	“where did I put this file?” syndrome

⸻

1. CORE PHILOSOPHY

Aura is NOT:
	•	a chatbot app
	•	a repair app
	•	a random Flask project

Aura IS:
	•	a clinical automotive operating system

So the structure should feel:
	•	modular
	•	controlled
	•	layered
	•	role-aware
	•	expandable

⸻

2. ROOT STRUCTURE

/aura
│
├── app/
├── migrations/
├── tests/
├── requirements.txt
├── config.py
├── run.py
├── .env
└── README.md


⸻

3. APP STRUCTURE

/app
│
├── auth/
├── dashboard/
├── vehicles/
├── consultations/
├── treatment_plans/
├── care/
├── priority/
├── admin/
├── chat/
├── ai/
├── services/
├── models/
├── templates/
├── static/
├── utils/
├── extensions/
├── middleware/
└── api/


⸻

4. AUTH MODULE

/auth
│
├── routes.py
├── forms.py
├── services.py
├── validators.py
├── decorators.py
└── templates/

Purpose:
	•	authentication
	•	verification
	•	session control
	•	role gating

⸻

5. DASHBOARD MODULE

/dashboard
│
├── routes.py
├── services.py
├── widgets.py
└── templates/

Purpose:
	•	owner dashboard
	•	driver dashboard
	•	advisor overview

This should NEVER contain:
	•	AI logic
	•	treatment logic
	•	diagnosis logic

Dashboard only presents state.

⸻

6. VEHICLES MODULE

/vehicles
│
├── routes.py
├── services.py
├── health_engine.py
├── monitoring.py
├── events.py
├── alerts.py
└── templates/

Purpose:
	•	vehicle records
	•	health scoring
	•	monitoring
	•	alerts
	•	snapshots
	•	history timeline

This becomes the core medical-record layer.

⸻

7. CONSULTATIONS MODULE

/consultations
│
├── routes.py
├── services.py
├── queue.py
├── summaries.py
├── escalation.py
└── templates/

Purpose:
	•	consultation requests
	•	advisor review queue
	•	escalation management
	•	summaries
	•	appointment lifecycle

VERY IMPORTANT:
Rina should escalate INTO consultations.
Not solve consultations herself.

⸻

8. TREATMENT PLANS MODULE

/treatment_plans
│
├── routes.py
├── services.py
├── states.py
├── transitions.py
└── templates/

Purpose:
	•	Approved
	•	In Progress
	•	Deferred
	•	Monitoring
	•	Completed

This is one of the MOST important Aura systems.

It creates:

“professional care continuity”

instead of:

“one-off mechanic repairs”

⸻

9. CARE MODULE

/care
│
├── routes.py
├── plans.py
├── entitlements.py
├── monitoring.py
└── templates/

Purpose:
	•	care plans
	•	monitoring access
	•	preventive coverage
	•	membership logic

This handles monetization elegantly.

⸻

10. PRIORITY MODULE

/priority
│
├── routes.py
├── access.py
├── queue.py
└── templates/

Purpose:
	•	priority scheduling
	•	emergency review requests
	•	member-only access

This is scarcity infrastructure.

Very important psychologically.

⸻

11. CHAT MODULE

/chat
│
├── routes.py
├── websocket.py
├── session.py
├── logging.py
└── templates/

Purpose:
	•	messaging transport
	•	session handling
	•	history
	•	streaming

IMPORTANT:
Chat module should NOT contain AI intelligence.

Only communication transport.


nly communication transport.

⸻

12. AI MODULE

/ai
│
├── brain.py
├── prompts.py
├── memory.py
├── escalation.py
├── topic_tracking.py
├── behavior.py
├── role_control.py
├── response_style.py
└── safety.py

THIS is Rina.

Not routes.
Not templates.
Not Flask logic.

Pure AI orchestration.

This separation is CRITICAL.

⸻

13. SERVICES MODULE

/services
│
├── vehicle_health_service.py
├── consultation_service.py
├── treatment_service.py
├── notification_service.py
├── audit_service.py
├── analytics_service.py
└── report_service.py

Purpose:
Business logic layer.

Routes should call services.

NOT:
	•	giant route files
	•	direct DB logic everywhere

⸻

14. MODELS MODULE

/models
│
├── user.py
├── vehicle.py
├── consultation.py
├── treatment_plan.py
├── chat.py
├── alerts.py
├── events.py
├── care_plan.py
└── __init__.py

Purpose:
database entities only.

NO business logic here.

⸻

15. API MODULE

/api
│
├── v1/
│   ├── auth.py
│   ├── vehicles.py
│   ├── consultations.py
│   └── chat.py
│
└── middleware/

Purpose:
future:
	•	mobile apps
	•	external integrations
	•	telemetry
	•	partner systems

⸻

16. MIDDLEWARE MODULE

/middleware
│
├── auth_guard.py
├── role_guard.py
├── audit_logger.py
├── request_context.py
└── rate_limit.py

Purpose:
	•	security
	•	tracking
	•	access control
	•	audit compliance

⸻

17. UTILS MODULE

/utils
│
├── dates.py
├── formatting.py
├── validators.py
├── helpers.py
└── constants.py

Small reusable helpers only.

NOT business systems.

⸻

18. EXTENSIONS MODULE

/extensions
│
├── db.py
├── login_manager.py
├── migrate.py
├── mail.py
└── cache.py

Purpose:
centralize Flask extensions cleanly.

⸻

19. STATIC + TEMPLATES

/templates
/static

Should be organized by module.

Example:

/templates/vehicles/
/templates/admin/
/templates/chat/

NOT:
	•	one giant templates folder nightmare

⸻

20. TESTS STRUCTURE

/tests
│
├── auth/
├── vehicles/
├── consultations/
├── ai/
├── admin/
└── integration/

This becomes critical later.

Especially for:
	•	escalation boundaries
	•	AI safety
	•	role access
	•	treatment state transitions

⸻

21. MOST IMPORTANT STRUCTURAL RULE

ROUTES DO NOT THINK

Routes should:
	•	receive request
	•	validate
	•	call service
	•	return response

That’s it.

⸻

22. MOST IMPORTANT AI RULE

RINA NEVER DIRECTLY CONTROLS THE DATABASE

Rina:
	•	observes
	•	interprets
	•	escalates
	•	guides

Services:
	•	update records
	•	create consultations
	•	modify plans
	•	save state

That separation protects the platform long-term.

⸻

23. FUTURE EXPANSION READY

This structure already supports:
	•	mobile app
	•	live telemetry
	•	advisor teams
	•	subscription billing
	•	AI memory layers
	•	multilingual support
	•	enterprise fleets
	•	predictive maintenance
	•	vehicle digital twins

Without rewriting the architecture later.

That’s the whole point.