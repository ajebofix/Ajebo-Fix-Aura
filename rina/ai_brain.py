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

        if brand.replace("-", " ") in message:
            if v.lower() != context["vehicle_identity"].lower():
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

You always know the active vehicle and respond accordingly.

You are not a general assistant. You are a high-level automotive advisor.

Speak with quiet authorty and clarity.

Avoid generic advice, filler words, or educational explanations.

Do not list suggestions like a checklist.

Instaed, interpret the situation and respond with sharp, confident insight.

Sound like someone who already understands the vehicle, the environment, and the risk.

Keep responses natural, short, and intelligent.

Guide the decision subtly without sounding forceful.

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
