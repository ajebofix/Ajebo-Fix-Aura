# rina/memory.py


def build_vehicle_context(events=None):
    """
    Builds lightweight vehicle context from past events.

    Safe fallback if no data is provided.
    """

    context = {"recent_events": [], "systems_flagged": set(), "known_risks": []}

    if not events:
        return context

    for event in events[-5:]:  # last 5 events only
        context["recent_events"].append(
            {
                "type": getattr(event, "event_type", None),
                "title": getattr(event, "title", None),
                "severity": getattr(event, "severity", None),
            }
        )

        # Basic system detection (you can expand later)
        title = (getattr(event, "title", "") or "").lower()

        if "cooling" in title or "overheat" in title:
            context["systems_flagged"].add("cooling")

        if "battery" in title or "electrical" in title:
            context["systems_flagged"].add("electrical")

        if "suspension" in title or "steering" in title:
            context["systems_flagged"].add("suspension")

        if getattr(event, "event_type", None) == "prediction":
            context["known_risks"].append(title)

    return context


# ============================================
# USER BEHAVIOR MEMORY (LIGHTWEIGHT)
# ============================================

_user_behavior = {
    "risk_check": 0,
    "understanding": 0,
    "decision": 0,
    "conversion": 0,
}


def update_user_behavior(intent: str):
    """
    Tracks what the user tends to ask.
    Lightweight in-memory tracker.
    Safe fallback (no DB).
    """

    if intent in _user_behavior:
        _user_behavior[intent] += 1


def get_user_behavior_profile() -> dict:
    """
    Returns user behavioral tendencies.
    """

    total = sum(_user_behavior.values()) or 1

    return {
        "risk_focus": _user_behavior["risk_check"] / total,
        "learning_focus": _user_behavior["understanding"] / total,
        "decision_ready": _user_behavior["decision"] / total,
        "conversion_ready": _user_behavior["conversion"] / total,
    }
