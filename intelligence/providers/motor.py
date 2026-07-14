# app/intelligence/providers/motor.py

"""
Aura Vehicle Intelligence
MOTOR Information Systems Provider (STUB)

This is intentionally NOT a working integration.

MOTOR's DTC/Data-as-a-Service product is commercial and
sales-led. Before this can be implemented for real, Aura needs:

- A business account with MOTOR Information Systems
- Confirmed API/authentication details
- Confirmed response schema
- Confirmed licensing terms (caching rights, redistribution,
  Nigerian commercial use, coverage for grey-import/European/US
  market Mercedes-Benz vehicles)

Fabricating request/response handling against an unverified,
undocumented API would silently produce wrong or misleading
results in production. Do not "fill this in" with guessed
endpoints - implement it only against MOTOR's actual published
API documentation once credentials are secured.
"""

from .base import DTCProvider, IntelligenceResult


class MotorDTCProvider(DTCProvider):

    def decode(
        self,
        code: str,
        manufacturer: str | None = None,
    ) -> IntelligenceResult:

        raise NotImplementedError(
            "MotorDTCProvider is not yet implemented. "
            "MOTOR Information Systems requires a licensed "
            "business account and confirmed API details before "
            "this provider can be built. See module docstring."
        )
