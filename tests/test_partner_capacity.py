"""Contract tests: daily Gulf throughput versus independent parallel capacity."""

import unittest
from dataclasses import replace
from datetime import date, datetime
from zoneinfo import ZoneInfo

from partnerships.capacity import (
    BayAvailability, DayPolicy, TechnicianAvailability, TimeWindow, assess_slot,
)

ZONE = ZoneInfo("Africa/Lagos")


def period(day, start_hour, end_hour):
    return TimeWindow(
        datetime(day.year, day.month, day.day, start_hour, tzinfo=ZONE),
        datetime(day.year, day.month, day.day, end_hour, tzinfo=ZONE),
    )


class FlexibleCapacityTests(unittest.TestCase):
    def setUp(self):
        self.day = date(2026, 10, 12)  # Monday
        self.window = period(self.day, 10, 11)
        self.tech = TechnicianAvailability(
            "tech-a", frozenset({"standard"}), (period(self.day, 9, 13),)
        )
        self.bay = BayAvailability("bay-1", (period(self.day, 9, 18),))

    def check(
        self, *, policy=None, techs=None, bays=None,
        window=None, skill="standard", concurrent=0,
    ):
        return assess_slot(
            window=window or self.window,
            policy=policy or DayPolicy(self.day),
            required_skill=skill,
            technicians=(self.tech,) if techs is None else techs,
            bays=(self.bay,) if bays is None else bays,
            overlapping_oil_jobs=concurrent,
        )

    def test_six_is_conservative_daily_ceiling_not_guaranteed_capacity(self):
        self.assertEqual(DayPolicy(self.day).max_bookings, 6)
        self.assertEqual(DayPolicy(self.day).max_concurrent_oil_jobs, 2)
        result = self.check(techs=())
        self.assertIn("no_accepted_qualified_technician", result.blockers)
        self.assertFalse(result.can_offer_tentative_hold)

    def test_qualified_worker_and_free_bay_allow_advisory_hold(self):
        result = self.check()
        self.assertEqual(result.blockers, ())
        self.assertEqual(result.eligible_technician_ids, ("tech-a",))
        self.assertEqual(result.eligible_bay_ids, ("bay-1",))

    def test_home_service_conflict_blocks_technician(self):
        busy = replace(self.tech, other_commitments=(period(self.day, 10, 12),))
        self.assertIn(
            "no_accepted_qualified_technician",
            self.check(techs=(busy,)).blockers,
        )

    def test_other_work_bay_collision(self):
        busy = replace(self.bay, other_commitments=(period(self.day, 10, 12),))
        self.assertIn("no_free_confirmed_bay", self.check(bays=(busy,)).blockers)

    def test_worker_without_specialist_qualification(self):
        self.assertIn(
            "no_accepted_qualified_technician",
            self.check(skill="specialist").blockers,
        )

    def test_intern_assists_but_is_not_unsupervised_primary_technician(self):
        intern = TechnicianAvailability(
            "intern", frozenset({"standard"}),
            (period(self.day, 9, 13),),
            is_intern=True,
        )
        self.assertIn(
            "no_accepted_qualified_technician",
            self.check(techs=(intern,)).blockers,
        )
        self.assertTrue(self.check(techs=(intern, self.tech)).can_offer_tentative_hold)
        self.assertEqual(self.check(techs=(intern, self.tech)).eligible_technician_ids, ("tech-a",))

    def test_sixth_daily_slot_is_possible_if_resources_exist(self):
        self.assertTrue(
            self.check(policy=DayPolicy(self.day, counted_gulf_jobs=5)).can_offer_tentative_hold
        )

    def test_seventh_daily_slot_requires_explicit_day_override(self):
        policy = DayPolicy(self.day, counted_gulf_jobs=6)
        self.assertIn("daily_cap_reached", self.check(policy=policy).blockers)
        policy = replace(policy, authorised_day_cap=7)
        self.assertTrue(self.check(policy=policy).can_offer_tentative_hold)

    def test_eighth_booking_rejected_even_after_seventh_slot_approved(self):
        policy = DayPolicy(self.day, counted_gulf_jobs=7, authorised_day_cap=7)
        self.assertIn("daily_cap_reached", self.check(policy=policy).blockers)

    def test_two_parallel_oil_changes_is_default_ceiling(self):
        self.assertTrue(self.check(concurrent=1).can_offer_tentative_hold)
        self.assertIn("concurrent_oil_capacity_reached", self.check(concurrent=2).blockers)

    def test_reduced_crew_can_use_three_parallel_slots_after_explicit_approval(self):
        policy = DayPolicy(self.day, authorised_concurrent_cap=3)
        self.assertTrue(self.check(policy=policy, concurrent=2).can_offer_tentative_hold)
        self.assertIn(
            "concurrent_oil_capacity_reached",
            self.check(policy=policy, concurrent=3).blockers,
        )

    def test_full_crew_can_use_four_parallel_slots_after_explicit_approval(self):
        policy = DayPolicy(self.day, authorised_concurrent_cap=4)
        self.assertTrue(self.check(policy=policy, concurrent=3).can_offer_tentative_hold)
        self.assertIn(
            "concurrent_oil_capacity_reached",
            self.check(policy=policy, concurrent=4).blockers,
        )

    def test_daily_quota_and_parallel_slots_are_independent(self):
        # Full staffing never authorises an eighth Gulf job that day.
        policy = DayPolicy(self.day, counted_gulf_jobs=7,
                           authorised_day_cap=7, authorised_concurrent_cap=4)
        self.assertIn(
            "daily_cap_reached", self.check(policy=policy, concurrent=0).blockers,
        )
        # A day may have six jobs scheduled but 0/2 concurrent at 10 a.m.
        self.assertTrue(self.check(
            policy=DayPolicy(self.day, counted_gulf_jobs=5), concurrent=0
        ).can_offer_tentative_hold)

    def test_staff_or_bay_still_required_when_concurrent_ceiling_raised(self):
        policy = DayPolicy(self.day, authorised_concurrent_cap=4)
        self.assertIn(
            "no_free_confirmed_bay",
            self.check(policy=policy, concurrent=3, bays=()).blockers,
        )

    def test_saturday_requires_staff_check_and_explicit_acceptance(self):
        sat = date(2026, 10, 10)
        window = period(sat, 10, 11)
        worker = replace(self.tech, accepted_windows=(period(sat, 9, 14),))
        bay = replace(self.bay, open_windows=(period(sat, 9, 17),))
        self.assertIn(
            "saturday_staff_check_required",
            self.check(window=window, policy=DayPolicy(sat), techs=(worker,), bays=(bay,)).blockers,
        )
        self.assertTrue(self.check(
            window=window, policy=DayPolicy(sat, saturday_staff_check_done=True),
            techs=(worker,), bays=(bay,),
        ).can_offer_tentative_hold)

    def test_sunday_stays_manual(self):
        sun = date(2026, 10, 11)
        worker = replace(self.tech, accepted_windows=(period(sun, 9, 14),))
        bay = replace(self.bay, open_windows=(period(sun, 9, 17),))
        self.assertIn(
            "sunday_opening_not_approved",
            self.check(window=period(sun, 10, 11), policy=DayPolicy(sun),
                       techs=(worker,), bays=(bay,)).blockers,
        )

    def test_invalid_day_or_cross_midnight(self):
        self.assertIn(
            "invalid_service_day",
            self.check(policy=DayPolicy(date(2026, 10, 13))).blockers,
        )
        cross = TimeWindow(datetime(2026, 10, 12, 23, tzinfo=ZONE),
                           datetime(2026, 10, 13, 1, tzinfo=ZONE))
        self.assertIn("invalid_service_day", self.check(window=cross).blockers)

    def test_adjacent_other_job_is_not_overlap(self):
        tech = replace(
            self.tech, other_commitments=(period(self.day, 9, 10),)
        )
        self.assertTrue(self.check(techs=(tech,)).can_offer_tentative_hold)

    def test_inputs_reject_unverified_time_and_capacity_beyond_attested_limits(self):
        with self.assertRaises(ValueError):
            TimeWindow(datetime(2026, 10, 12, 10), datetime(2026, 10, 12, 11))
        with self.assertRaises(ValueError):
            TimeWindow(datetime(2026, 10, 12, 11, tzinfo=ZONE),
                       datetime(2026, 10, 12, 10, tzinfo=ZONE))
        for args in (
            {"counted_gulf_jobs": -1},
            {"baseline_day_cap": 8},
            {"baseline_concurrent_cap": 5},
            {"authorised_day_cap": 8},
            {"authorised_concurrent_cap": 5},
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                DayPolicy(self.day, **args)
        with self.assertRaises(ValueError):
            self.check(concurrent=-1)


if __name__ == "__main__":
    unittest.main()
