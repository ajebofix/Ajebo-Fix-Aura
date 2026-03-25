# rina/logic.py

from rina.memory import build_vehicle_context


def interpret_event(event, events_history=None):
    """
    Rina Interpretation Engine (Clinical Mode)
    Context-aware, safe, non-diagnostic.
    """

    # ---------------------------
    # Build memory context
    # ---------------------------
    context = build_vehicle_context(events_history)

    # ---------------------------
    # Tone control
    # ---------------------------
    if getattr(event, "severity", None) in ["critical", "high"]:
        tone = "attentive"
    else:
        tone = "calm"

    # ---------------------------
    # Context awareness
    # ---------------------------
    context_note = ""

    systems_flagged = context.get("systems_flagged", set())
    recent_events = context.get("recent_events", [])

    if recent_events:
        context_note += "Recent activity has been recorded. "

    if "cooling" in systems_flagged:
        context_note += "This aligns with previous cooling system observations. "

    elif "electrical" in systems_flagged:
        context_note += "This aligns with prior electrical system patterns. "

    elif "suspension" in systems_flagged:
        context_note += (
            "This aligns with previous suspension or steering observations. "
        )

    # ---------------------------
    # Interpretation logic
    # ---------------------------

    event_type = getattr(event, "event_type", None)
    title = getattr(event, "title", "this item")
    description = getattr(event, "description", "")

    if event_type == "prediction":
        message = (
            f"{context_note}"
            f"A potential risk pattern has been observed: {title}. "
            "This does not indicate a fault, but may warrant review during your next consultation."
        )

    elif event_type == "reported_concern":
        message = (
            f"{context_note}"
            f"A concern has been recorded: {title}. "
            "This will be reviewed as part of your vehicle’s ongoing care."
        )

    elif event_type == "treatment_record":
        message = (
            f"A service record has been added: {title}. "
            "This contributes to your vehicle’s long-term health profile."
        )

    elif event_type == "monitoring":
        message = (
            f"{context_note}"
            f"This item is currently under observation: {title}. "
            "No immediate action is required unless advised."
        )

    else:
        message = (
            f"{context_note}"
            f"{description or 'An update has been recorded in your vehicle health file.'}"
        )

    # ---------------------------
    # Final output
    # ---------------------------

    return {
        "tone": tone,
        "message": message,
        "disclaimer": (
            "This insight is informational only and does not constitute a diagnosis. "
            "All decisions are guided through Ajebo Fix consultation."
        ),
    }
