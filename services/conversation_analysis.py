# services/conversation_analysis.py


def analyze_conversation(message: str):

    text = (message or "").lower()

    # =========================
    # DEFAULTS
    # =========================
    urgency = "low"
    emotion = "calm"
    action = "monitor"
    summary = message[:200]

    # =========================
    # URGENCY DETECTION
    # =========================
    urgent_words = [
        "urgent",
        "asap",
        "immediately",
        "now",
        "danger",
        "smoke",
        "burning",
        "brake failed",
        "won't start",
    ]

    if any(w in text for w in urgent_words):
        urgency = "high"

    elif any(
        w in text
        for w in [
            "warning light",
            "noise",
            "vibration",
            "leak",
            "jerking",
        ]
    ):
        urgency = "medium"

    # =========================
    # EMOTIONAL STATE
    # =========================
    if any(w in text for w in ["worried", "scared", "concerned", "afraid"]):
        emotion = "anxious"

    elif any(w in text for w in ["angry", "frustrated", "upset"]):
        emotion = "frustrated"

    elif urgency == "high":
        emotion = "distressed"

    # =========================
    # RECOMMENDED ACTION
    # =========================
    if urgency == "high":
        action = "priority_review"

    elif urgency == "medium":
        action = "schedule_inspection"

    # =========================
    # SUMMARY GENERATION
    # =========================
    summary = f"Client reported: {message[:150]}"

    return {
        "summary": summary,
        "urgency": urgency,
        "emotion": emotion,
        "action": action,
    }
