AURA V2 — ENTITY RELATIONSHIP DESIGN (ERD)

Ajebo Fix Automotive Health Platform

This is the backbone of the entire system.

If the PRD defines:

what Aura is

The ERD defines:

how Aura thinks

This keeps you from building random tables later and ending up with spaghetti architecture.

⸻

1. CORE SYSTEM MAP

USER
 ├── owns → VEHICLE
 ├── drives → VEHICLE
 ├── creates → CONSULTATION
 ├── sends → CHAT_MESSAGE
 └── subscribes → CARE_PLAN

VEHICLE
 ├── has → HEALTH_SNAPSHOT
 ├── has → HEALTH_ALERT
 ├── has → VEHICLE_EVENT
 ├── has → CONCERN
 ├── has → TREATMENT_PLAN
 ├── has → CONSULTATION
 └── has → ASSESSMENT

CONSULTATION
 ├── linked_to → VEHICLE
 ├── linked_to → USER
 ├── contains → CONCERN
 ├── contains → ADVISOR_NOTE
 └── may_create → TREATMENT_PLAN


 2. USERS

Table: users

Purpose:
Core identity table.

⸻

Fields

id
first_name
last_name
email
password_hash
role
phone
created_at
updated_at
is_active

⸻

Roles

owner
driver
admin
advisor

⸻

3. VEHICLES

Table: cars

Purpose:
Vehicle identity.

⸻

Fields

id
brand
model
year
vin
plate_number
color
mileage
created_at
updated_at

⸻

4. OWNERSHIP

Table: car_ownerships

Purpose:
Defines ownership relationship.

⸻

Fields

id
user_id
car_id
is_active
start_date
end_date
created_at

⸻

5. DRIVER ASSIGNMENT

Table: car_drivers

Purpose:
Driver assignment system.

⸻

Fields

id
user_id
car_id
assigned_by
is_active
created_at
removed_at

⸻

6. VEHICLE HEALTH SNAPSHOT

Table: vehicle_health_snapshots

Purpose:
Stores calculated vehicle health state.

⸻

Fields

id
car_id
health_score
health_status
risk_level
escalation_level
summary
created_at

⸻

Health Status

healthy
monitor
attention
critical

⸻

7. VEHICLE ALERTS

Table: vehicle_health_alerts

Purpose:
Active monitoring alerts.

⸻

Fields

id
car_id
alert_type
severity
message
is_active
created_at
resolved_at

⸻

8. VEHICLE EVENTS

Table: vehicle_events

Purpose:
Chronological activity log.

⸻

Fields

id
car_id
event_type
title
description
severity
source
created_by
created_at

⸻

Examples Events

Oil Change
Warning Light
Consultation Created
Brake Concern
Inspection Completed
Treatment Approved

⸻

9. CONCERNS

Table: vehicle_concerns

Purpose:
Structured concern tracking.

This is extremely important.

⸻

Fields

id
car_id
reported_by
consultation_id
category
title
description
severity
status
created_at
updated_at

⸻

Status

reported
under_review
monitoring
resolved

⸻

Categories

engine
transmission
brakes
suspension
electrical
cooling
steering
warning_lights

⸻

10. CONSULTATIONS

Table: consultations

Purpose:
Clinical interaction layer.

⸻

Fields

id
car_id
user_id
advisor_id
status
priority_level
summary
created_at
updated_at

⸻

Status

requested
approved
in_progress
completed
deferred

⸻

11. CHAT RECORDS

Table: chat_messages

Purpose:
Stores Rina conversations.

⸻

Fields

id
user_id
car_id
role
message
ai_summary
intent
escalation_level
created_at

⸻

Roles

user
assistant
advisor
system

⸻

12. ADVISOR NOTES

Table: advisor_notes

Purpose:
Internal-only observations.

NOT visible to clients.

⸻

Fields

id
car_id
consultation_id
advisor_id
note
risk_level
created_at

⸻

13. VEHICLE ASSESSMENTS

Table: vehicle_assessments

Purpose:
Structured inspection workflow.

⸻

Fields

id
car_id
advisor_id
status
summary
recommendations
created_at
completed_at

⸻

14. TREATMENT PLANS

Table: treatment_plans

Purpose:
Professional workflow management.

⸻

Fields

id
car_id
consultation_id
advisor_id
title
status
priority
created_at
updated_at
completed_at


⸻

Status

approved
in_progress
completed
deferred


⸻

15. TREATMENT ITEMS

Table: treatment_plan_items

Purpose:
Internal breakdown of work.

⸻

Fields

id
treatment_plan_id
title
description
status
created_at
updated_at


⸻

16. CARE PLANS

Table: care_plans

Purpose:
Subscription/membership model.

⸻

Fields

id
name
slug
description
priority_access
monitoring_enabled
consultation_limit
created_at


⸻

Examples

Active Monitoring
Preventive Coverage
Priority Access


⸻

17. USER SUBSCRIPTIONS

Table: user_care_plans

Purpose:
Links users to care plans.

⸻

Fields

id
user_id
care_plan_id
status
started_at
expires_at
created_at


⸻

18. PRIORITY ACCESS REQUESTS

Table: priority_requests

Purpose:
Emergency escalation queue.

⸻

Fields

id
user_id
car_id
consultation_id
reason
status
created_at
resolved_at


⸻

Status

pending
accepted
rejected
resolved


⸻

19. ACCESS CONTROL LOGS

Table: access_logs

Purpose:
Track sensitive actions.

⸻

Fields

id
user_id
action
target_type
target_id
ip_address
created_at


⸻

20. SYSTEM MEMORY (OPTIONAL V2.1)

Table: rina_memory

Purpose:
Long-term AI memory.

⸻

Fields

id
user_id
car_id
memory_type
content
importance
created_at


⸻

21. RELATIONSHIP SUMMARY

USER ↔ VEHICLE

Relationship            Table

Ownership           car_ownerships
Driver Access       car_drivers


⸻

VEHICLE ↔ HEALTH

Relationship            Table

Snapshot            vehicle_health_snapshots
Alerts              vehicle_health_alerts
Events              vehicle_events
Concerns            vehicle_concerns


⸻

CONSULTATION SYSTEM

Relationship            Table

Consultation       consultations
Advisor Notes      advisor_notes
Treatment Plan     treatment_plans


⸻

22. CRITICAL DESIGN RULES

RULE 1

Rina NEVER becomes source of truth.

Database becomes source of truth.

⸻

RULE 2

Conversations become structured records.

⸻

RULE 3

Everything important links back to:
	•	user
	•	vehicle
	•	consultation

⸻

RULE 4

Treatment plans are workflow states, not tutorials.

⸻

RULE 5

Access control must remain role-based.

⸻

23. CURRENT BUILD PRIORITY

Now that PRD + ERD are defined:

Next:
	1.	Route Map
	2.	Folder Structure
	3.	Consultation Workflow
	4.	Treatment Plan Flow
	5.	Admin Console Flow
	6.	Care Plan Gating