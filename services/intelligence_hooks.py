# =====================================================
# CLINICAL ASSESSMENT HOOKS
# Central invocation points for Aura vehicle assessment
# =====================================================

from typing import Optional

from models import Car, CarOwnership
from services.vehicle_intelligence import calculate_vehicle_health


# =====================================================
# CORE ASSESSMENT INVOCATION
# =====================================================


def invoke_vehicle_assessment(
    car_id: int,
    ownership_id: Optional[int] = None,
    context: str = "",
) -> dict:
    """
    Invokes a vehicle health assessment for Aura.

    This function:
    - Observes current vehicle condition
    - Interprets status calmly
    - Returns an assessment summary

    IMPORTANT:
    - This function does NOT store data
    - It does NOT trigger actions
    - It does NOT notify clients
    - It does NOT imply urgency

    It exists solely to provide a clean,
    authoritative assessment snapshot
    for advisors, records, or downstream systems.
    """

    car = Car.query.get(car_id)
    if not car:
        return {}

    if ownership_id:
        ownership = CarOwnership.query.get(ownership_id)
    else:
        ownership = CarOwnership.query.filter_by(
            car_id=car_id,
            is_active=True,
        ).first()

    if not ownership:
        return {}

    assessment = calculate_vehicle_health(car, ownership)

    # ---------------------------------
    # RETURN — NO SIDE EFFECTS
    # ---------------------------------
    # This output may later be:
    # - recorded as an assessment
    # - reviewed by an advisor
    # - summarized in a report
    # - referenced by Rina (clinical mode)
    #
    # But this function itself does NOTHING
    # beyond observation.
    # ---------------------------------

    return {
        "car_id": car.id,
        "ownership_id": ownership.id,
        "context": context,
        **assessment,
    }


# =====================================================
# BACKWARD-COMPATIBILITY ALIAS (V1)
# =====================================================

trigger_vehicle_intelligence = invoke_vehicle_assessment
