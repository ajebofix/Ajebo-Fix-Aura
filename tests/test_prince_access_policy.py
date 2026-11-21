"""Boundary tests for Prince pilot policy; no real customer data required."""
import unittest

from partnerships.prince_access_policy import (
    PrinceAction,
    PrinceActor,
    PrinceRole,
    authorize,
    operator_intake_response,
    redact_operator_job,
    validate_operator_intake_keys,
)


class PrinceAccessPolicyTests(unittest.TestCase):
    def setUp(self):
        self.seun = PrinceActor(user_id=10, tenant_id="prince", role=PrinceRole.OPERATOR)

    def test_operator_can_create_intake_only_in_own_tenant(self):
        self.assertTrue(authorize(self.seun, tenant_id="prince", action=PrinceAction.INTAKE_CREATE))
        self.assertFalse(authorize(self.seun, tenant_id="ajebo", action=PrinceAction.INTAKE_CREATE))

    def test_operator_cannot_read_unassigned_job(self):
        self.assertFalse(authorize(self.seun, tenant_id="prince", action=PrinceAction.JOB_READ))
        self.assertFalse(authorize(self.seun, tenant_id="prince", action=PrinceAction.JOB_READ, assigned_user_ids=frozenset({11})))
        self.assertTrue(authorize(self.seun, tenant_id="prince", action=PrinceAction.JOB_READ, assigned_user_ids=frozenset({10})))

    def test_operator_cannot_read_contact_or_approve_money(self):
        for action in (PrinceAction.CONTACT_READ, PrinceAction.CONTACT_CORRECT,
                       PrinceAction.DIRECTORY_EXPORT, PrinceAction.ESTIMATE_APPROVE,
                       PrinceAction.PAYMENT_VERIFY, PrinceAction.SERVICE_CONFIRM):
            with self.subTest(action=action):
                self.assertFalse(authorize(self.seun, tenant_id="prince", action=action, assigned_user_ids=frozenset({10})))

    def test_disabled_account_denied(self):
        disabled = PrinceActor(user_id=10, tenant_id="prince", role=PrinceRole.OPERATOR, active=False)
        self.assertFalse(authorize(disabled, tenant_id="prince", action=PrinceAction.INTAKE_CREATE))

    def test_operator_job_allowlist_excludes_contact_and_banking(self):
        record = dict(customer_id=5, full_name="Test Customer", job_id=7, vehicle_id=9,
                      phone_number="REDACT", email="REDACT", bank_reference="REDACT",
                      vin="REDACT", vehicle_plate="REDACT", secret_notes="REDACT")
        response = redact_operator_job(record)
        self.assertEqual(response, {"customer_id": 5, "full_name": "Test Customer", "job_id": 7, "vehicle_id": 9})

    def test_intake_response_does_not_echo_submitted_private_fields(self):
        record = dict(customer_id=5, full_name="Test Customer", phone_number="REDACT",
                      email="REDACT", home_address="REDACT")
        self.assertEqual(operator_intake_response(record),
                         {"customer_id": 5, "full_name": "Test Customer"})

    def test_intake_rejects_unapproved_fields(self):
        self.assertTrue(validate_operator_intake_keys({"full_name": "Test Customer", "phone_number": "000"}))
        self.assertFalse(validate_operator_intake_keys({"full_name": "Test Customer", "bank_pin": "1234"}))
        self.assertFalse(validate_operator_intake_keys({"phone_number": "000"}))

    def test_admin_cannot_verify_payments_by_default(self):
        admin = PrinceActor(user_id=1, tenant_id="prince", role=PrinceRole.PILOT_ADMIN)
        self.assertFalse(authorize(admin, tenant_id="prince", action=PrinceAction.PAYMENT_VERIFY))


if __name__ == "__main__":
    unittest.main()
