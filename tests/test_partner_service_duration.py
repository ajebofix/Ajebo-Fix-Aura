"""Gulf oil-change timing: brand-neutral, checklist-dependent, configurable."""

import unittest
from dataclasses import replace

from partnerships.service_duration import ServiceTimePolicy, recommended_planning_minutes


class ServiceDurationTests(unittest.TestCase):
    def test_observed_range_and_provisional_buffer(self):
        policy = ServiceTimePolicy()
        self.assertEqual((policy.observed_min_minutes, policy.observed_max_minutes), (30, 45))
        self.assertEqual(policy.base_provisional_minutes, 60)

    def test_unknown_ten_point_check_blocks_final_duration(self):
        policy = ServiceTimePolicy()
        self.assertIsNone(policy.confirmed_scope_planning_minutes)
        self.assertIsNone(recommended_planning_minutes(policy))

    def test_checklist_scope_agreed_but_inclusion_unknown_still_blocks(self):
        policy = ServiceTimePolicy(ten_point_scope_agreed=True)
        self.assertIsNone(policy.confirmed_scope_planning_minutes)

    def test_included_checklist_uses_sixty_minute_first_estimate(self):
        policy = ServiceTimePolicy(
            ten_point_scope_agreed=True, check_included_in_observed_minutes=True,
        )
        self.assertEqual(recommended_planning_minutes(policy), 60)

    def test_separate_check_requires_measured_duration(self):
        policy = ServiceTimePolicy(
            ten_point_scope_agreed=True, check_included_in_observed_minutes=False,
        )
        self.assertIsNone(recommended_planning_minutes(policy))
        self.assertEqual(
            recommended_planning_minutes(replace(policy, extra_check_minutes=12)),
            72,
        )

    def test_mercedes_not_automatically_slower_than_toyota(self):
        policy = ServiceTimePolicy(
            ten_point_scope_agreed=True, check_included_in_observed_minutes=True,
        )
        toyota_estimate = recommended_planning_minutes(policy)
        mercedes_estimate = recommended_planning_minutes(policy)
        self.assertEqual(toyota_estimate, mercedes_estimate)

    def test_vehicle_specific_extra_time_is_explicit_not_brand_assumption(self):
        policy = ServiceTimePolicy(
            ten_point_scope_agreed=True, check_included_in_observed_minutes=True,
        )
        self.assertEqual(
            recommended_planning_minutes(policy, vehicle_specific_extra_minutes=20),
            80,
        )

    def test_negative_or_invalid_durations_rejected(self):
        for kwargs in (
            {"observed_min_minutes": 0},
            {"observed_max_minutes": 20},
            {"provisional_buffer_minutes": -1},
            {"extra_check_minutes": -2},
            {"check_included_in_observed_minutes": True, "extra_check_minutes": 5},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ServiceTimePolicy(**kwargs)
        with self.assertRaises(ValueError):
            recommended_planning_minutes(
                ServiceTimePolicy(ten_point_scope_agreed=True,
                                  check_included_in_observed_minutes=True),
                vehicle_specific_extra_minutes=-3,
            )


if __name__ == "__main__":
    unittest.main()
