def interpret_event(event):
    """
    AURA — Rina Interpretation Engine (Clinical Mode)
    Day 1: Language & Authority Reset
    """

    # -------------------------
    # Tone control (calm first)
    # -------------------------
    if event.severity in ["critical", "high"]:
        tone = "attentive"
    else:
        tone = "calm"

    # -------------------------
    # Interpretation logic
    # -------------------------
    if event.event_type == "prediction":
        message = (
            f"A potential risk pattern has been observed: {event.title}. "
            "This does not indicate a fault, but it may warrant review during your next consultation."
        )

    elif event.event_type == "reported_concern":
        message = (
            f"A reported concern has been logged: {event.title}. "
            "Our team will assess this as part of your vehicle’s ongoing care."
        )

    elif event.event_type == "treatment_record":
        message = (
            f"A treatment record has been added: {event.title}. "
            "This contributes to your vehicle’s long-term health profile."
        )

    elif event.event_type == "monitoring":
        message = (
            f"This item is currently under monitoring: {event.title}. "
            "No immediate action is required unless advised."
        )

    else:
        message = (
            event.description
            or "An update has been recorded in your vehicle’s health file."
        )

    # -------------------------
    # Final output (controlled)
    # -------------------------
    return {
        "tone": tone,
        "message": message,
        "disclaimer": (
            "This insight is informational only and does not constitute a diagnosis "
            "or instruction. All decisions are guided through Ajebo Fix consultations."
        ),
    }
