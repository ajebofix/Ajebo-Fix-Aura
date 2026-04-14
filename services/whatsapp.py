import requests
import os

from dotenv import load_dotenv

load_dotenv()


WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID")


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

    print("WHATSAPP RESPONSE:", response.json())

    return response.json()
