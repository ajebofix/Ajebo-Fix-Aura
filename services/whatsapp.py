# services/whatsapp.py

import requests
import os

from dotenv import load_dotenv

load_dotenv()


WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")


# =================================
# SEND BOOKING CONFIRMATION
# ================================
def send_booking_confirmation(phone, name, vehicle):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "template",
        "template": {
            "name": "booking_confirmation",
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": name},
                        {"type": "text", "text": vehicle},
                    ],
                }
            ],
        },
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        print("WHATSAPP FAILED")
        print("Status Code:", response.status_code)
        print("Response:", response.text)
    else:
        print("WHATSAPP SENT")
        print("Response:", response.json())
        print("SENDING TO:", phone)
        print("NAME:", name)
        print("VEHICLE:", vehicle)

    return response.json()


# =================================
# NOTIFY ADMIN NEW BOOKING
# ================================
def notify_admin_new_booking(user, vehicle, time):
    print("Sending admin booking alert...")
    result = send_template_admin(user, vehicle, time)
    print("ADMIN TEMPLATE SENT:", result)
    return result


# =================================
# ADMIN TEXT ALERT
# ================================
def send_text_admin(user, vehicle, time):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": "2347074490640",
        "type": "text",
        "text": {
            "body": f"""NEW Booking

Client: {user}
Vehicle: {vehicle}
Time: {time}
"""
        },
    }

    response = requests.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        raise Exception(response.text)

    return response.json()


# =================================
# ADMIN TEMPLATE ALERT
# ================================
def send_template_admin(user, vehicle, time):
    url = f"https://graph.facebook.com/v18.0/{PHONE_NUMBER_ID}/messages"

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": "2347074490640",
        "type": "template",
        "template": {
            "name": "admin_booking_alert_v1",
            "language": {"code": "en_US"},
            "components": [
                {
                    "type": "body",
                    "parameters": [
                        {"type": "text", "text": user},
                        {"type": "text", "text": vehicle},
                        {"type": "text", "text": str(time)}
                    ],
                },
            ],
        },
    }
    print(payload)

    response = requests.post(url, headers=headers, json=payload)

    print("ADMIN STATUS:", response.status_code)
    print("ADMIN RESPONSE:", response.text)

    return response.json()
