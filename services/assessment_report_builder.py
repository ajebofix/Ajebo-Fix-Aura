from datetime import datetime

# =====================================================
# VEHICLE ASSESSMENT REPORT BUILDER
# =====================================================
# IMPORTANT:
# - Pure function
# - NO database writes
# - NO HTML
# - NO PDF
# - NO pricing
# - NO diagnosis
# =====================================================


class AssessmentNotFinalizedError(Exception):
    pass


def build_assessment_report(*, assessment):
    """
    Builds a structured professional vehicle report
    from a FINALIZED VehicleAssessment.

    INPUT:
        assessment (VehicleAssessment) — must be finalized

    OUTPUT:
        dict — canonical report structure
    """

    # -------------------------------------------------
    # Guard: Assessment must be finalized
    # -------------------------------------------------
    if not assessment.is_finalized:
        raise AssessmentNotFinalizedError(
            "Assessment must be finalized before generating report."
        )

    car = assessment.car
    ownership = assessment.ownership
    consultation = assessment.consultation
    advisor = assessment.finalized_by_user

    # -------------------------------------------------
    # SECTION 0 — TITLE PAGE
    # -------------------------------------------------
    title_page = {
        "powered_by": "Ajebo Fix",
        "issued_date": assessment.finalized_at,
        "vehicle_vin": car.vin,
        "engine_number": getattr(car, "engine_number", None),
        "current_mileage": car.current_mileage,
    }

    # -------------------------------------------------
    # SECTION 1 — VEHICLE OVERVIEW
    # -------------------------------------------------
    vehicle_overview = {
        "brand": car.brand,
        "model": car.model,
        "year": car.year,
        "engine_type": getattr(car, "engine_type", None),
        "transmission": assessment.transmission_type,
        "usage_pattern": assessment.usage_pattern,
        "ownership_duration": (
            (datetime.utcnow() - ownership.start_date).days
            if ownership and ownership.start_date
            else None
        ),
    }

    # -------------------------------------------------
    # SECTION 2 — CURRENT HEALTH STATUS
    # -------------------------------------------------
    current_health_status = {
        "engine_system": assessment.engine_health_statement,
        "transmission_system": assessment.transmission_health_statement,
        "suspension_and_steering": assessment.suspension_health_statement,
        "electrical_and_controls": assessment.electrical_health_statement,
        "cooling_and_lubrication": assessment.cooling_health_statement,
    }

    # -------------------------------------------------
    # SECTION 3 — IDENTIFIED RISKS
    # -------------------------------------------------
    identified_risks = []

    for risk in assessment.identified_risks or []:
        identified_risks.append(
            {
                "description": risk.get("description"),
                "likely_cause": risk.get("likely_cause"),
                "potential_consequence": risk.get("potential_consequence"),
            }
        )

    # -------------------------------------------------
    # SECTION 4 — URGENCY CLASSIFICATION
    # -------------------------------------------------
    urgency_classification = {
        "immediate_attention": assessment.immediate_attention_items or [],
        "monitoring_closely": assessment.monitoring_items or [],
        "preventive_recommendations": assessment.preventive_items or [],
    }

    # -------------------------------------------------
    # SECTION 5 — COST VS CONSEQUENCE (LOGIC FRAME)
    # -------------------------------------------------
    cost_vs_consequence = {
        "summary": (
            "Addressing the identified issues at this stage significantly reduces "
            "the likelihood of major component failure, extended downtime, and "
            "substantially higher repair costs in the future."
        )
    }

    # -------------------------------------------------
    # SECTION 6 — RECOMMENDED TREATMENT PATHS
    # -------------------------------------------------
    treatment_paths = {
        "option_a_conservative": assessment.treatment_option_a,
        "option_b_balanced": assessment.treatment_option_b,
        "option_c_preventive": assessment.treatment_option_c,
    }

    # -------------------------------------------------
    # SECTION 7 — PROFESSIONAL RECOMMENDATION
    # -------------------------------------------------
    professional_recommendation = {
        "statement": assessment.professional_recommendation,
        "recommended_option": assessment.recommended_option,
        "advisor": advisor.full_name if advisor else None,
    }

    # -------------------------------------------------
    # FINAL REPORT PACKAGE
    # -------------------------------------------------
    return {
        "meta": {
            "assessment_id": assessment.id,
            "consultation_id": consultation.id if consultation else None,
            "car_id": car.id,
        },
        "title_page": title_page,
        "vehicle_overview": vehicle_overview,
        "current_health_status": current_health_status,
        "identified_risks": identified_risks,
        "urgency_classification": urgency_classification,
        "cost_vs_consequence": cost_vs_consequence,
        "treatment_paths": treatment_paths,
        "professional_recommendation": professional_recommendation,
    }
