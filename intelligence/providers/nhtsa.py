# app/intelligence/providers/nhtsa.py

"""
Aura Vehicle Intelligence
NHTSA VIN Provider
"""

from __future__ import annotations

import requests

from .base import (
    IntelligenceResult,
    VINProvider,
)


class NHTSAVINProvider(VINProvider):
    """
    VIN decoder backed by the NHTSA vPIC API.
    """

    BASE_URL = (
        "https://vpic.nhtsa.dot.gov/api/"
        "vehicles/DecodeVinValues/{vin}"
        "?format=json"
    )

    TIMEOUT = 10

    def decode(self, vin: str) -> IntelligenceResult:

        vin = vin.strip().upper()

        if len(vin) != 17:
            return IntelligenceResult(
                success=False,
                errors=["VIN must contain exactly 17 characters."],
                source="nhtsa",
            )

        try:

            response = requests.get(
                self.BASE_URL.format(vin=vin),
                timeout=self.TIMEOUT,
            )

            response.raise_for_status()

            payload = response.json()

        except requests.RequestException as exc:

            return IntelligenceResult(
                success=False,
                errors=[str(exc)],
                source="nhtsa",
            )

        results = payload.get("Results", [])

        if not results:

            return IntelligenceResult(
                success=False,
                errors=["No VIN data returned."],
                source="nhtsa",
            )

        vehicle = results[0]
        print(vehicle)

        error_code = str(vehicle.get("ErrorCode", "")).strip()
        error_text = (vehicle.get("ErrorText") or "").strip()

        if error_code not in ("0", "1"):
            return IntelligenceResult(
                success=False,
                errors=[error_text or "VIN could not be decoded."],
                source="nhtsa",
            )

        manufacturer = vehicle.get("Manufacturer")
        make = vehicle.get("Make")
        model = vehicle.get("Model")

        if not any([manufacturer, make, model]):
            return IntelligenceResult(
                success=False,
                errors=["No vehicle information could be extracted from this VIN."],
                source="nhtsa",
            )

        return IntelligenceResult(
            success=True,
            source="nhtsa",
            data={
                "vin": vin,
                "manufacturer": vehicle.get("Manufacturer"),
                "make": vehicle.get("Make"),
                "model": vehicle.get("Model"),
                "year": vehicle.get("ModelYear"),
                "trim": vehicle.get("Trim"),
                "body_style": vehicle.get("BodyClass"),
                "fuel_type": vehicle.get("FuelTypePrimary"),
                "drive_type": vehicle.get("DriveType"),
                "plant_country": vehicle.get("PlantCountry"),
                "engine": vehicle.get("EngineModel"),
            },
        )
