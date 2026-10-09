"""Advisory capacity checks for flexible Gulf pilot workforce and workspaces.

Two distinct limits: daily completed/allocated jobs and oil changes happening
at once. No presence, qualification, bay, or technician approval is inferred.
This is NOT a reservation transaction; production callers must recheck every
fact under an atomic lock and account for all non-Gulf workshop commitments.
"""

from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

LAGOS = ZoneInfo("Africa/Lagos")

# Current Ajebo Fix capability as described on 2026-10-09. Reassess these
# ceilings explicitly if workshop operating arrangements change.
ATTESTED_DAILY_CEILING = 7
ATTESTED_CONCURRENT_CEILING = 4


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
    # Interns may assist qualified technicians but are not themselves a
    # primary technician reservation or technical-authorisation authority.
    is_intern: bool = False

    def free_and_accepted(self, window: TimeWindow, service_skill: str) -> bool:
        return (
            not self.is_intern
            and service_skill in self.qualified_for
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
    # Sum of today's completed Gulf jobs, confirmed allocations and unexpired
    # holds; each job is counted once (never count a state transition twice).
    counted_gulf_jobs: int = 0
    # Conservative defaults. Higher capability is not prebooked by default.
    baseline_day_cap: int = 6
    baseline_concurrent_cap: int = 2
    authorised_day_cap: int | None = None
    authorised_concurrent_cap: int | None = None
    saturday_staff_check_done: bool = False
    sunday_open_approved: bool = False

    def __post_init__(self) -> None:
        if not 0 <= self.counted_gulf_jobs:
            raise ValueError("Counted jobs cannot be negative")
        for limit, ceiling, label in (
            (self.baseline_day_cap, ATTESTED_DAILY_CEILING, "Daily baseline"),
            (self.baseline_concurrent_cap, ATTESTED_CONCURRENT_CEILING, "Concurrent baseline"),
            (self.authorised_day_cap, ATTESTED_DAILY_CEILING, "Daily override"),
            (self.authorised_concurrent_cap, ATTESTED_CONCURRENT_CEILING, "Concurrent override"),
        ):
            if limit is not None and not 0 <= limit <= ceiling:
                raise ValueError(f"{label} must be within attested ceiling")

    @property
    def max_bookings(self) -> int:
        # Overrides require an audited authorised action outside this pure
        # evaluator. It does not accept client-reported availability as proof.
        return self.baseline_day_cap if self.authorised_day_cap is None else self.authorised_day_cap

    @property
    def max_concurrent_oil_jobs(self) -> int:
        return (
            self.baseline_concurrent_cap
            if self.authorised_concurrent_cap is None
            else self.authorised_concurrent_cap
        )


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
    overlapping_oil_jobs: int = 0,
) -> SlotCandidates:
    """Evaluate one potential Gulf oil-change hold, not a confirmed booking.

    overlapping_oil_jobs MUST include all oil-change jobs with intersecting
    windows: Gulf confirmed, in progress and unexpired holds, plus Ajebo Fix
    oil changes that consume the same resources. Non-oil workshop commitments
    must still appear in technician/bay other_commitments.

    Never infer a staff commitment from habitual presence at the workshop.
    Runtime must perform this check again inside the reservation transaction.
    """
    if overlapping_oil_jobs < 0:
        raise ValueError("Overlapping oil jobs cannot be negative")

    start_day = window.start.astimezone(LAGOS).date()
    end_day = window.end.astimezone(LAGOS).date()
    blockers: list[str] = []

    if start_day != end_day or start_day != policy.service_day:
        blockers.append("invalid_service_day")
    if policy.counted_gulf_jobs >= policy.max_bookings:
        blockers.append("daily_cap_reached")
    if overlapping_oil_jobs >= policy.max_concurrent_oil_jobs:
        blockers.append("concurrent_oil_capacity_reached")
    if policy.service_day.weekday() == 5 and not policy.saturday_staff_check_done:
        blockers.append("saturday_staff_check_required")
    if policy.service_day.weekday() == 6 and not policy.sunday_open_approved:
        blockers.append("sunday_opening_not_approved")

    available_techs = tuple(sorted(
        {t.technician_id for t in technicians if t.free_and_accepted(window, required_skill)}
    ))
    available_bays = tuple(sorted({b.bay_id for b in bays if b.free_for(window)}))
    if not available_techs:
        blockers.append("no_accepted_qualified_technician")
    if not available_bays:
        blockers.append("no_free_confirmed_bay")

    return SlotCandidates(available_techs, available_bays, tuple(blockers))
