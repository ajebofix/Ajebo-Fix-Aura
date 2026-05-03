# services/rina_chat_engine.py

from typing import Dict, List, Optional

from rina.memory import update_user_behavior, get_user_behavior_profile
from rina.ai_brain import generate_rina_response
from models import CarOwnership, CarDriver, User


class RinaChatEngine:
    """
    Aura Assistant — Clinical Mode (ENHANCED + AI)

    - Context aware
    - Behavior-aware
    - Urgency-aware
    - AI-enhanced responses
    """

    @staticmethod
    def respond(user_id: int, car_id: int, message: str, context: Dict) -> str:

        message = (message or "").lower().strip()

        # =========================
        # VEHICLE IDENTITY
        # =========================
        vehicle_identity: str = context.get("vehicle_identity") or ""
        if not vehicle_identity:
            return (
                "I need to know which vehicle we’re discussing before I can help.\n\n"
                "Please select a vehicle from your dashboard."
            )

        # =========================
        # SCORE
        # =========================
        score: Optional[int] = None

        if isinstance(context.get("health_score"), (int, float)):
            score = int(context["health_score"])
        elif isinstance(context.get("score"), (int, float)):
            score = int(context["score"])

        score_text = str(score) if score is not None else "unavailable"

        # =========================
        # CORE CONTEXT
        # =========================
        alerts = context.get("alerts", [])
        events = context.get("events", [])
        consultations = context.get("consultations", {})
        admin_summary = context.get("admin_summary", {})

        # ACTIVE SESSION OVERRIDE
        if consultations.get("active", 0) > 0:
            context["tone"] = "operational"

        # =========================
        # CORE HEALTH DATA
        # =========================
        status: str = context.get("health_status", "unknown")
        reasons: List[str] = context.get("risk_reasons") or []

        # =========================
        # SYSTEM CONTEXT
        # =========================
        guidance = context.get("guidance") or {}
        care_context = context.get("care_context") or {}
        escalation = context.get("escalation") or {}

        escalation_level = escalation.get("level", "monitor")

        # =========================
        # SITUATION AWARENESS
        # =========================
        situation = "normal"

        if escalation_level == "critical":
            situation = "high_risk"
        elif alerts:
            situation = "watch"
        elif not alerts and score is not None and score > 80:
            situation = "stable"

        context["situation"] = situation

        # =========================
        # EVENT AWARENESS (TREND DETECTION)
        # =========================
        recent_critical_events = [
            e for e in events if e.get("severity") in ["critical", "high"]
        ]

        if recent_critical_events and situation != "high_risk":
            situation = "watch"
            context["situation"] = situation

        # =========================
        # SYSTEM PRESSURE (ADMIN INTELLIGENCE)
        # =========================
        system_pressure = "normal"

        if admin_summary.get("vehicles_requiring_attention", 0) > 5:
            system_pressure = "high_load"

        context["system_pressure"] = system_pressure

        # =========================
        # TIMELINE CONTROL SYSTEM
        # =========================

        if escalation_level == "critical":
            timeline = "immediately"

        elif status == "attention":
            timeline = "within 48 hours"

        elif status == "monitor":
            timeline = "within 7 days"

        else:
            timeline = "routine schedule"

        context["time_awareness"] = "live"

        high_risk_keywords = [
            "suspension low",
            "car leaning",
            "airmatic",
            "brake failure",
        ]

        if any(k in message for k in high_risk_keywords):
            timeline = "immediately"

        # =========================
        # INTENT DETECTION
        # =========================
        intent = context.get("intent") or "unknown"

        if intent == "general":

            if any(
                k in message for k in ["can i drive", "is it safe", "should i drive"]
            ):
                intent = "risk_check"

            elif any(k in message for k in ["what is wrong", "what caused", "why"]):
                intent = "understanding"

            elif any(k in message for k in ["what should i do", "next step"]):
                intent = "decision"

            elif any(k in message for k in ["book", "check", "inspection"]):
                intent = "conversion"

            elif any(k in message for k in ["score", "rating"]):
                intent = "score"

        # =========================
        # URGENCY DETECTION
        # =========================
        urgency_keywords = ["now", "urgent", "asap", "immediately"]
        hesitation_keywords = ["maybe", "not sure", "later", "thinking"]

        urgency_state = "neutral"

        if any(k in message for k in urgency_keywords):
            urgency_state = "urgent"

        elif any(k in message for k in hesitation_keywords):
            urgency_state = "hesitant"

        # =========================
        # MEMORY TRACKING
        # =========================
        update_user_behavior(intent)
        behavior = get_user_behavior_profile()

        dominant_behavior = max(behavior, key=behavior.get) if behavior else "neutral"

        # ========================
        # ROLE CONTEXT
        # ========================
        role = get_user_role_context(user_id, car_id)

        tone = context.get("tone")  # preserve override

        if not tone:
            if role == "driver":
                tone = "operational"
            elif role == "owner":
                tone = "decision"
            elif role == "admin":
                tone = "command"
            else:
                tone = "neutral"

        # lock it in
        context["tone"] = tone

        # =========================
        # BUILD AI CONTEXT
        # =========================

        context.update(
            {
                "vehicle": vehicle_identity,
                "score": score_text,
                "status": status,
                "reasons": "\n".join(reasons) if reasons else "None",
                "guidance": guidance.get("summary", ""),
                "care_context": care_context,
                "escalation": escalation_level,
                "intent": intent,
                "urgency": urgency_state,
                "message": message,
                "behavior": dominant_behavior,
                "timeline": timeline,
                "role": role,
                "tone": tone,
            }
        )

        # =========================
        # AI RESPONSE (DEBUG)
        # =========================
        print("\n====== RINA DEBUG ======")
        print("Vehicle:", context.get("vehicle_identity"))
        print("Score:", context.get("health_score"))
        print("Alerts:", context.get("alerts"))
        print("Events:", context.get("events"))
        print("Consultations:", context.get("consultations"))
        print("Situation:", context.get("situation"))
        print("Tone:", context.get("tone"))
        print("Intent:", context.get("intent"))
        print("Urgency:", context.get("urgency"))
        print("Timeline:", context.get("timeline"))
        print("Message:", context.get("message"))
        print("Role:", context.get("role"))
        print("Context:", context)
        print("============================\n")

        # =========================
        # AI RESPONSE (PRIMARY)
        # =========================
        try:
            ai_response = generate_rina_response(context)
            if ai_response:
                return ai_response
        except Exception as e:
            print("AI ERROR:", e)

        # =========================
        # FALLBACK RESPONSE (SAFE)
        # =========================
        if status == "critical":
            return (
                f"{vehicle_identity} is currently under elevated observation.\n\n"
                "A professional review by Ajebo Fix is advised."
            )

        if status == "attention":
            return (
                f"{vehicle_identity} is under observation (score: {score_text}).\n\n"
                "A scheduled review is recommended."
            )

        if status == "healthy":
            return (
                f"{vehicle_identity} is currently stable (score: {score_text}).\n\n"
                "Routine monitoring remains appropriate."
            )

        return (
            f"I can help you understand {vehicle_identity}.\n\n"
            "Ask about score, observations, or next steps."
        )


# =================================
# RINA GETS USER ROLE CONTEXT
# =================================


def get_user_role_context(user_id, car_id):
    user = User.query.get(user_id)

    if user and user.role == "admin":
        return "admin"

    is_owner = CarOwnership.query.filter_by(
        user_id=user_id, car_id=car_id, is_active=True
    ).first()

    if is_owner:
        return "owner"

    is_driver = CarDriver.query.filter_by(
        user_id=user_id, car_id=car_id, is_active=True
    ).first()

    if is_driver:
        return "driver"

    return "unknown"
