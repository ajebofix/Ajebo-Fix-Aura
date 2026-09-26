"""Verified speaker identity and vehicle-free workspace help.

Account help never retrieves vehicle records, history or provider context.
"""

from __future__ import annotations

import re

from models import User
from services.rina_authority import RinaIdentityUnavailable


def speaker_identity(user_id: int) -> dict:
    user = User.query.filter_by(id=user_id, is_active=True).first()
    if user is None:
        raise RinaIdentityUnavailable("active authenticated identity is required")
    return {
        "display_name": (user.name or "").strip()[:120] or None,
        "account_role": user.role,
    }


def identity_question(message: str) -> bool:
    clean = " ".join(re.sub(r"[^a-z0-9 ]", "", (message or "").lower()).split())
    return clean in {
        "who am i",
        "do you know who i am",
        "what is my name",
        "whats my name",
        "do you know my name",
        "what is my role",
        "whats my role",
        "am i an admin",
        "am i an administrator",
        "am i an advisor",
        "do you know im an admin",
        "do you know i am an admin",
    }


def professional_capability_question(message: str) -> bool:
    clean = " ".join(re.sub(r"[^a-z0-9 ]", "", (message or "").lower()).split())
    return clean in {
        "what can i do",
        "what can i do as an advisor",
        "what can i do as an admin",
        "what can i do as an administrator",
        "how can i use the advisor workspace",
        "how do i use the advisor workspace",
        "what can rina do for me",
        "what can you do for me",
    }


def describe_speaker(
    identity: dict,
    *,
    vehicle_name: str | None = None,
    relationships: tuple[str, ...] = (),
) -> str:
    name = identity["display_name"]
    role = identity["account_role"]
    label = {
        "admin": "an Ajebo Fix administrator with access to the Advisor Console",
        "advisor": "an Ajebo Fix advisor",
        "driver": "a driver",
        "user": "an Aura client",
    }.get(role, "an Aura user")
    reply = (
        f"You’re signed in as {name}, {label}."
        if name
        else f"You’re signed in as {label}. Your account has no saved display name."
    )
    if vehicle_name:
        reply += f" We’re reviewing the selected vehicle: {vehicle_name}."
        if "advisor" in relationships:
            reply += " Aura also confirms your advisor relationship to this vehicle."
        if "owner" in relationships:
            reply += " Aura records you as its owner."
        elif "driver" in relationships:
            reply += " Aura records you as an assigned driver."
    return reply


def account_help(identity: dict, message: str) -> str:
    if identity_question(message):
        return describe_speaker(identity)

    professional = identity["account_role"] in {"admin", "advisor"}
    clean = (message or "").strip().lower()
    name = identity.get("display_name")
    greeting = f"Hi {name}." if name else "Hi."

    if clean in {"hello", "hi", "hey"}:
        if professional:
            return (
                f"{greeting} You’re in the Advisor Workspace. I can explain Aura and "
                "advisor workflows here without a vehicle selected. Search for a client "
                "or vehicle when you want me to use that vehicle’s recorded care context."
            )
        return (
            describe_speaker(identity)
            + " I can help you find your way around Aura. Select an authorised vehicle "
            "when you want to discuss its recorded care context."
        )

    if professional and professional_capability_question(message):
        role_label = (
            "administrator" if identity["account_role"] == "admin" else "advisor"
        )
        return (
            f"As an Ajebo Fix {role_label}, you can use the Advisor Console to review "
            "authorised clients and vehicles, consultations, assessments, treatment "
            "progression and advisor notes. In this Advisor Workspace, you can ask "
            "account or workflow questions without selecting a vehicle. Search by client "
            "name, vehicle, plate or VIN and select a result only when you want Rina to "
            "use that vehicle’s recorded care context."
        )

    if re.search(r"\b(help|how|where)\b|\bcan you do\b", clean):
        if professional:
            return (
                "You can stay in Advisor overview for account and workflow help. When a "
                "question depends on one vehicle, search by client name, vehicle, plate "
                "or VIN and select the exact record. Rina will then use only that "
                "authorised vehicle context. Administrators can use Advisor Console → "
                "Clients to manage client setup."
            )
        return (
            "Open your dashboard and select a vehicle to discuss its recorded condition "
            "or next steps. Your profile contains your account details. "
            "In account help, no vehicle records are loaded."
        )

    if professional:
        return (
            "You’re in Advisor overview with no vehicle selected. I can help with your "
            "account and Aura workflows here. Search for a client or vehicle only when "
            "you want to discuss recorded vehicle history, concerns or care records."
        )

    return (
        "This is account help, with no vehicle selected. I can confirm your signed-in "
        "identity or explain how to use Aura. Select an authorised vehicle to ask about "
        "a client, vehicle history, concerns or care records."
    )

