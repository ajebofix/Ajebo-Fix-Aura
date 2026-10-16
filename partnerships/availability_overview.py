"""Conservative day-by-day Gulf workforce preparation view.

This is an OPERATIONS BOARD, not a bookable-slot generator.
Only explicit accepted technician windows contribute to staffing counts;
no workers are automatically scheduled by virtue of being registered.
"""
from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class GulfStaffDay:
    day: date
    accepted_leads: int
    pending_leads: int
    accepted_interns: int
    blocked_regular_oil_jobs: int
    booked_gulf_jobs: int
    daily_ceiling: int
    parallel_ceiling: int
    sunday_approved: bool
    saturday_verified: bool

    @property
    def scheduling_note(self) -> str:
        if self.day.weekday() == 6 and not self.sunday_approved:
            return "Sunday: manual workshop-opening approval required"
        if self.day.weekday() == 5 and not self.saturday_verified:
            return "Saturday: confirm workshop staffing"
        if self.accepted_leads == 0:
            return "No qualified lead has accepted a work window"
        return "Preparation only: exact slot, bay and job must still be checked"


def staff_day_overview(
    today: date,
    *,
    technicians,
    shifts,
    busy_slots,
    plans,
    jobs,
    as_of: datetime,
    number_of_days: int = 21,
) -> list[GulfStaffDay]:
    """Return a human-readable staffing view; never asserts booking eligibility."""
    if not 1 <= number_of_days <= 45:
        raise ValueError("Planning range must be between 1 and 45 days")

    workers = {t.id: t for t in technicians if t.active}
    day_plans = {p.service_day: p for p in plans}
    result: list[GulfStaffDay] = []
    for delta in range(number_of_days):
        day = today + timedelta(days=delta)
        accepted_leads = set()
        pending_leads = set()
        accepted_interns = set()
        for shift in shifts:
            if shift.starts_at.date() != day or shift.technician_id not in workers:
                continue
            worker = workers[shift.technician_id]
            if shift.accepted:
                if worker.is_intern:
                    accepted_interns.add(worker.id)
                elif worker.qualified_classes and worker.brand_specialties:
                    accepted_leads.add(worker.id)
            elif not worker.is_intern:
                pending_leads.add(worker.id)

        busy_oil = sum(
            1 for slot in busy_slots
            if slot.starts_at.date() == day and slot.is_oil_change
        )
        reserved = sum(
            1 for job in jobs
            if job.slot_start is not None
            and job.slot_start.date() == day
            and (
                job.state in {"confirmed", "in_service", "completed", "handed_over"}
                or (
                    job.state == "tentative"
                    and job.hold_expires_at is not None
                    and job.hold_expires_at > as_of
                )
            )
        )
        plan = day_plans.get(day)
        result.append(GulfStaffDay(
            day=day,
            accepted_leads=len(accepted_leads),
            pending_leads=len(pending_leads - accepted_leads),
            accepted_interns=len(accepted_interns),
            blocked_regular_oil_jobs=busy_oil,
            booked_gulf_jobs=reserved,
            daily_ceiling=plan.daily_cap if plan else 6,
            parallel_ceiling=plan.concurrent_cap if plan else 2,
            sunday_approved=bool(plan and plan.sunday_open),
            saturday_verified=bool(plan and plan.saturday_verified),
        ))
    return result
