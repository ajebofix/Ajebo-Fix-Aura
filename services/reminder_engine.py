from datetime import datetime, timedelta
from models import BookingIntent, User
from services.whatsapp import send_whatsapp_reminder


def check_abandoned_bookings():
    pending = BookingIntent.query.filter(
        BookingIntent.completed == False,
        BookingIntent.started_at < datetime.utcnow() - timedelta(minutes=10),
    ).all()

    for intent in pending:
        user = User.query.get(intent.user_id)

        if not user:
            continue

        phone = user.phone_number.replace("+", "").strip()

        send_whatsapp_reminder(
            phone=phone,
            message="Hey, you started booking a consultation but didn't finish. Need help?",
        )
