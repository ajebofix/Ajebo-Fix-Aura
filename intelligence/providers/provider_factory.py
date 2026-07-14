# app/intelligence/providers/provider_factory.py

"""
Aura Vehicle Intelligence
DTC Provider Factory

Selects the active DTC provider based on environment
configuration, so production can never silently fall back to
demo/mock data.

Environment variables:

    DTC_PROVIDER   "motor" | "carapi" | "mock"
    FLASK_ENV      "development" | "testing" | "production"

MockDTCProvider is only permitted when DTC_PROVIDER=mock AND
FLASK_ENV is "development" or "testing". Any other combination
either selects a real provider or raises, rather than guessing.
"""

import os

from .mock import MockDTCProvider
from .carapi import CarApiDTCProvider
from .motor import MotorDTCProvider


def get_default_provider():
    provider_name = os.getenv(
        "DTC_PROVIDER",
        "",
    ).strip().lower()

    if provider_name == "motor":
        return MotorDTCProvider()

    if provider_name == "carapi":
        return CarApiDTCProvider()

    if provider_name == "mock":
        environment = os.getenv(
            "FLASK_ENV",
            "production",
        ).lower()

        if environment not in (
            "development",
            "testing",
        ):
            raise RuntimeError(
                "MockDTCProvider cannot run in production. "
                "Set DTC_PROVIDER to 'motor' or 'carapi', or set "
                "FLASK_ENV to 'development'/'testing' for local "
                "work."
            )

        return MockDTCProvider()

    raise RuntimeError(
        "No real DTC provider has been configured. Set the "
        "DTC_PROVIDER environment variable to 'motor', 'carapi', "
        "or (development/testing only) 'mock'."
    )
