from typing import Dict, List, Optional


class RinaChatEngine:
    """
    Aura Assistant — Clinical Mode (STRICT & STATELESS)

    HARD RULES:
    - Responds ONLY using the provided health context
    - NEVER infers, guesses, or reuses previous vehicle data
    - If vehicle_identity or score is missing → say so clearly
    - No diagnosis, no commands, no urgency language
    """

    @staticmethod
    def respond(message: str, health: Dict) -> str:
        # =================================================
        # INPUT NORMALIZATION
        # =================================================
        message = (message or "").lower().strip()

        # =================================================
        # VEHICLE IDENTITY (MANDATORY)
        # =================================================
        vehicle_identity: str = health.get("vehicle_identity")
        if not vehicle_identity:
            return (
                "I need to know which vehicle we’re discussing before I can help.\n\n"
                "Please select a vehicle from your dashboard."
            )

        # =================================================
        # HEALTH SCORE — HARD LOCK (NO GUESSING)
        # =================================================
        score: Optional[int] = None

        if isinstance(health.get("health_score"), (int, float)):
            score = int(health["health_score"])
        elif isinstance(health.get("score"), (int, float)):
            score = int(health["score"])

        score_text = str(score) if score is not None else "unavailable"

        # =================================================
        # HEALTH STATUS & CONTEXT
        # =================================================
        status: str = health.get("health_status", "unknown")
        label: str = health.get("label", "unavailable")
        reasons: List[str] = health.get("risk_reasons") or []

        next_action = health.get("next_action")
        if isinstance(next_action, dict):
            next_action_text = next_action.get(
                "message",
                "A professional review by Ajebo Fix is advised.",
            )
        elif isinstance(next_action, str):
            next_action_text = next_action
        else:
            next_action_text = "A professional review by Ajebo Fix is advised."

        # =================================================
        # INTENT DETECTION (SOFT, SAFE)
        # =================================================
        why_keywords = {"why", "cause", "what caused"}
        detail_keywords = {"what exactly", "what is happening", "issue", "engine"}
        drive_keywords = {"drive", "driving", "trip", "journey", "travel"}
        reassurance_keywords = {"can i", "should i", "okay to", "safe"}
        next_keywords = {"next step", "what should i do", "recommend", "recommendation"}
        score_keywords = {"score", "health score", "rating"}

        # =================================================
        # SCORE QUESTIONS (EXPLICIT & SAFE)
        # =================================================
        if any(k in message for k in score_keywords):
            if score is None:
                return (
                    f"The health score for {vehicle_identity} is currently unavailable.\n\n"
                    "This can happen if there isn’t enough recorded data yet.\n\n"
                    "Ajebo Fix can assist with a formal assessment if needed."
                )

            return (
                f"The current health score for {vehicle_identity} is {score}.\n\n"
                "This score reflects recorded usage, service history, "
                "and observed indicators — not a mechanical diagnosis."
            )

        # =================================================
        # WHY / CAUSE QUESTIONS
        # =================================================
        if any(k in message for k in why_keywords):
            if not reasons:
                return (
                    f"{vehicle_identity} is assessed using usage patterns, "
                    "service history, and recorded observations.\n\n"
                    "At this time, there are no specific monitored indicators "
                    "requiring explanation."
                )

            formatted = "\n".join(f"- {r}" for r in reasons)
            return (
                f"The current health context for {vehicle_identity} reflects:\n\n"
                f"{formatted}\n\n"
                "These are monitoring indicators — not a diagnosis. "
                "A physical inspection is required for certainty."
            )

        # =================================================
        # DETAIL QUESTIONS
        # =================================================
        if any(k in message for k in detail_keywords):
            if not reasons:
                return (
                    f"There isn’t enough recorded data to describe "
                    f"specific concerns for {vehicle_identity} yet.\n\n"
                    "Ongoing monitoring or a formal assessment will improve clarity."
                )

            formatted = "\n".join(f"- {r}" for r in reasons)
            return (
                f"Here’s what is currently being observed for {vehicle_identity}:\n\n"
                f"{formatted}\n\n"
                "These observations do not confirm a fault."
            )

        # =================================================
        # DRIVING / USAGE QUESTIONS
        # =================================================
        if any(k in message for k in drive_keywords):
            if status == "critical":
                return (
                    f"{vehicle_identity} is currently under elevated observation "
                    f"(health score: {score_text}).\n\n"
                    "Extended use may increase exposure to avoidable issues.\n\n"
                    "A professional review by Ajebo Fix is advised before prolonged use."
                )

            return (
                f"No elevated risk signals are currently present for {vehicle_identity}.\n\n"
                "Routine monitoring and professional oversight remain recommended."
            )

        # =================================================
        # NEXT STEP QUESTIONS
        # =================================================
        if any(k in message for k in next_keywords):
            return (
                f"Based on the current profile for {vehicle_identity}, "
                "the following step is advised:\n\n"
                f"{next_action_text}\n\n"
                "Ajebo Fix can assist whenever you’re ready."
            )

        # =================================================
        # REASSURANCE QUESTIONS
        # =================================================
        if any(k in message for k in reassurance_keywords):
            return (
                f"{vehicle_identity} is currently assessed as {status} "
                f"(health score: {score_text}).\n\n"
                "No immediate action is enforced. "
                "A professional assessment provides the highest confidence."
            )

        # =================================================
        # STATUS-BASED CONTEXT (DEFAULT)
        # =================================================
        if status == "critical":
            context = (
                "\n".join(f"- {r}" for r in reasons)
                if reasons
                else "- Multiple elevated monitoring indicators are present."
            )
            return (
                f"{vehicle_identity} is currently under elevated observation.\n\n"
                f"{context}\n\n"
                "This is not a diagnosis. "
                "Ajebo Fix can review and advise appropriately."
            )

        if status == "attention":
            return (
                f"{vehicle_identity} is being actively monitored "
                f"(health score: {score_text}).\n\n"
                "No immediate concern, though follow-up is recommended.\n\n"
                f"Suggested next step:\n{next_action_text}"
            )

        if status == "healthy":
            return (
                f"{vehicle_identity} is currently within a healthy operating range "
                f"(health score: {score_text}).\n\n"
                "Continued routine care is advised."
            )

        # =================================================
        # FINAL FALLBACK (SAFE, GUIDED)
        # =================================================
        return (
            f"I can help you understand the current health context for "
            f"{vehicle_identity}.\n\n"
            "You may ask:\n"
            "- What is the health score?\n"
            "- What is being observed?\n"
            "- Is continued use advisable?\n"
            "- What is the recommended next step?"
        )
