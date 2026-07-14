# app/intelligence/providers/carapi.py

"""
Aura Vehicle Intelligence
Car API Provider (STUB)

This is intentionally NOT a working integration yet.

Car API is the more practical candidate for a real generic-code
beta provider (treat it as a fallback/beta source, not the final
source of truth for OEM/Mercedes-Benz diagnostics - MOTOR remains
the production target for that). Before this can be implemented
for real, Aura needs:

- A Car API account and API key
- Confirmed request/response schema from their actual, current
  documentation (not guessed or assumed)
- Confirmed rate limits and usage terms

Fabricating request/response handling against an unverified API
shape would silently produce wrong or missing results in
production. Implement this only against Car API's actual current
documentation once an account/key exists.
"""

from .base import DTCProvider, IntelligenceResult


class CarApiDTCProvider(DTCProvider):

    def decode(
        self,
        code: str,
        manufacturer: str | None = None,
    ) -> IntelligenceResult:

        raise NotImplementedError(
            "CarApiDTCProvider is not yet implemented. "
            "An account, API key, and verified current API "
            "documentation are required before this provider can "
            "be built. See module docstring."
        )
