"""Pure advisory capacity checks for flexible, commission-based service crews.

A successful result means an operator MAY allocate a temporary time hold, not
that a customer appointment is confirmed or a technician has been booked.
These checks must be repeated under transactional locks when a real booking
is created. No staffing or attendance is inferred from historical presence.
"""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

LAGOS = ZoneInfo("Africa/Lagos")


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("Windows require timezone-aware start and end")
        if self.start.utcoffset() is None or self.end.utcoffset() is None:
            raise ValueError("Windows require valid UTC offsets")
        if self.end <= self.start:
            raise ValueError("Window end must follow start")

    def contains(self, other: "TimeWindow") -> bool:
        return self.start <= other.start and other.end <= self.end

    def overlaps(self, other: "TimeWindow") -> bool:
        return self.start < other.end and other.start < self.end


@dataclass(frozen=True)
class TechnicianAvailability:
    technician_id: str
    qualified_for: frozenset[str]
    accepted_windows: tuple[TimeWindow, ...] = ()
    other_commitments: tuple[TimeWindow, ...] = ()

    def free_and_accepted(self, window: TimeWindow, service_skill: str) -> bool:
        return (
            service_skill in self.qualified_for
            and any(w.contains(window) for w in self.accepted_windows)
            and not any(w.overlaps(window) for w in self.other_commitments)
        )


@dataclass(frozen=True)
class BayAvailability:
    bay_id: str
    open_windows: tuple[TimeWindow, ...] = ()
    other_commitments: tuple[TimeWindow, ...] = ()

    def free_for(self, window: TimeWindow) -> bool:
        return (
            any(w.contains(window) for w in self.open_windows)
            and not any(w.overlaps(window) for w in self.other_commitments)
        )


@dataclass(frozen=True)
class DayPolicy:
    service_day: date
    confirmed_gulf_jobs: int = 0
    baseline_day_cap: int = 4
    authorised_day_cap: int | None = None
    saturday_staff_check_done: bool = False
    sunday_open_approved: bool = False

    def __post_init__(self) -> None:
        if self.confirmed_gulf_jobs < 0 or self.baseline_day_cap < 0:
            raise ValueError("Counts and limits cannot be negative")
        if self.authorised_day_cap is not None and self.authorised_day_cap < 0:
            raise ValueError("Authorised day cap cannot be negative")

    @property
    def max_bookings(self) -> int:
        # authorised_day_cap is set only by Ajebo Fix's staff-facing approval path.
        # It is not a customer preference or a self-reported technician claim.
        return self.baseline_day_cap if self.authorised_day_cap is None else self.authorised_day_cap


@dataclass(frozen=True)
class SlotCandidates:
    eligible_technician_ids: tuple[str, ...]
    eligible_bay_ids: tuple[str, ...]
    blockers: tuple[str, ...]

    @property
    def can_offer_tentative_hold(self) -> bool:
        return not self.blockers


def assess_slot(
    *,
    window: TimeWindow,
    policy: DayPolicy,
    required_skill: str,
    technicians: tuple[TechnicianAvailability, ...],
    bays: tuple[BayAvailability, ...],
) -> SlotCandidates:
    """Compute eligible resources, never confirm an appointment by itself.

    A technician's accepted window is an explicit, current human response,
    not a routine assumption that someone will be around the workshop.
    Other Ajebo Fix work and home-service commitments must be included in
    the busy intervals supplied by the booking layer.
    """
    start_day = window.start.astimezone(LAGOS).date()
    end_day = window.end.astimezone(LAGOS).date()
    blockers: list[str] = []

    if start_day != end_day or start_day != policy.service_day:
        blockers.append("invalid_service_day")
    if policy.confirmed_gulf_jobs >= policy.max_bookings:
        blockers.append("daily_cap_reached")
    if policy.service_day.weekday() == 5 and not policy.saturday_staff_check_done:
        blockers.append("saturday_staff_check_required")
    if policy.service_day.weekday() == 6 and not policy.sunday_open_approved:
        blockers.append("sunday_opening_not_approved")

    available_techs = tuple(sorted(t.technician_id for t in technicians if t.free_and_accepted(window, required_skill)))
    available_bays = tuple(sorted(b.bay_id for b in bays if b.free_for(window)))
    if not available_techs:
        blockers.append("no_accepted_qualified_technician")
    if not available_bays:
        blockers.append("no_free_confirmed_bay")

    return SlotCandidates(available_techs, available_bays, tuple(blockers))
