def calculate_assessment_risk(assessment):

    score = 0
    systems = [
        assessment.engine_status,
        assessment.transmission_status,
        assessment.suspension_status,
        assessment.electrical_status,
        assessment.cooling_status,
    ]

    for system in systems:

        if system == "critical":
            score += 30

        elif system == "attention":
            score += 15

        elif system == "monitoring":
            score += 5

    risk_count = len(assessment.risks)

    score += risk_count * 10

    if score >= 80:
        level = "critical"
        label = "HIGH RISK"

    elif score >= 40:
        level = "attention"
        label = "MODERATE RISK"

    else:
        level = "healthy"
        label = "LOW RISK"

    return {
        "score": score,
        "level": level,
        "label": label,
    }
