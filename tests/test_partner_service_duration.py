"""Gulf pilot: actual oil-change time excludes inspection, photos and handover."""

import unittest

from partnerships.service_duration import ServiceTimePolicy, recommended_planning_minutes


class ServiceDurationTests(unittest.TestCase):
    def test_observed_oil_change_is_thirty_to_forty_five_minutes_only(self):
        policy = ServiceTimePolicy()
        self.assertEqual((policy.observed_min_minutes, policy.observed_max_minutes), (30, 45))

    def test_ninety_minute_internal_hold_is_not_confirmed_service_duration(self):
        policy = ServiceTimePolicy()
        self.assertEqual(policy.provisional_hold_only_minutes, 90)
        self.assertIsNone(recommended_planning_minutes(policy))

    def test_approved_checklist_does_not_fake_missing_timing_estimates(self):
        policy = ServiceTimePolicy(ten_point_scope_agreed=True)
        self.assertIsNone(recommended_planning_minutes(policy))

    def test_missing_one_extra_task_still_produces_unknown(self):
        policy = ServiceTimePolicy(
            ten_point_scope_agreed=True,
            inspection_minutes=15, photo_minutes=5, service_record_minutes=5,
        )
        self.assertIsNone(recommended_planning_minutes(policy))

    def test_all_approved_extra_steps_generate_end_to_end_planning_time(self):
        policy = ServiceTimePolicy(
            ten_point_scope_agreed=True,
            inspection_minutes=15,
            photo_minutes=5,
            service_record_minutes=5,
            handover_minutes=5,
        )
        self.assertEqual(recommended_planning_minutes(policy), 90)

    def test_unapproved_checklist_blocks_even_if_someone_entered_durations(self):
        policy = ServiceTimePolicy(
            inspection_minutes=15, photo_minutes=5,
            service_record_minutes=5, handover_minutes=5,
        )
        self.assertIsNone(recommended_planning_minutes(policy))

    def test_more_work_can_exceed_internal_ninety_minute_placeholder(self):
        policy = ServiceTimePolicy(
            ten_point_scope_agreed=True,
            inspection_minutes=30, photo_minutes=10,
            service_record_minutes=10, handover_minutes=10,
        )
        self.assertEqual(recommended_planning_minutes(policy), 120)
        self.assertGreater(
            recommended_planning_minutes(policy),
            policy.provisional_hold_only_minutes,
        )

    def test_toyota_and_mercedes_do_not_have_default_brand_multipliers(self):
        policy = ServiceTimePolicy(
            ten_point_scope_agreed=True,
            inspection_minutes=15, photo_minutes=5,
            service_record_minutes=5, handover_minutes=5,
        )
        toyota = recommended_planning_minutes(policy)
        mercedes = recommended_planning_minutes(policy)
        self.assertEqual(toyota, mercedes)

    def test_justified_vehicle_specific_extra_time_is_possible(self):
        policy = ServiceTimePolicy(
            ten_point_scope_agreed=True,
            inspection_minutes=15, photo_minutes=5,
            service_record_minutes=5, handover_minutes=5,
        )
        self.assertEqual(
            recommended_planning_minutes(policy, vehicle_specific_extra_minutes=20),
            110,
        )

    def test_zero_task_time_can_be_recorded_if_work_precompleted(self):
        policy = ServiceTimePolicy(
            ten_point_scope_agreed=True,
            inspection_minutes=10, photo_minutes=0,
            service_record_minutes=0, handover_minutes=0,
        )
        self.assertEqual(recommended_planning_minutes(policy), 70)

    def test_negative_and_invalid_durations_rejected(self):
        cases = [
            {"observed_min_minutes": 0},
            {"observed_max_minutes": 20},
            {"provisional_buffer_minutes": -1},
            {"provisional_internal_hold_minutes": 0},
            {"provisional_internal_hold_minutes": 40},
            {"inspection_minutes": -1},
            {"photo_minutes": -1},
            {"service_record_minutes": -1},
            {"handover_minutes": -1},
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                ServiceTimePolicy(**kwargs)
        with self.assertRaises(ValueError):
            recommended_planning_minutes(
                ServiceTimePolicy(), vehicle_specific_extra_minutes=-1
            )


if __name__ == "__main__":
    unittest.main()
