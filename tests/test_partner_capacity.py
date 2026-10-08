"""Contract tests: flexible Gulf crew, bay allocation and daily caps."""

import unittest
from datetime import date, datetime
from zoneinfo import ZoneInfo

from partnerships.capacity import (
    BayAvailability,
    DayPolicy,
    TechnicianAvailability,
    TimeWindow,
    assess_slot,
)

ZONE = ZoneInfo("Africa/Lagos")


def period(day, from_h, to_h):
    return TimeWindow(datetime(day.year, day.month, day.day, from_h, tzinfo=ZONE),
                      datetime(day.year, day.month, day.day, to_h, tzinfo=ZONE))


class FlexibleCapacityTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 10, 12)  # Monday
        self.window = period(self.day, 10, 11)
        self.tech = TechnicianAvailability("tech-a", frozenset({"standard"}), (period(self.day, 9, 13),))
        self.bay = BayAvailability("bay-1", (period(self.day, 9, 18),))

    def check(self, *, policy=None, techs=None, bays=None, window=None, skill="standard"):
        return assess_slot(
            window=window or self.window,
            policy=policy or DayPolicy(self.day),
            required_skill=skill,
            technicians=(self.tech,) if techs is None else techs,
            bays=(self.bay,) if bays is None else bays,
        )

    def test_four_is_baseline_planning_cap_not_a_guarantee(self):
        self.assertIn("no_accepted_qualified_technician", self.check(techs=()).blockers)
        self.assertFalse(self.check(techs=()).can_offer_tentative_hold)

    def test_accepted_qualified_worker_and_free_bay_permit_tentative_hold(self):
        result = self.check()
        self.assertEqual(result.blockers, ())
        self.assertEqual(result.eligible_technician_ids, ("tech-a",))
        self.assertEqual(result.eligible_bay_ids, ("bay-1",))

    def test_concurrent_home_service_blocks_available_technician(self):
        busy = TechnicianAvailability("tech-a", frozenset({"standard"}),
                                      (period(self.day, 9, 13),), (period(self.day, 10, 12),))
        self.assertIn("no_accepted_qualified_technician", self.check(techs=(busy,)).blockers)

    def test_other_work_occupying_bay_blocks_offer(self):
        busy = BayAvailability("bay-1", (period(self.day, 9, 18),), (period(self.day, 10, 12),))
        self.assertIn("no_free_confirmed_bay", self.check(bays=(busy,)).blockers)

    def test_worker_is_not_qualified_for_premium_vehicle(self):
        self.assertIn("no_accepted_qualified_technician", self.check(skill="specialist").blockers)

    def test_already_four_jobs_blocks_fifth_even_when_worker_free(self):
        self.assertIn("daily_cap_reached", self.check(policy=DayPolicy(self.day, confirmed_gulf_jobs=4)).blockers)

    def test_approved_daily_override_allows_fifth_slot(self):
        self.assertTrue(self.check(policy=DayPolicy(self.day, confirmed_gulf_jobs=4,
                                                   authorised_day_cap=5)).can_offer_tentative_hold)

    def test_saturday_requires_staff_check_and_explicit_acceptance(self):
        sat = date(2026, 10, 10)
        slot = period(sat, 10, 11)
        worker = TechnicianAvailability("tech-a", frozenset({"standard"}), (period(sat, 9, 14),))
        bay = BayAvailability("bay-1", (period(sat, 9, 17),))
        self.assertIn("saturday_staff_check_required", self.check(
            window=slot, policy=DayPolicy(sat), techs=(worker,), bays=(bay,)).blockers)
        self.assertTrue(self.check(
            window=slot, policy=DayPolicy(sat, saturday_staff_check_done=True),
            techs=(worker,), bays=(bay,)).can_offer_tentative_hold)

    def test_sunday_never_opens_automatically(self):
        sun = date(2026, 10, 11)
        slot = period(sun, 10, 11)
        worker = TechnicianAvailability("tech-a", frozenset({"standard"}), (period(sun, 9, 15),))
        bay = BayAvailability("bay-1", (period(sun, 9, 15),))
        self.assertIn("sunday_opening_not_approved", self.check(
            window=slot, policy=DayPolicy(sun), techs=(worker,), bays=(bay,)).blockers)

    def test_wrong_date_or_crossing_local_midnight_blocks(self):
        mismatch = self.check(policy=DayPolicy(date(2026, 10, 13)))
        self.assertIn("invalid_service_day", mismatch.blockers)
        cross = TimeWindow(datetime(2026, 10, 12, 23, tzinfo=ZONE),
                           datetime(2026, 10, 13, 1, tzinfo=ZONE))
        self.assertIn("invalid_service_day", self.check(window=cross).blockers)

    def test_adjacent_non_overlapping_work_is_allowed(self):
        worker = TechnicianAvailability("tech-a", frozenset({"standard"}),
                                        (period(self.day, 9, 14),), (period(self.day, 9, 10),))
        self.assertTrue(self.check(techs=(worker,)).can_offer_tentative_hold)

    def test_time_window_validation(self):
        with self.assertRaises(ValueError):
            TimeWindow(datetime(2026, 10, 12, 10), datetime(2026, 10, 12, 11))
        with self.assertRaises(ValueError):
            TimeWindow(datetime(2026, 10, 12, 11, tzinfo=ZONE),
                       datetime(2026, 10, 12, 10, tzinfo=ZONE))
        with self.assertRaises(ValueError):
            DayPolicy(self.day, confirmed_gulf_jobs=-1)


if __name__ == "__main__":
    unittest.main()
