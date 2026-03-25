from typing import Dict, List, Optional

from rina.memory import update_user_behavior, get_user_behavior_profile
from rina.ai_brain import generate_rina_response


class RinaChatEngine:
    """
    Aura Assistant — Clinical Mode (ENHANCED + AI)

    - Context aware
    - Behavior-aware
    - Urgency-aware
    - AI-enhanced responses
    """

    @staticmethod
    def respond(message: str, health: Dict) -> str:

        message = (message or "").lower().strip()

        # =========================
        # VEHICLE IDENTITY
        # =========================
        vehicle_identity: str = health.get("vehicle_identity") or ""
        if not vehicle_identity:
            return (
                "I need to know which vehicle we’re discussing before I can help.\n\n"
                "Please select a vehicle from your dashboard."
            )

        # =========================
        # SCORE
        # =========================
        score: Optional[int] = None

        if isinstance(health.get("health_score"), (int, float)):
            score = int(health["health_score"])
        elif isinstance(health.get("score"), (int, float)):
            score = int(health["score"])

        score_text = str(score) if score is not None else "unavailable"

        # =========================
        # CORE HEALTH DATA
        # =========================
        status: str = health.get("health_status", "unknown")
        reasons: List[str] = health.get("risk_reasons") or []

        # =========================
        # SYSTEM CONTEXT
        # =========================
        guidance = health.get("guidance") or {}
        care_context = health.get("care_context") or {}
        escalation = health.get("escalation") or {}

        escalation_level = escalation.get("level", "monitor")

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
        intent = "unknown"

        if any(k in message for k in ["can i drive", "is it safe", "should i drive"]):
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

        # =========================
        # BUILD AI CONTEXT
        # =========================
        context = {
            "vehicle": vehicle_identity,
            "score": score_text,
            "status": status,
            "reasons": "\n".join(reasons) if reasons else "None",
            "guidance": guidance.get("summary", ""),
            "escalation": escalation_level,
            "intent": intent,
            "urgency": urgency_state,
            "message": message,
            "behavior": dominant_behavior,
            "timeline": timeline,
        }

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
