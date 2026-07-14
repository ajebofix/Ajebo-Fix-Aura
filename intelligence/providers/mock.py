# app/intelligence/providers/mock.py

"""
Aura Vehicle Intelligence
Mock Provider

Development/testing provider.

Returns deterministic sample data without making
external API calls.
"""

from .base import (
    IntelligenceResult,
    VINProvider,
    DTCProvider,
    RecallProvider,
    MaintenanceProvider,
)


class MockVINProvider(VINProvider):

    def decode(self, vin: str) -> IntelligenceResult:

        return IntelligenceResult(
            success=True,
            source="mock",
            data={
                "vin": vin.upper(),
                "trim": "GLE 450 4MATIC",
                "body_style": "SUV",
                "fuel_type": "Gasoline",
                "drive_type": "AWD",
                "plant_country": "USA",
                "manufacturer": "Mercedes-Benz",
            },
        )


class MockDTCProvider(DTCProvider):

    def decode(
        self,
        code: str,
        manufacturer: str | None = None,
    ) -> IntelligenceResult:

        code = (code or "").strip().upper()

        mapping = {
            "P0300": {
                "code_type": "SAE",
                "manufacturer": None,
                "is_generic": True,
                "description": ("Random/Multiple Cylinder Misfire Detected"),
                "severity": "attention",
                "affected_system": "Powertrain",
                "possible_causes": [
                    "Ignition system irregularity",
                    "Fuel delivery irregularity",
                    "Air intake or vacuum leak",
                    "Compression irregularity",
                ],
                "recommended_action": ("Professional diagnostic review advised."),
            },
            "P0171": {
                "code_type": "SAE",
                "manufacturer": None,
                "is_generic": True,
                "description": "System Too Lean (Bank 1)",
                "severity": "attention",
                "affected_system": "Fuel and Air Metering",
                "possible_causes": [
                    "Unmetered air entering the engine",
                    "Fuel delivery restriction",
                    "Airflow measurement irregularity",
                    "Exhaust oxygen sensor irregularity",
                ],
                "recommended_action": ("Inspect fuel-trim data and intake integrity."),
            },
            "U0121": {
                "code_type": "SAE",
                "manufacturer": None,
                "is_generic": True,
                "description": (
                    "Lost Communication With " "Anti-Lock Brake System Control Module"
                ),
                "severity": "high",
                "affected_system": ("Network Communication / Braking"),
                "possible_causes": [
                    "CAN network interruption",
                    "ABS control module power supply issue",
                    "Wiring or connector fault",
                ],
                "recommended_action": (
                    "Professional electrical and network " "diagnostic review advised."
                ),
            },
        }

        result = mapping.get(code)

        if not result:
            return IntelligenceResult(
                success=False,
                source="mock",
                errors=[
                    f"{code} is not available in the " "development mock provider."
                ],
            )

        return IntelligenceResult(
            success=True,
            source="mock",
            data={
                "code": code,
                **result,
            },
        )


class MockRecallProvider(RecallProvider):

    def lookup(self, vin: str) -> IntelligenceResult:

        return IntelligenceResult(
            success=True,
            source="mock",
            data={
                "open_recalls": [
                    {
                        "recall_number": "MB-2025-001",
                        "title": "Steering Control Module",
                        "risk_level": "high",
                        "summary": (
                            "Potential steering control module " "software issue."
                        ),
                    }
                ]
            },
        )


class MockMaintenanceProvider(MaintenanceProvider):

    def generate(
        self,
        *,
        brand: str,
        model: str,
        year: int,
        mileage: int,
    ) -> IntelligenceResult:

        return IntelligenceResult(
            success=True,
            source="mock",
            data={
                "services": [
                    {
                        "service_name": "Service A",
                        "status": "due",
                        "due_mileage": mileage + 500,
                    },
                    {
                        "service_name": "Brake Fluid",
                        "status": "upcoming",
                        "due_mileage": mileage + 4000,
                    },
                ]
            },
        )
