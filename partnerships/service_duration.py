"""Planning-time contract for the Gulf One-Pay end-to-end appointment.

Workshop observation: an oil change itself takes 30–45 minutes (Toyota and
Mercedes can be comparable). Owner confirmed on 2026-10-13 that this EXCLUDES
inspection, photographs, service record and customer handover.

Until the exact inspection scope and additional task durations are agreed,
90 minutes may be tentatively protected on an INTERNAL calendar, but is not
a verified full-service duration, customer promise or automatic confirmation.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceTimePolicy:
    observed_min_minutes: int = 30
    observed_max_minutes: int = 45

    # Extra durations must be explicitly set from approved planning figures
    # or measured pilot records. Unknown is intentionally None, not zero.
    inspection_minutes: int | None = None
    photo_minutes: int | None = None
    service_record_minutes: int | None = None
    handover_minutes: int | None = None

    provisional_buffer_minutes: int = 15
    provisional_internal_hold_minutes: int = 90
    ten_point_scope_agreed: bool = False

    def __post_init__(self) -> None:
        if not 0 < self.observed_min_minutes <= self.observed_max_minutes:
            raise ValueError("Observed oil-change service range is invalid")
        if self.provisional_buffer_minutes < 0:
            raise ValueError("Scheduling buffer cannot be negative")
        if self.provisional_internal_hold_minutes <= 0:
            raise ValueError("Temporary calendar hold duration must be positive")
        if self.provisional_internal_hold_minutes < (
            self.observed_max_minutes + self.provisional_buffer_minutes
        ):
            raise ValueError("Temporary hold must cover oil-change work and buffer")
        for name in (
            "inspection_minutes",
            "photo_minutes",
            "service_record_minutes",
            "handover_minutes",
        ):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} cannot be negative")

    @property
    def provisional_hold_only_minutes(self) -> int:
        """An internal reservation placeholder, not customer confirmation."""
        return self.provisional_internal_hold_minutes

    @property
    def additional_tasks_known(self) -> bool:
        return all(
            minutes is not None
            for minutes in (
                self.inspection_minutes,
                self.photo_minutes,
                self.service_record_minutes,
                self.handover_minutes,
            )
        )

    @property
    def confirmed_scope_planning_minutes(self) -> int | None:
        """No final planning duration until ALL extra tasks and scope are resolved."""
        if not self.ten_point_scope_agreed or not self.additional_tasks_known:
            return None

        return (
            self.observed_max_minutes
            + self.inspection_minutes
            + self.photo_minutes
            + self.service_record_minutes
            + self.handover_minutes
            + self.provisional_buffer_minutes
        )


def recommended_planning_minutes(
    policy: ServiceTimePolicy,
    *,
    vehicle_specific_extra_minutes: int = 0,
) -> int | None:
    """Apply a justified vehicle-specific adjustment, not a brand multiplier."""
    if vehicle_specific_extra_minutes < 0:
        raise ValueError("Vehicle-specific extra time cannot be negative")
    base = policy.confirmed_scope_planning_minutes
    return None if base is None else base + vehicle_specific_extra_minutes
