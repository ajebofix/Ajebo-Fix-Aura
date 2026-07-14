# app/intelligence/providers/base.py

"""
Aura Vehicle Intelligence
Provider Interface

All Vehicle Intelligence providers must implement this interface.

Providers should:
- Retrieve intelligence from an external source.
- Normalize responses into Aura's standard format.
- Never perform database writes.
- Never depend on Flask or SQLAlchemy.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IntelligenceResult:
    """
    Standard response returned by every intelligence provider.
    """

    success: bool
    data: dict[str, Any] | None = None
    errors: list[str] = field(default_factory=list)
    source: str = "unknown"


class VINProvider(ABC):
    """
    Abstract interface for VIN decoding providers.
    """

    @abstractmethod
    def decode(self, vin: str) -> IntelligenceResult:
        """
        Decode a VIN and return normalized vehicle intelligence.

        Implementations should never raise provider-specific exceptions.
        Return IntelligenceResult instead.
        """
        raise NotImplementedError


class DTCProvider(ABC):
    """
    Abstract interface for DTC decoding providers.
    """

    @abstractmethod
    def decode(self, code: str) -> IntelligenceResult:
        """
        Decode a Diagnostic Trouble Code.
        """
        raise NotImplementedError


class RecallProvider(ABC):
    """
    Abstract interface for vehicle recall providers.
    """

    @abstractmethod
    def lookup(self, vin: str) -> IntelligenceResult:
        """
        Retrieve recall information for a VIN.
        """
        raise NotImplementedError


class MaintenanceProvider(ABC):
    """
    Abstract interface for maintenance schedule providers.
    """

    @abstractmethod
    def generate(
        self,
        *,
        brand: str,
        model: str,
        year: int,
        mileage: int,
    ) -> IntelligenceResult:
        """
        Generate maintenance schedule recommendations.
        """
        raise NotImplementedError
