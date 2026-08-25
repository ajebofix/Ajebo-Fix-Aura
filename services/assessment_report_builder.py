from datetime import datetime
from services.assessment_risk_engine import calculate_assessment_risk
from models import CarOwnership
from models import User
from models_assessment_addendum import VehicleAssessmentAddendum

# =====================================================
# VEHICLE ASSESSMENT REPORT BUILDER
# =====================================================
# IMPORTANT:
# - Read-only builder
# - NO database writes
# - NO HTML
# - NO PDF
# - NO pricing
# - NO diagnosis
# - Finalized assessment content is never rewritten by addenda
# =====================================================


class AssessmentNotFinalizedError(Exception):
    pass


def _advisor_display(user):
    if not user:
        return None
    return (
        getattr(user, "full_name", None)
        or getattr(user, "name", None)
        or getattr(user, "email", None)
    )


def build_assessment_report(*, assessment):
    """Build the owner-safe report for one finalized VehicleAssessment.

    Client-visible corrections/addenda are appended after the original report.
    Advisor/internal addenda are deliberately excluded from this shared report
    surface so owner authorization cannot expose restricted professional notes.
    """

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

    title_page = {
        "powered_by": "Ajebo Fix",
        "issued_date": assessment.finalized_at,
        "vehicle_vin": car.vin,
        "engine_number": getattr(car, "engine_number", None),
        "current_mileage": car.current_mileage,
    }

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

    current_health_status = {
        "engine_system": assessment.engine_status,
        "transmission_system": assessment.transmission_status,
        "suspension_and_steering": assessment.suspension_status,
        "electrical_and_controls": assessment.electrical_status,
        "cooling_and_lubrication": assessment.cooling_status,
    }

    identified_risks = []
    for item in getattr(assessment, "risks", []) or []:
        identified_risks.append(
            {
                "description": item.description,
                "likely_cause": item.likely_cause,
                "potential_consequence": item.consequence_if_ignored,
            }
        )

    immediate = []
    monitoring = []
    preventive = []

    for item in getattr(assessment, "risks", []) or []:
        if item.urgency == "immediate":
            immediate.append(item.description)
        elif item.urgency == "monitoring":
            monitoring.append(item.description)
        elif item.urgency == "preventive":
            preventive.append(item.description)

    urgency_classification = {
        "immediate_attention": immediate,
        "monitoring_closely": monitoring,
        "preventive_recommendations": preventive,
    }

    cost_vs_consequence = {
        "summary": assessment.cost_consequence_analysis
    }

    treatment_paths = []
    for option in getattr(assessment, "treatment_options", []) or []:
        treatment_paths.append(
            {
                "option_code": option.option_code,
                "title": option.title,
                "description": option.description,
            }
        )

    professional_recommendation = {
        "statement": getattr(assessment, "professional_recommendation", None),
        "recommended_option": getattr(assessment, "recommended_option", None),
        "advisor": _advisor_display(advisor),
    }

    addenda = []
    client_addenda = (
        VehicleAssessmentAddendum.query.filter_by(
            assessment_id=assessment.id,
            visibility="client",
        )
        .order_by(
            VehicleAssessmentAddendum.created_at.asc(),
            VehicleAssessmentAddendum.id.asc(),
        )
        .all()
    )
    for addendum in client_addenda:
        addenda.append(
            {
                "id": addendum.id,
                "category": addendum.category,
                "reason": addendum.reason,
                "statement": addendum.client_text,
                "created_at": addendum.created_at,
                "advisor": _advisor_display(addendum.creator),
            }
        )

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
        "addenda": addenda,
    }
