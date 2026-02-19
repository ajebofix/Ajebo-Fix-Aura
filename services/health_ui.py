def map_health_to_ui(health):
    """
    Converts internal health engine output
    into calm, human-facing UI state.
    """

    status = health.get("health_status")

    if status == "healthy":
        return {
            "ui_color": "green",
            "ui_label": "Stable",
            "reassurance_copy": (
                "No action needed right now. " "Your vehicle is in good standing."
            ),
        }

    if status in ("attention", "monitor"):
        return {
            "ui_color": "amber",
            "ui_label": "Under Observation",
            "reassurance_copy": (
                "Your vehicle is being monitored by Ajebo Fix. "
                "We’ll alert you if anything changes."
            ),
        }

    return {
        "ui_color": "red",
        "ui_label": "Needs Attention",
        "reassurance_copy": (
            "Your vehicle requires attention. " "An advisor is reviewing the situation."
        ),
    }
