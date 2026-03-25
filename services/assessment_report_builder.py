from datetime import datetime
from services.assessment_risk_engine import calculate_assessment_risk
from models import CarOwnership
from models import User

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

    ownership = CarOwnership.query.filter_by(
        car_id=assessment.car_id,
        is_active=True,
    ).first()

    consultation = assessment.consultation

    advisor = None

    if assessment.finalized_by:
        advisor = User.query.get(assessment.finalized_by)

    risk = calculate_assessment_risk(assessment)

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
        "transmission": getattr(car, "transmission", None),
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
        "engine_system": assessment.engine_status,
        "transmission_system": assessment.transmission_status,
        "suspension_and_steering": assessment.suspension_status,
        "electrical_and_controls": assessment.electrical_status,
        "cooling_and_lubrication": assessment.cooling_status,
    }

    # -------------------------------------------------
    # SECTION 3 — IDENTIFIED RISKS
    # -------------------------------------------------
    identified_risks = []

    for item in getattr(assessment, "risks", []) or []:
        identified_risks.append(
            {
                "description": item.description,
                "likely_cause": item.likely_cause,
                "potential_consequence": item.consequence_if_ignored,
            }
        )
    # -------------------------------------------------
    # SECTION 4 — URGENCY CLASSIFICATION
    # -------------------------------------------------
    immediate = []
    monitoring = []
    preventive = []

    for risk in getattr(assessment, "risks", []) or []:

        if risk.urgency == "immediate":
            immediate.append(risk.description)

        elif risk.urgency == "monitoring":
            monitoring.append(risk.description)

        elif risk.urgency == "preventive":
            preventive.append(risk.description)

    urgency_classification = {
        "immediate_attention": immediate,
        "monitoring_closely": monitoring,
        "preventive_recommendations": preventive,
    }

    # -------------------------------------------------
    # SECTION 5 — COST VS CONSEQUENCE (LOGIC FRAME)
    # -------------------------------------------------
    cost_vs_consequence = {
        "summary": assessment.cost_consequence_analysis
    }

    # -------------------------------------------------
    # SECTION 6 — RECOMMENDED TREATMENT PATHS
    # -------------------------------------------------
    treatment_paths = []

    for option in getattr(assessment, "treatment_options", []) or []:
        treatment_paths.append(
            {
                "option_code": option.option_code,
                "title": option.title,
                "description": option.description,
            }
        )

    # -------------------------------------------------
    # SECTION 7 — PROFESSIONAL RECOMMENDATION
    # -------------------------------------------------
    professional_recommendation = {
        "statement": getattr(assessment, "professional_recommendation", None),
        "recommended_option": getattr(assessment, "recommended_option", None),
        "advisor": (
            getattr(advisor, "full_name", None)
            or getattr(advisor, "name", None)
            or getattr(advisor, "email", None)
            if advisor
            else None
        ),
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
        "risk": risk,
        "identified_risks": identified_risks,
        "urgency_classification": urgency_classification,
        "cost_vs_consequence": cost_vs_consequence,
        "treatment_paths": treatment_paths,
        "professional_recommendation": professional_recommendation,
    }
