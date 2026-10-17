"""Policy foundation tests: no implied or automatic warranty, ever."""

from copy import deepcopy

import pytest

from services.workmanship_assurance_policy import (
    AssuranceProposalError,
    POLICY_VERSION,
    validate_proposed_assurance,
)


@pytest.fixture
def baseline():
    return {
        "car_id": 1001,
        "advisor_user_id": 80,
        "job_reference": "ANONYMISED-JOB-001",
        "approved_labour_ngn": "70000",
        "approved_scope": "Specified collision panel fit and finish; no hidden structure",
        "vehicle_condition_baseline": "Previous accident damage documented",
        "prior_damage_and_repairs": "Prior paint/filler visible at front left",
        "parts_category": "client_supplied",
        "parts_provenance_and_supplier": "Owner purchased used panel directly",
        "seller_testing_return_terms": "Seller warranty unknown; written proof requested",
        "materials_and_repair_method": "Client-selected paint system; prep recorded",
        "technical_risk_and_hidden_damage": "Underlying panel alignment unverified",
        "testing_and_quality_control": "Panel-fit inspection and finish photos",
        "client_choices_and_declined_recommendations": "Declined new panel",
        "environmental_use_and_aftercare": "Flood, potholes, washing and curing noted",
        "advisor_reason": "Budget and previously damaged body limit optional commitments",
        "proposed_decision": "documented_service_no_additional_guarantee",
    }


def test_budget_collision_case_does_not_create_a_voluntary_guarantee(baseline):
    result = validate_proposed_assurance(baseline)
    assert result["policy_version"] == POLICY_VERSION
    assert result["evidence_complete"] is True
    assert result["review_status"] == "ready_for_human_review"
    assert result["requires_manual_approval"] is True
    assert result["additional_contractual_assurance_issued"] is False
    assert result["external_publication_permitted"] is False
    assert result["statutory_rights_unaffected"] is True


def test_tokunbo_supplier_three_day_window_is_not_auto_workmanship_term(baseline):
    review = deepcopy(baseline)
    review["parts_category"] = "preowned_tokunbo"
    review["parts_provenance_and_supplier"] = "Used imported engine; client supplier"
    review["seller_testing_return_terms"] = "Seller reported 3-day testing window"
    review["approved_labour_ngn"] = "120000"
    review["proposed_decision"] = "documented_service_no_additional_guarantee"
    result = validate_proposed_assurance(review)
    assert result["review_status"] == "ready_for_human_review"
    assert not result["additional_contractual_assurance_issued"]


def test_fully_documented_service_still_does_not_auto_grant_coverage(baseline):
    review = deepcopy(baseline)
    review.update(
        job_reference="ANONYMISED-JOB-003",
        parts_category="new_oem",
        parts_provenance_and_supplier="Genuine documented new component",
        approved_labour_ngn="450000",
        proposed_decision="tailored_extended_assurance_proposed",
        additional_coverage_scope="Specified installation workmanship only",
        additional_coverage_conditions="Installation defect directly attributable to agreed work",
        coverage_start_date="2026-11-01",
        coverage_end_date="2026-11-15",
    )
    result = validate_proposed_assurance(review)
    assert result["evidence_complete"]
    assert not result["additional_contractual_assurance_issued"]
    assert not result["external_publication_permitted"]


def test_extra_coverage_requires_individual_written_scope_and_dates(baseline):
    review = deepcopy(baseline)
    review["proposed_decision"] = "limited_additional_workmanship_assurance_proposed"
    result = validate_proposed_assurance(review)
    assert result["review_status"] == "needs_evidence"
    assert "additional_coverage_scope" in result["missing_evidence"]
    assert "individual_coverage_dates" in result["missing_evidence"]


def test_unverified_parts_fail_closed_without_blanket_exclusion(baseline):
    review = deepcopy(baseline)
    review["parts_provenance_and_supplier"] = ""
    result = validate_proposed_assurance(review)
    assert not result["evidence_complete"]
    assert "parts_provenance_and_supplier" in result["missing_evidence"]


def test_no_guarantee_proposal_cannot_carry_hidden_extra_promise(baseline):
    review = deepcopy(baseline)
    review["additional_coverage_scope"] = "General engine guarantee"
    with pytest.raises(AssuranceProposalError, match="Do not attach"):
        validate_proposed_assurance(review)


def test_end_date_must_follow_proposed_start_date(baseline):
    review = deepcopy(baseline)
    review.update(
        proposed_decision="limited_additional_workmanship_assurance_proposed",
        additional_coverage_scope="Installation",
        additional_coverage_conditions="Covered installation error only",
        coverage_start_date="2026-11-10",
        coverage_end_date="2026-11-02",
    )
    with pytest.raises(AssuranceProposalError, match="must follow"):
        validate_proposed_assurance(review)


def test_missing_verified_job_or_advisor_rejected(baseline):
    review = deepcopy(baseline)
    review["job_reference"] = ""
    with pytest.raises(AssuranceProposalError, match="specific job"):
        validate_proposed_assurance(review)
    review["job_reference"] = "TEST-JOB"
    review["advisor_user_id"] = 0
    with pytest.raises(AssuranceProposalError, match="identified advisor"):
        validate_proposed_assurance(review)


def test_rejected_scope_must_not_have_coverage_terms(baseline):
    review = deepcopy(baseline)
    review["proposed_decision"] = "decline_or_redesign_unsafe_scope"
    assert not validate_proposed_assurance(review)["additional_contractual_assurance_issued"]
    review["coverage_end_date"] = "2026-12-01"
    with pytest.raises(AssuranceProposalError, match="Do not attach"):
        validate_proposed_assurance(review)
