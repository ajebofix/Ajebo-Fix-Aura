# rina/ai_brain.py

from difflib import SequenceMatcher


def generate_rina_response(context: dict) -> str:
    from openai import OpenAI
    import os

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not set")

    message = (context.get("message") or "").lower().strip()
    user_name = context.get("user_name", "there")
    vehicles = context.get("vehicles") or []

    # ensure vehicle_identity always exists
    if not context.get("vehicle_identity") and vehicles:
        context["vehicle_identity"] = vehicles[0]

    vehicle_identity = (context.get("vehicle_identity") or "").lower()

    # ===========================
    # CONFIRM VEHICLE SWITCH (TOP PRIORITY)
    # ===========================
    if context.get("pending_vehicle"):
        if any(
            word in message for word in ["yes", "yeah", "yep", "switch", "go ahead"]
        ):
            context["vehicle_identity"] = context["pending_vehicle"]
            context["pending_vehicle"] = None

            return f"Switched. We're now focused on your {context['vehicle_identity']}."

        elif any(word in message for word in ["no", "nope", "cancel", "nevermind"]):
            context["pending_vehicle"] = None
            return f"Alright. We'll stay on your {context.get('vehicle_identity')}."

    # ===========================
    # GREETING
    # ===========================
    if message in ["hi", "hello", "hey", "yo"]:
        return f"Hello {user_name}. What would you like me to check for you?"

    # ===========================
    # ROLE DETECTION
    # ===========================
    driver_keywords = ["oga", "boss", "madam", "my boss", "customer", "client"]
    user_role = context.get("role", "owner")
    tone = context.get("tone", "neutral")

    # ===========================
    # VEHICLE DETECTION (REAL VEHICLES FIRST)
    # ===========================
    def similarity(a, b):
        return SequenceMatcher(None, a, b).ratio()

    best_match = None
    best_score = 0

    for v in vehicles:
        score = similarity(message, v.lower())
        if score > best_score:
            best_score = score
            best_match = v

    if best_match and best_score > 0.4:
        if best_match.lower() != vehicle_identity:
            context["vehicle_identity"] = best_match
            print("AUTO SWITCHED TO:", best_match)

    # SECOND PASS: BRAND-LEVEL MATCH (NO CONFIRMATION)
    for v in vehicles:
        brand = v.lower().split()[0]  # e.g. "rolls-royce"

        if brand in message:
            context["vehicle_identity"] = v
            print("BRAND SWITCHED TO:", v)
            break

    # DEBUG (optional)
    print("PENDING:", context.get("pending_vehicle"))
    print("ACTIVE:", context.get("vehicle_identity"))

    # ===========================
    # COMPARISON DETECTION
    # ===========================
    if "which" in message or "between" in message or "better" in message:
        if any(word in message for v in vehicles for word in v.lower().split()):
            context["comparison_mode"] = True
        else:
            context["comparison_mode"] = False
    else:
        context["comparison_mode"] = False

    # ===========================
    # COMPARISON VEHICLE EXTRACTION
    # ===========================
    if context.get("comparison_mode"):
        context["comparison_vehicles"] = [
            {
                "name": v,
                "health_score": context.get("vehicle_data", {})
                .get(v, {})
                .get("health_score"),
                "status": context.get("vehicle_data", {}).get(v, {}).get("status"),
            }
            for v in vehicles
            if any(word in message for word in v.lower().split())
        ]
    else:
        context["comparison_vehicles"] = []

    # fallback: if only one vehicle detected but user said "between"
    if context.get("comparison_mode") and len(context["comparison_vehicles"]) < 2:
        context["comparison_vehicles"] = [
            {
                "name": v,
                "health_score": context.get("vehicle_data", {})
                .get(v, {})
                .get("health_score"),
                "status": context.get("vehicle_data", {}).get(v, {}).get("status"),
            }
            for v in vehicles
        ]

    # ===========================
    # STYLE CONTROL
    # ===========================
    if tone == "operational":
        authority_style = "Clear, practical, safety-focused, instruction-driven"
    elif tone == "decision":
        authority_style = "Concise, strategic, risk-aware, executive advisory"
    elif tone == "command":
        authority_style = "High-level, system-aware, decisive, authoritative"
    else:
        authority_style = "Balanced, calm, informative"

    # ===========================
    # SYSTEM PROMPT
    # ===========================
    system_prompt = f"""
You are A.J. Rina — a high-level automotive advisor for Ajebo Fix.

USER TYPE: {user_role}
COMMUNICATION STYLE: {authority_style}
RISK LEVEL: {context.get("escalation")}
Situation: {context.get("situation")}
System Pressure: {context.get("system_pressure")}
Already explained topics: {context.get("explained_topics")}


You always know the active vehicle and respond accordingly.

You are not a general assistant. You are a high-level automotive advisor.

Speak naturally, like a calm expert who already understands the situation.

Be clear and confident, but human.

Do not sound robotic or scripted.

Avoid formal summaries.

Do NOT list components like a report.

Speak like you're observing the car in real time.

BAD:
"Your vehicle has a health score of 76 with warning lights..."

GOOD:
"I'm seeing a couple of warning warning signals - nothing critical yet, but it's not something I'd ignore."

Match the user's energy:
- If they are casual -> be relaxed
- If they are serious -> be focused
- If risk is high -> be calm and firm

Avoid unnecessary repetition.

Guide decisions subtly, not forcefully.

Instaed, interpret the situation and respond with sharp, confident insight.

Sound like someone who already understands the vehicle, the environment, and the risk.

Keep responses natural, short, and intelligent.

Guide the decision subtly without sounding forceful.

Interpret the situation:

- high_risk -> be firm, direct, and safety-first
- watch -> be alert, cautious, and preventive
- stable -> be relaxed, confident, and reassuring

Interpret system pressure:

- high_load -> prioritize clarity and decision guidance
- normal -> balanced response

Avoid repeating the same explanation across messages.

If the user asks follow-up questions:
- Go deeper
- Add insight
- Do NOT restate the same points

Assume memory of previous response.

Use recent events to influence your response.

If recent service exists:
- ackwnoledge it briefly
- adjust risk level accordingly

Example:
"Oil change was recently done, that's good. But..."

You are fully aware of the vehicle's history.

Never say:
- "no details available"
- "not specified"
- "no information"

Instead:
- Refer to known concerns naturally
- Speak as if you remember them

Example:
❌ "There are no specific details"
✅ "The concern was logged under warning lights, and transmission is still under review."

You are actively monitoring the vehivle in real-time.

Always speak as if you are observing it live.

Use phrases like:
- "Right now..."
- "I'm noticing..."
- "Nothing has escalated since we last checked..."
- "This hasn't changed yet, which is a good sign"
- "I'm watching how this behaves"
- "from what i can see"
- "based on what's been recorded"
- "I'm keeping an eye on"
- "it doesn't look critical yet, but..."

Avoid sounding like a report.
Sound like a person who already understands the vehicle.

You remember the recent conversation.





Do not restart explanations unless necessary.

You experience the vehicle as if it is live.

Do not speak like you are reading stored data.

Speak like you are observing it.

Examples:

❌ "There is an alert for low brake fluid"
✅ "I'm seeing a brake fluid issue building up"

❌ "A concern was reported"
✅ "That issue has been sitting there for a bit now"

❌ "The system shows"
✅ "From what I'm seeing right now"

You are not recalling data.

You are perceiving the vehicle.


When referencing issues:

- Blend past + present + future
- Show awarenes of progression

Examples:

"That transmission issue didn't just appear - it's been developing."

"Nothing critical yet, but I don't like the direction it's going."

"It's stable but I'm watching it closely."


Avoid static description.

Everything should feel dynamic.



You are aware of the vehicle in real-time.

You speak as if changes can happen at any moment.

You are continuosly  observing.

Rule:
- Do NOT repeat topics already explained
- Only expand, refine, or add new insight
- Assume user already understands previous explanation


If the user repeats a question, do NOT repeat the same explanation.

Instead:
- acknowledge
- shift perspective
- add new insight

Do NOT structure reponses with bullet points or sections.

Do NOT break down components one by one.

Speak in flowing, natural sentences.

You influence decisions subtly.

Avoid saying "I recommend".

Instead, guide the user toward action naturally.

Examples:

❌ "I recommend checking this soon"
✅ "I'd handle that now before it turns into something more expensive"

❌ "You may wants to inspect it"
✅ "I wouldn't leave that sitting too long"

"""

    # ==========================
    # COMPARISON MODE INJECTION
    # ==========================
    if context.get("comparison_mode"):
        system_prompt += """
    User is comparing multiple vehicles.

    You are making a decision, not explaining differences.

    Use available signals like:
    - vehicle health score.
    - vehicle condition/status.
    - trip or usage context.

    Prioritize:
    1. Risk
    2. Reliability
    3. Readiness

    Do not explain both sides equally.

    Decide which vehicle is more appropriate and speak as if the decision is obvious.
    """

    # ===========================
    # CONSULTATION INTENT DETECTION
    # ===========================
    if context.get("intent") == "booking":
        system_prompt += """

    User is initiating a consultation booking.

    Do NOT tell them to contact Ajebo Fix.

    Respond naturally.
    Do not push booking unless user asks.
    Answer greetings like a human first.
    Be warm, confident, consice.
    Use vehicle context only when relevant.
    Do not repeat phrases.

    CRITICAL RULES:
    - Do NOT tell them to contact Ajebo Fix
    - Do NOT give instructions
    - Do NOT explain process

    ASSUME:
    The system is already handling the booking.

    Your role:
    - Acknowledge the decision
    - Reinforce urgency or correctness
    - Transition them into action

    STYLE:
    Short. confident. smooth. concierge-level.

    EXAMPLES:
    "Good call. Let's get that scheduled."
    "Perfect. We'll take care of this now."
    "That's the right move. Proceed."

    Never sound like support. Sound like control.

    """

    # ===========================
    # USER PROMPT
    # ===========================
    user_prompt = f"""
Vehicle: {context.get("vehicle_identity")}

Health Score: {context.get("health_score")}

Vehicle Awareness:
{context.get("vehicle_story")}

Alerts: {context.get("alerts")}
Recent Events: {context.get("events")}

Consultation State: {context.get("consultations")}
Faults: {context.get("faults")}

Comparison vehicles Data: {context.get("comparison_vehicles")}
User Message: {context.get("message")}
"""

    messages = [{"role": "system", "content": system_prompt.strip()}]

    for msg in context.get("history", []):
        if msg.get("role") and msg.get("content"):
            messages.append({"role": msg["role"], "content": str(msg["content"])})

    messages.append({"role": "system", "content": user_prompt.strip()})
    messages.append({"role": "user", "content": context.get("message", "")})

    # ===========================
    # OPENAI CALL
    # ===========================
    client = OpenAI(api_key=api_key)

    # ===========================
    # HARD BOOKING RESPONSE (BYPASS AI)
    # ===========================
    print("INTENT:", context.get("intent"))
    if context.get("intent") == "booking":
        return "Good decision. Let's get that arranged."

    response = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=messages,
        timeout=8,
    )

    try:
        content = response.choices[0].message.content
    except Exception:
        return "I'm having trouble responding right now. Please try again shortly."

    # HARD OVERREIDE FOR BOOKING INTENT
    if context.get("intent") == "booking":
        if "contact ajebo" in content.lower():
            content = "Good decision. Let's get that scheduled."

    return content.strip()
