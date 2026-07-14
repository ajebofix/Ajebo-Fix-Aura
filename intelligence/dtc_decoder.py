# intelligence/dtc_decoder.py

"""
Aura Vehicle Intelligence
DTC Decoder Service

Resolution order:

1. Manufacturer-specific definition
2. Generic SAE definition
3. External/provider lookup
4. Save provider result into local knowledge library
5. Create vehicle-specific DTC occurrence
"""

from __future__ import annotations

import re
from datetime import datetime

from extensions import db
from models import (
    DiagnosticCodeDefinition,
    VehicleDTC,
)

from .providers.base import IntelligenceResult
from .providers.mock import MockDTCProvider


DTC_PATTERN = re.compile(
    r"^[PBCU][0-3][0-9A-F]{3}$",
    re.IGNORECASE,
)


def get_default_provider():
    """
    Temporary development provider.

    Replace this later with a real SAE/OEM/API provider.
    """
    return MockDTCProvider()


class DTCDecoderService:
    """
    Database-first DTC resolution service.

    The local DiagnosticCodeDefinition table is the primary
    source of truth.

    External providers are only used when the local library
    has no suitable definition.
    """

    def __init__(self, provider=None):
        self.provider = provider or get_default_provider()


    MANUFACTURER_ALIASES = {
        "MERCEDES": "MERCEDES-BENZ",
        "MERCEDES-BENZ": "MERCEDES-BENZ",
        "MERCEDES BENZ": "MERCEDES-BENZ",
        "MB": "MERCEDES-BENZ",
        "BMW AG": "BMW",
        "VOLKSWAGEN": "VOLKSWAGEN",
        "VW": "VOLKSWAGEN",
    }

    # =====================================================
    # NORMALIZATION
    # =====================================================

    @staticmethod
    def normalize_manufacturer(
        cls,
        manufacturer: str | None,
    ) -> str:
        if not manufacturer:
            return "GENERIC"

        normalized = (
            manufacturer.strip()
        )


    @staticmethod
    def normalize_manufacturer(manufacturer: str | None) -> str | None:
        if not manufacturer:
            return None

        value = manufacturer.strip()

        return value or None

    # =====================================================
    # VALIDATION
    # =====================================================

    def validate_code(self, code: str) -> IntelligenceResult | None:
        """
        Return an error result when invalid.
        Return None when valid.
        """

        if not code:
            return IntelligenceResult(
                success=False,
                errors=["Diagnostic Trouble Code is required."],
                source="service",
            )

        if not DTC_PATTERN.fullmatch(code):
            return IntelligenceResult(
                success=False,
                errors=[
                    (
                        f"{code} is not a valid standard DTC format. "
                        "Expected formats include P0300, U0121, "
                        "B1000, or C0035."
                    )
                ],
                source="service",
            )

        return None

    # =====================================================
    # DEFINITION LOOKUP
    # =====================================================

    def find_definition(
        self,
        *,
        code: str,
        manufacturer: str | None = None,
    ) -> DiagnosticCodeDefinition | None:
        """
        Lookup order:

        1. Manufacturer-specific definition
        2. Generic definition
        """

        normalized_code = self.normalize_code(code)
        normalized_manufacturer = self.normalize_manufacturer(manufacturer)

        if normalized_manufacturer:
            manufacturer_match = (
                DiagnosticCodeDefinition.query.filter(
                    DiagnosticCodeDefinition.code == normalized_code,
                    db.func.lower(
                        DiagnosticCodeDefinition.manufacturer
                    )
                    == normalized_manufacturer.lower(),
                )
                .order_by(
                    DiagnosticCodeDefinition.updated_at.desc()
                )
                .first()
            )

            if manufacturer_match:
                return manufacturer_match

        generic_match = (
            DiagnosticCodeDefinition.query.filter(
                DiagnosticCodeDefinition.code == normalized_code,
                DiagnosticCodeDefinition.is_generic.is_(True),
            )
            .order_by(
                DiagnosticCodeDefinition.updated_at.desc()
            )
            .first()
        )

        return generic_match

    # =====================================================
    # PROVIDER LOOKUP
    # =====================================================

    def lookup_provider(
        self,
        *,
        code: str,
        manufacturer: str | None = None,
    ) -> IntelligenceResult:
        """
        Ask the configured provider for a definition.

        Supports both providers that accept:
            decode(code)

        and future providers that accept:
            decode(code, manufacturer=...)
        """

        try:
            return self.provider.decode(
                code,
                manufacturer=manufacturer,
            )

        except TypeError:
            return self.provider.decode(code)

        except Exception as exc:
            return IntelligenceResult(
                success=False,
                errors=[
                    f"DTC provider lookup failed: {str(exc)}"
                ],
                source="provider",
            )

    # =====================================================
    # SAVE DEFINITION
    # =====================================================

    def save_definition_from_result(
        self,
        *,
        code: str,
        result: IntelligenceResult,
        manufacturer: str | None = None,
    ) -> DiagnosticCodeDefinition:
        """
        Save a provider result into Aura's local knowledge library.

        Existing definitions are updated rather than duplicated.
        """

        data = result.data or {}

        resolved_manufacturer = (
            data.get("manufacturer")
            or manufacturer
        )

        is_generic = data.get("is_generic")

        if is_generic is None:
            is_generic = not bool(resolved_manufacturer)

        definition = self.find_definition(
            code=code,
            manufacturer=resolved_manufacturer,
        )

        if not definition:
            definition = DiagnosticCodeDefinition(
                code=code,
                manufacturer=resolved_manufacturer,
            )

            db.session.add(definition)

        definition.code_type = data.get(
            "code_type",
            "SAE" if is_generic else "OEM",
        )

        definition.is_generic = is_generic

        definition.description = data.get(
            "description",
            "Definition unavailable.",
        )

        definition.affected_system = data.get(
            "affected_system",
        )

        definition.severity = data.get(
            "severity",
            "information",
        )

        definition.possible_causes = self._serialize_text_field(
            data.get("possible_causes")
        )

        definition.recommended_action = self._serialize_text_field(
            data.get("recommended_action")
        )

        definition.source = (
            result.source
            or data.get("source")
            or "provider"
        )

        definition.last_verified_at = datetime.utcnow()

        db.session.flush()

        return definition

    # =====================================================
    # MAIN DECODE FLOW
    # =====================================================

    def decode(
        self,
        code: str,
        manufacturer: str | None = None,
    ) -> IntelligenceResult:
        """
        Resolve a code without creating a VehicleDTC occurrence.
        """

        normalized_code = self.normalize_code(code)

        validation_error = self.validate_code(normalized_code)

        if validation_error:
            return validation_error

        definition = self.find_definition(
            code=normalized_code,
            manufacturer=manufacturer,
        )

        if definition:
            return self._definition_to_result(definition)

        provider_result = self.lookup_provider(
            code=normalized_code,
            manufacturer=manufacturer,
        )

        if not provider_result.success:
            return provider_result

        data = provider_result.data or {}

        description = data.get("description")

        if not description:
            return IntelligenceResult(
                success=False,
                errors=[
                    (
                        f"No reliable definition was found for "
                        f"{normalized_code}."
                    )
                ],
                source=provider_result.source,
            )

        definition = self.save_definition_from_result(
            code=normalized_code,
            result=provider_result,
            manufacturer=manufacturer,
        )

        try:
            db.session.commit()

        except Exception as exc:
            db.session.rollback()

            return IntelligenceResult(
                success=False,
                errors=[
                    (
                        "The DTC was decoded, but its definition "
                        f"could not be saved: {str(exc)}"
                    )
                ],
                source="database",
            )

        return self._definition_to_result(definition)

    # =====================================================
    # CREATE VEHICLE OCCURRENCE
    # =====================================================

    def add_vehicle_dtc(
        self,
        *,
        car,
        code: str,
        source: str = "manual",
        advisor_note: str | None = None,
    ) -> IntelligenceResult:
        """
        Resolve a definition and store a vehicle-specific occurrence.
        """

        normalized_code = self.normalize_code(code)

        manufacturer = getattr(car, "brand", None)

        result = self.decode(
            normalized_code,
            manufacturer=manufacturer,
        )

        if not result.success:
            return result

        data = result.data or {}

        definition_id = data.get("definition_id")

        existing = VehicleDTC.query.filter_by(
            car_id=car.id,
            code=normalized_code,
            status="active",
        ).first()

        if existing:
            return IntelligenceResult(
                success=True,
                source="database",
                data={
                    **data,
                    "vehicle_dtc_id": existing.id,
                    "already_active": True,
                },
            )

        dtc = VehicleDTC(
            car_id=car.id,
            definition_id=definition_id,
            code=normalized_code,
            code_type=data.get("code_type", "SAE"),
            description=data.get(
                "description",
                "Definition unavailable.",
            ),
            affected_system=data.get("affected_system"),
            severity=data.get(
                "severity",
                "information",
            ),
            status="active",
            advisor_note=advisor_note,
            source=source,
        )

        db.session.add(dtc)

        try:
            db.session.commit()

        except Exception as exc:
            db.session.rollback()

            return IntelligenceResult(
                success=False,
                errors=[
                    f"Unable to save DTC occurrence: {str(exc)}"
                ],
                source="database",
            )

        return IntelligenceResult(
            success=True,
            source=result.source,
            data={
                **data,
                "vehicle_dtc_id": dtc.id,
                "already_active": False,
            },
        )

    # =====================================================
    # CLEAR VEHICLE OCCURRENCE
    # =====================================================

    def clear_vehicle_dtc(
        self,
        *,
        car,
        dtc_id: int,
        cleared_by_user_id: int | None = None,
    ) -> bool:
        """
        Mark a vehicle DTC occurrence as cleared.

        The definition remains in the reusable knowledge library.
        """

        dtc = VehicleDTC.query.filter_by(
            id=dtc_id,
            car_id=car.id,
        ).first()

        if not dtc:
            return False

        if dtc.status == "cleared":
            return True

        dtc.status = "cleared"
        dtc.cleared_at = datetime.utcnow()
        dtc.cleared_by = cleared_by_user_id

        try:
            db.session.commit()

        except Exception:
            db.session.rollback()
            return False

        return True

    # =====================================================
    # HELPERS
    # =====================================================

    @staticmethod
    def _serialize_text_field(value) -> str | None:
        """
        Store lists cleanly in Text columns.
        """

        if value is None:
            return None

        if isinstance(value, (list, tuple, set)):
            return "\n".join(
                str(item).strip()
                for item in value
                if str(item).strip()
            )

        return str(value).strip() or None

    @staticmethod
    def _definition_to_result(
        definition: DiagnosticCodeDefinition,
    ) -> IntelligenceResult:

        return IntelligenceResult(
            success=True,
            source=definition.source or "database",
            data={
                "definition_id": definition.id,
                "code": definition.code,
                "code_type": definition.code_type,
                "manufacturer": definition.manufacturer,
                "is_generic": definition.is_generic,
                "description": definition.description,
                "affected_system": definition.affected_system,
                "severity": definition.severity,
                "possible_causes": definition.possible_causes,
                "recommended_action": (
                    definition.recommended_action
                ),
            },
        )