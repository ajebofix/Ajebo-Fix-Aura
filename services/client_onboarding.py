"""Secure advisor-assisted owner onboarding for Aura."""

from __future__ import annotations

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import func

from extensions import db
from models import ClientInvitation, User


INVITATION_LIFETIME_HOURS = 72


class ClientOnboardingError(ValueError):
    """Raised when an assisted owner onboarding action is invalid."""


@dataclass(frozen=True)
class InvitationResult:
    invitation: ClientInvitation
    token: str


def _utcnow() -> datetime:
    return datetime.utcnow()


def _normalise_email(value: str | None) -> str | None:
    email = (value or "").strip().lower()
    if not email:
        return None
    if len(email) > 120 or not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", email):
        raise ClientOnboardingError("Enter a valid email address.")
    return email


def _normalise_phone(value: str | None) -> str:
    phone = re.sub(r"[\s().-]", "", (value or "").strip())
    if not re.fullmatch(r"\+?\d{7,20}", phone):
        raise ClientOnboardingError(
            "Phone number must contain 7 to 20 digits and may begin with +."
        )
    return phone


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class ClientOnboardingService:
    """Own assisted client creation, invitation and basic account identity."""

    @staticmethod
    def create_client(
        *,
        name: str,
        phone_number: str,
        email: str | None,
        created_by_user_id: int,
    ) -> tuple[User, InvitationResult]:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ClientOnboardingError("Client name is required.")
        if len(clean_name) > 120:
            raise ClientOnboardingError("Client name must be 120 characters or fewer.")

        phone = _normalise_phone(phone_number)
        clean_email = _normalise_email(email)

        if User.query.filter_by(phone_number=phone).first() is not None:
            raise ClientOnboardingError(
                "An Aura account already uses this phone number."
            )

        if (
            clean_email
            and User.query.filter(func.lower(User.email) == clean_email).first()
            is not None
        ):
            raise ClientOnboardingError(
                "An Aura account already uses this email address."
            )

        user = User(
            name=clean_name,
            email=clean_email,
            phone_number=phone,
            role="user",
            is_active=True,
        )
        # Advisor never knows the credential. The random bootstrap password is
        # immediately replaced by the owner during invitation activation.
        user.set_password(secrets.token_urlsafe(48))
        db.session.add(user)
        db.session.flush()

        result = ClientOnboardingService.issue_invitation(
            user=user,
            created_by_user_id=created_by_user_id,
            commit=False,
        )
        db.session.commit()
        return user, result

    @staticmethod
    def issue_invitation(
        *,
        user: User,
        created_by_user_id: int,
        commit: bool = True,
    ) -> InvitationResult:
        if user.role != "user":
            raise ClientOnboardingError(
                "Only owner/client accounts can use this activation flow."
            )

        now = _utcnow()
        ClientInvitation.query.filter(
            ClientInvitation.user_id == user.id,
            ClientInvitation.accepted_at.is_(None),
            ClientInvitation.revoked_at.is_(None),
        ).update(
            {"revoked_at": now},
            synchronize_session=False,
        )

        token = secrets.token_urlsafe(32)
        invitation = ClientInvitation(
            user_id=user.id,
            created_by_user_id=created_by_user_id,
            token_hash=_token_hash(token),
            expires_at=now + timedelta(hours=INVITATION_LIFETIME_HOURS),
        )
        db.session.add(invitation)

        if commit:
            db.session.commit()
        else:
            db.session.flush()

        return InvitationResult(invitation=invitation, token=token)

    @staticmethod
    def resolve_invitation(token: str) -> ClientInvitation | None:
        token = (token or "").strip()
        if not token:
            return None

        invitation = ClientInvitation.query.filter_by(
            token_hash=_token_hash(token)
        ).first()
        if invitation is None:
            return None
        if invitation.accepted_at is not None or invitation.revoked_at is not None:
            return None
        if invitation.expires_at <= _utcnow():
            return None
        if invitation.user is None or invitation.user.role != "user":
            return None
        return invitation

    @staticmethod
    def accept_invitation(
        *,
        invitation: ClientInvitation,
        password: str,
    ) -> User:
        if invitation.accepted_at is not None or invitation.revoked_at is not None:
            raise ClientOnboardingError("This activation link has already been used.")
        if invitation.expires_at <= _utcnow():
            raise ClientOnboardingError("This activation link has expired.")

        user = invitation.user
        if user is None or user.role != "user":
            raise ClientOnboardingError("This activation link is not valid.")

        user.set_password(password)
        invitation.accepted_at = _utcnow()

        ClientInvitation.query.filter(
            ClientInvitation.user_id == user.id,
            ClientInvitation.id != invitation.id,
            ClientInvitation.accepted_at.is_(None),
            ClientInvitation.revoked_at.is_(None),
        ).update(
            {"revoked_at": _utcnow()},
            synchronize_session=False,
        )

        db.session.commit()
        return user

    @staticmethod
    def update_basic_identity(
        *,
        user: User,
        name: str,
        phone_number: str,
        email: str,
    ) -> bool:
        clean_name = (name or "").strip()
        if not clean_name:
            raise ClientOnboardingError("Your name is required.")
        if len(clean_name) > 120:
            raise ClientOnboardingError("Your name must be 120 characters or fewer.")

        phone = _normalise_phone(phone_number)
        clean_email = _normalise_email(email)
        if clean_email is None:
            raise ClientOnboardingError(
                "Add an email address to finish setting up your Aura account."
            )

        phone_owner = User.query.filter(
            User.phone_number == phone,
            User.id != user.id,
        ).first()
        if phone_owner is not None:
            raise ClientOnboardingError(
                "Another Aura account already uses this phone number."
            )

        email_owner = User.query.filter(
            func.lower(User.email) == clean_email,
            User.id != user.id,
        ).first()
        if email_owner is not None:
            raise ClientOnboardingError(
                "Another Aura account already uses this email address."
            )

        email_changed = (user.email or "").strip().lower() != clean_email

        user.name = clean_name
        user.phone_number = phone
        user.email = clean_email
        if email_changed:
            user.email_verified_at = None

        db.session.commit()
        return email_changed

    @staticmethod
    def latest_invitation(user_id: int) -> ClientInvitation | None:
        return (
            ClientInvitation.query.filter_by(user_id=user_id)
            .order_by(ClientInvitation.created_at.desc(), ClientInvitation.id.desc())
            .first()
        )

    @staticmethod
    def account_status(user: User) -> str:
        if user.email and getattr(user, "email_verified_at", None) is not None:
            return "ready"

        latest = ClientOnboardingService.latest_invitation(user.id)
        now = _utcnow()
        if (
            latest is not None
            and latest.accepted_at is None
            and latest.revoked_at is None
            and latest.expires_at > now
        ):
            return "invited"

        if user.email:
            return "email_unverified"

        return "setup_incomplete"
