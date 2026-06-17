AURA V2 — CLINICAL CONVERSATION LOGGING

Ajebo Fix Automotive Health Platform

This is one of the most important systems in Aura.

Why?

Because conversations are NOT chats anymore.

They are:
	•	clinical records
	•	liability protection
	•	continuity systems
	•	behavioral history
	•	escalation evidence
	•	advisor intelligence

This is where Aura stops feeling like a chatbot
and starts feeling like a private automotive health institution.

⸻

1. CORE PHILOSOPHY

Aura does NOT treat conversations as:
	•	disposable
	•	temporary
	•	“AI chat history”

Aura treats conversations as:

vehicle health records

Every important interaction becomes part of:
	•	vehicle memory
	•	advisor awareness
	•	risk continuity
	•	treatment context

⸻

2. PRIMARY OBJECTIVE

Convert:

Random chat messages

into:

Structured automotive health records

automatically.

⸻

3. WHAT SHOULD BE LOGGED

ALWAYS LOG

Type                                Example

User concerns                   “Car feels unstable”
Symptoms                        “Steering vibration”
Severity language               “Urgent”, “asap”
Vehicle state references        “Warning light came back”
Escalation triggers             “Can I still drive?”
Timeline mentions               “Started yesterday”
Emotional state                 anxious / hesitant / frustrated
AI escalation                   review advised
Consultation references         booking requested
Vehicle mentioned               active vehicle
Timestamp                       exact UTC
User role                       owner/driver/advisor


4. WHAT SHOULD NOT BE LOGGED

NEVER STORE

Bad Logging                         Why

Entire raw AI prompt            bloated + unsafe
OpenAI system prompts           security
Internal chain logic            architecture leakage
Session secrets                 security risk
Token metadata                  useless clinically


Aura should store:

meaningful medical-style summaries

NOT AI internals.

⸻

5. CONVERSATION ARCHITECTURE

Layer 1 — Raw Chat

ChatMessage

Stores:
	•	actual conversation
	•	user text
	•	assistant response

Purpose:
conversation continuity.

⸻

Layer 2 — Clinical Summary

ConversationRecord

Stores:
	•	distilled meaning
	•	advisor summary
	•	escalation state
	•	vehicle relevance

Purpose:
professional continuity.

This is the IMPORTANT layer.

⸻

6. DATABASE MODEL

ChatMessage

class ChatMessage(db.Model):
    id
    user_id
    vehicle_id

    role
    message

    created_at

Simple.

Raw conversation transport.

⸻

ConversationRecord

class ConversationRecord(db.Model):
    id

    user_id
    vehicle_id

    concern
    summary

    emotional_state
    urgency_level

    escalation_level

    consultation_related

    created_at

THIS becomes:
	•	advisor intelligence
	•	audit history
	•	continuity memory

⸻

7. EMOTIONAL STATE TRACKING

Aura should detect:

State                           Meaning

calm                        informational
anxious                     concern rising
urgent                      immediate pressure
hesitant                    avoiding action
frustrated                  trust degradation


Why this matters:

Rina should adapt tone based on emotional progression.

That’s clinical behavior modeling.

⸻

8. URGENCY LEVELS

low
moderate
high
critical

Derived from:
	•	language
	•	symptoms
	•	alerts
	•	escalation
	•	repeated concern frequency

⸻

9. ESCALATION STATES

monitor
review_advised
priority_review
unsafe_operation

This is HUGE.

Because Aura is NOT diagnosing.

Aura is:

escalating risk professionally

Exactly like a clinic.

⸻

10. AI BOUNDARY RULE

Rina NEVER says:

❌
	•	“replace the transmission”
	•	“change the brake pads”
	•	“this is definitely the issue”

Rina DOES say:

✅
	•	“That needs professional review”
	•	“I wouldn’t ignore that progression”
	•	“This should be assessed soon”
	•	“I’m seeing signs that deserve attention”

That distinction protects:
	•	liability
	•	trust
	•	professionalism

⸻

11. AUTO SUMMARY SYSTEM

Every important interaction should generate:

Advisor Summary

Example:

Client reported recurring steering instability and warning light persistence.

Concern appears unresolved and urgency language increased during interaction.

Vehicle remains operational but monitoring concern progression.

This is what advisors read.

NOT giant raw chats.

⸻

12. WHEN TO CREATE A RECORD

CREATE RECORD IF:

Trigger                             Example

Risk language                   “urgent”
Symptoms detected               “vibration”
Warning references              “light came back”
Escalation phrases              “safe to drive?”
Consultation intent             “book review”
Repeated concern                recurring topic
Emotional escalation            frustration/anxiety



⸻

13. WHEN NOT TO CREATE RECORD

Do NOT create records for:

"hi"
"thanks"
"okay"
"lol"

Avoid database pollution.

⸻

14. FILE STRUCTURE

/app/consultations/
│
├── logging.py
├── summarizer.py
├── escalation.py
└── records.py


⸻

15. LOGGING FLOW

User Message
    ↓
Intent Detection
    ↓
Risk Detection
    ↓
Emotional Analysis
    ↓
Escalation Analysis
    ↓
Advisor Summary
    ↓
Save ConversationRecord

That’s the pipeline.

⸻

16. RELATIONSHIP TO VEHICLE HISTORY

Conversation records should appear in:

Vehicle Timeline

Example:

May 7
Client reported worsening steering instability.

May 10
Priority review requested.

May 11
Treatment plan approved.

This is where Aura becomes powerful.

⸻

17. RELATIONSHIP TO RINA MEMORY

Rina should reference:
	•	prior concerns
	•	unresolved progression
	•	escalation history
	•	emotional shifts

WITHOUT sounding repetitive.

Example:

✅

“That concern has been coming up repeatedly now.”

NOT:

❌

“Conversation record #12 indicates…”

⸻

18. FUTURE UPGRADE PATH

This architecture supports:
	•	AI memory compression
	•	predictive escalation
	•	vehicle behavioral profiling
	•	advisor analytics
	•	fleet risk scoring
	•	insurance-grade records
	•	legal audit trails

Without rewriting later.

⸻

19. MOST IMPORTANT RULE

Aura logs:

meaning

NOT:

messages

That single distinction changes the entire platform architecture.