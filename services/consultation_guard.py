from models import Consultation


def get_active_consultation(car_id):
    return (
        Consultation.query.filter_by(car_id=car_id, status="in_progress")
        .order_by(Consultation.started_at.desc())
        .first()
    )


def require_active_consultation(car_id):
    consultation = get_active_consultation(car_id)
    if not consultation:
        raise PermissionError("All vehicle care begins with a private consultation.")
    return consultation
