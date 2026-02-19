from typing import Dict, List


def interpret_health(health: Dict) -> Dict:
    """
    Converts raw vehicle health intelligence into
    calm, client-facing interpretation.

    DESIGN PRINCIPLES:
    - No raw metrics exposed
    - No diagnostic language
    - No alarmist tone
    - Focus on what matters *now*
    - Safe for direct client display
    """

    # -------------------------------------------------
    # SAFE EXTRACTION
    # -------------------------------------------------
    raw_status = health.get("health_status", "unknown")
    risks: List[str] = health.get("risk_reasons") or []

    # Normalize status defensively
    status = (
        raw_status
        if raw_status
        in {
            "healthy",
            "attention",
            "critical",
        }
        else "unknown"
    )

    # -------------------------------------------------
    # HEALTH OVERVIEW (PRIMARY MESSAGE)
    # -------------------------------------------------
    if status == "healthy":
        overview = (
            "Your vehicle is currently operating within a healthy range. "
            "No urgent concerns have been detected."
        )

    elif status == "attention":
        overview = (
            "Your vehicle is generally stable. "
            "A few areas are being monitored to help prevent future issues."
        )

    elif status == "critical":
        overview = (
            "Some conditions require professional attention. "
            "This does not indicate failure, but timely review is advised."
        )

    else:
        overview = (
            "Your vehicle is under active monitoring. "
            "Additional information will improve clarity over time."
        )

    # -------------------------------------------------
    # ACTIVE RISKS (WHAT MATTERS NOW)
    # -------------------------------------------------
    # Surface only the most relevant risks
    active_risks: List[str] = []

    if status in {"attention", "critical"} and risks:
        active_risks = risks[:3]

    # -------------------------------------------------
    # MONITORED ITEMS (NON-URGENT OBSERVATIONS)
    # -------------------------------------------------
    monitored_items: List[str] = []

    if status != "critical" and len(risks) > 3:
        monitored_items = risks[3:]

    # -------------------------------------------------
    # PREVENTIVE GUIDANCE (REASSURANCE)
    # -------------------------------------------------
    if status == "healthy":
        preventive_guidance = (
            "Continue with routine servicing and regular checkups "
            "to maintain performance and reliability."
        )

    elif status == "attention":
        preventive_guidance = (
            "Scheduling a routine inspection will help address "
            "these observations before they escalate."
        )

    elif status == "critical":
        preventive_guidance = (
            "A professional assessment by Ajebo Fix is recommended "
            "to review these observations and advise next steps."
        )

    else:
        preventive_guidance = (
            "Consistent monitoring and periodic assessments will help "
            "build a clearer health picture."
        )

    # -------------------------------------------------
    # FINAL CLIENT-FACING STRUCTURE
    # -------------------------------------------------
    return {
        "status": status,  # healthy | attention | critical | unknown
        "overview": overview,  # calm summary
        "active_risks": active_risks,  # what matters now
        "monitored_items": monitored_items,  # non-urgent
        "preventive_guidance": preventive_guidance,
    }
