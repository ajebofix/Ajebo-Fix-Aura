# from flask import Blueprint, jsonify
# from flask_login import login_required, current_user
# from datetime import datetime

# from models import Car, CarOwnership, VehicleEvent

# # IMPORTANT:
# # This blueprint is NOT chat.
# # It is insights/explainability ONLY.
# rina_insights_bp = Blueprint("rina_insights", __name__, url_prefix="/rina/insights")


# # =====================================================
# # INTERNAL — DETERMINISTIC ANALYSIS (NO CHAT)
# # =====================================================


# def analyze_car_health(car_id: int, ownership_id: int):
#     """
#     Deterministic, rule-based insights.
#     Facts only. No diagnosis. No AI conversation.
#     """

#     insights = []

#     car = Car.query.get(car_id)
#     ownership = CarOwnership.query.get(ownership_id)

#     if not car or not ownership:
#         return [
#             {
#                 "type": "error",
#                 "message": "Insufficient vehicle data to generate insights.",
#             }
#         ]

#     events = (
#         VehicleEvent.query.filter_by(
#             car_id=car_id,
#             ownership_id=ownership_id,
#             is_deleted=False,
#         )
#         .order_by(VehicleEvent.created_at.desc())
#         .all()
#     )

#     # -----------------------------
#     # No history
#     # -----------------------------
#     if not events:
#         return [
#             {
#                 "type": "warning",
#                 "title": "No Maintenance History",
#                 "message": "No service or fault events have been recorded.",
#                 "confidence": "high",
#                 "recommendation": "Log a service or inspection to establish baseline data.",
#             }
#         ]

#     # -----------------------------
#     # Maintenance
#     # -----------------------------
#     last_service = next((e for e in events if e.event_type == "service"), None)

#     if last_service and last_service.mileage:
#         mileage_gap = car.current_mileage - last_service.mileage

#         if mileage_gap >= 5000:
#             insights.append(
#                 {
#                     "type": "maintenance",
#                     "title": "Service Likely Overdue",
#                     "message": (
#                         f"Last service was at {last_service.mileage} km. "
#                         f"Current mileage is {car.current_mileage} km."
#                     ),
#                     "confidence": "high",
#                     "recommendation": "Schedule engine oil and filter service.",
#                 }
#             )
#     else:
#         insights.append(
#             {
#                 "type": "maintenance",
#                 "title": "No Service Record Found",
#                 "message": "No service history exists for this vehicle.",
#                 "confidence": "medium",
#                 "recommendation": "Perform a full inspection.",
#             }
#         )

#     # -----------------------------
#     # Faults
#     # -----------------------------
#     fault_events = [e for e in events if e.event_type == "fault"]

#     if len(fault_events) >= 3:
#         insights.append(
#             {
#                 "type": "risk",
#                 "title": "Repeated Fault Events",
#                 "message": f"{len(fault_events)} fault-related events recorded.",
#                 "confidence": "medium",
#                 "recommendation": "Run diagnostic scan to identify recurring issues.",
#             }
#         )

#     return insights


# # =====================================================
# # USER — INSIGHTS
# # =====================================================


# @rina_insights_bp.route("/cars/<int:car_id>", methods=["GET"])
# @login_required
# def user_car_insights(car_id: int):

#     ownership = CarOwnership.query.filter_by(
#         car_id=car_id,
#         user_id=current_user.id,
#         is_active=True,
#     ).first_or_404()

#     insights = analyze_car_health(car_id, ownership.id)

#     return (
#         jsonify(
#             {
#                 "car_id": car_id,
#                 "generated_at": datetime.utcnow().isoformat(),
#                 "insights": insights,
#             }
#         ),
#         200,
#     )


# # =====================================================
# # ADMIN — INSIGHTS
# # =====================================================


# @rina_insights_bp.route("/admin/cars/<int:car_id>", methods=["GET"])
# @login_required
# def admin_car_insights(car_id: int):

#     if not getattr(current_user, "is_admin", False):
#         return jsonify({"error": "Admin access required"}), 403

#     ownership = CarOwnership.query.filter_by(
#         car_id=car_id,
#         is_active=True,
#     ).first_or_404()

#     insights = analyze_car_health(car_id, ownership.id)

#     return (
#         jsonify(
#             {
#                 "car_id": car_id,
#                 "admin_view": True,
#                 "generated_at": datetime.utcnow().isoformat(),
#                 "insights": insights,
#             }
#         ),
#         200,
#     )
