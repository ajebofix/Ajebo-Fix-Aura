# app/intelligence/vin_decoder.py

"""
Aura Vehicle Intelligence
VIN Decoder Service
"""

from __future__ import annotations

from sqlalchemy.orm import exc

from models import VehicleProfile
from extensions import db

from .providers.base import IntelligenceResult
from .providers.mock import MockVINProvider
from .providers.nhtsa import NHTSAVINProvider


def get_default_provider():
    """
    Return the default VIN provider for Aura.

    This allows the provider to be changed later
    without modifying VINDecoderService.
    """
    return NHTSAVINProvider()


class VINDecoderService:
    """
    Aura VIN decoding orchestration layer.

    Responsibilities:
    - Choose provider
    - Validate requests
    - Decode VIN
    - Persist decoded intelligence
    """

    def __init__(self, provider=None):
        self.provider = provider or get_default_provider()

    def decode(self, vin: str) -> IntelligenceResult:
        """
        Decode VIN only.

        No database writes.
        """

        vin = (vin or "").strip().upper()

        if not vin:
            return IntelligenceResult(
                success=False,
                errors=["VIN is required."],
                source="service",
            )

        return self.provider.decode(vin)

    def update_vehicle_profile(self, car) -> IntelligenceResult:
        """
        Decode VIN and update VehicleProfile.
        """

        result = self.decode(car.vin)

        if not result.success:
            return result

        data = result.data or {}

        profile = VehicleProfile.query.filter_by(car_id=car.id).first()

        if profile is None:

            profile = VehicleProfile(
                car_id=car.id,
            )

            db.session.add(profile)

        # ------------------------------------------
        # Update the Car identity
        # ------------------------------------------

        if data.get("make"):
            car.brand = data["make"]

        if data.get("model"):
            car.model = data["model"]

        if data.get("year"):
            try:
                car.year = int(data["year"])
            except (TypeError, ValueError):
                pass

        car.vehicle_identity_source = result.source

        # ------------------------------------------
        # Update the VehicleProfile
        # ------------------------------------------

        profile.engine_model = data.get("engine")
        profile.trim = data.get("trim")
        profile.body_style = data.get("body_style")
        profile.fuel_type = data.get("fuel_type")
        profile.drive_type = data.get("drive_type")
        profile.plant_country = data.get("plant_country")

        profile.vin_decoded = True
        profile.decoded_at = db.func.now()
        profile.source = result.source

        db.session.commit()

        return result
