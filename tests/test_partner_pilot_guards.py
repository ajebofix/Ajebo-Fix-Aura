"""Contract checks for the isolated partner-pilot readiness gates."""

import unittest
from dataclasses import replace

from partnerships.pilot_guards import (
    AppointmentReadiness,
    ServiceStartReadiness,
    SettlementReadiness,
    appointment_blockers,
    service_start_blockers,
    settlement_blockers,
)


class PartnerPilotGuardTests(unittest.TestCase):
    def test_new_request_is_not_bookable(self):
        blockers = appointment_blockers(AppointmentReadiness())
        self.assertIn("campaign_inactive", blockers)
        self.assertIn("voucher_unverified", blockers)
        self.assertIn("technical_approval_missing", blockers)
        self.assertIn("products_not_accepted", blockers)

    def test_voucher_alone_does_not_confirm_booking(self):
        blockers = appointment_blockers(
            AppointmentReadiness(campaign_active=True, voucher_verified=True)
        )
        self.assertNotIn("voucher_unverified", blockers)
        self.assertIn("products_not_accepted", blockers)
        self.assertIn("technical_approval_missing", blockers)

    def test_all_explicit_requirements_allow_confirmation(self):
        self.assertEqual(
            appointment_blockers(
                AppointmentReadiness(
                    campaign_active=True,
                    voucher_verified=True,
                    human_technical_approval=True,
                    product_received_and_accepted=True,
                    capacity_reserved=True,
                    technician_confirmed_for_window=True,
                    customer_confirmed=True,
                )
            ),
            (),
        )

    def test_cannot_start_service_without_voucher_bay_and_vehicle_recheck(self):
        blockers = service_start_blockers(
            ServiceStartReadiness(appointment_confirmed=True)
        )
        self.assertIn("voucher_not_reserved_for_job", blockers)
        self.assertIn("vehicle_identity_not_rechecked", blockers)
        self.assertIn("products_not_at_bay", blockers)

    def test_all_service_start_requirements(self):
        self.assertEqual(
            service_start_blockers(
                ServiceStartReadiness(True, True, True, True, True)
            ),
            (),
        )

    def test_completed_work_without_evidence_cannot_be_claimed(self):
        blockers = settlement_blockers(
            SettlementReadiness(
                agreed_rate_available=True,
                voucher_redemption_acknowledged=True,
                service_completed=True,
            )
        )
        self.assertIn("evidence_not_reviewed", blockers)
        self.assertIn("handover_unconfirmed", blockers)

    def test_duplicate_claim_blocked_even_when_all_other_facts_complete(self):
        ready = SettlementReadiness(True, True, True, True, True, False)
        self.assertEqual(settlement_blockers(ready), ())
        self.assertEqual(
            settlement_blockers(replace(ready, existing_claim=True)),
            ("duplicate_claim",),
        )


if __name__ == "__main__":
    unittest.main()
