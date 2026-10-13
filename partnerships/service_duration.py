"""Conservative planning-time contract for the Gulf oil-change pilot.

The workshop reports 30–45 minutes for a normal oil change across Toyota and
Mercedes-Benz. Brand is not a time multiplier. The sponsor's separate 10-point
check is not yet defined as ten inspection items nor confirmed as included
in the observed time. Do not promise a finished appointment from a provisional
duration or a draft checklist.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceTimePolicy:
    observed_min_minutes: int = 30
    observed_max_minutes: int = 45
    provisional_buffer_minutes: int = 15
    ten_point_scope_agreed: bool = False
    check_included_in_observed_minutes: bool | None = None
    extra_check_minutes: int | None = None

    def __post_init__(self) -> None:
        if not 0 < self.observed_min_minutes <= self.observed_max_minutes:
            raise ValueError("Observed service range is invalid")
        if self.provisional_buffer_minutes < 0:
            raise ValueError("Turnover buffer cannot be negative")
        if self.extra_check_minutes is not None and self.extra_check_minutes < 0:
            raise ValueError("Additional check time cannot be negative")
        if self.check_included_in_observed_minutes is True and self.extra_check_minutes not in (None, 0):
            raise ValueError("Do not add inspection time that is already included")

    @property
    def base_provisional_minutes(self) -> int:
        """Planning estimate only; NOT the confirmed end-to-end campaign slot."""
        return self.observed_max_minutes + self.provisional_buffer_minutes

    @property
    def confirmed_scope_planning_minutes(self) -> int | None:
        """Return None when inspection scope or its extra time is unresolved."""
        if not self.ten_point_scope_agreed:
            return None
        if self.check_included_in_observed_minutes is True:
            return self.base_provisional_minutes
        if self.check_included_in_observed_minutes is False:
            if self.extra_check_minutes is None:
                return None
            return self.base_provisional_minutes + self.extra_check_minutes
        return None


def recommended_planning_minutes(
    policy: ServiceTimePolicy,
    *,
    vehicle_specific_extra_minutes: int = 0,
) -> int | None:
    """Add documented job-specific exceptions, never a brand-wide multiplier.

    Complex access, a slow reset or a particular technician may justify a
    manual, recorded adjustment. Nothing about 'Mercedes' alone does.
    """
    if vehicle_specific_extra_minutes < 0:
        raise ValueError("Vehicle-specific extra time cannot be negative")
    base = policy.confirmed_scope_planning_minutes
    return None if base is None else base + vehicle_specific_extra_minutes
