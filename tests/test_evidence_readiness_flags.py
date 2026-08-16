from evidence.readiness import evaluate_evidence_cutover_readiness


def _base_config() -> dict[str, object]:
    return {
        "EVIDENCE_IMAGE_INTAKE_ENABLED": "0",
        "EVIDENCE_RETRIEVAL_ENABLED": "false",
        "EVIDENCE_ADVISOR_REVIEW_ENABLED": "no",
        "EVIDENCE_TIMELINE_ENABLED": "off",
        "EVIDENCE_ADVISOR_DELETION_ENABLED": "",
        "EVIDENCE_RETENTION_DAYS": None,
        "EVIDENCE_RETRIEVAL_GRANT_SECONDS": None,
        "EVIDENCE_STORAGE_PROVIDER": "r2",
        "R2_ACCOUNT_ID": None,
        "R2_ACCESS_KEY_ID": None,
        "R2_SECRET_ACCESS_KEY": None,
        "R2_BUCKET": None,
    }


def test_raw_false_feature_flag_strings_are_not_treated_as_enabled():
    readiness = evaluate_evidence_cutover_readiness(_base_config())

    assert readiness.ready is True
    assert readiness.state == "disabled"
    assert readiness.enabled_features == ()
    assert readiness.storage_required is False


def test_raw_true_feature_flag_strings_are_parsed_consistently():
    config = _base_config()
    config.update(
        {
            "EVIDENCE_TIMELINE_ENABLED": "YES",
            "EVIDENCE_ADVISOR_REVIEW_ENABLED": "On",
            "EVIDENCE_RETRIEVAL_ENABLED": "1",
            "EVIDENCE_RETRIEVAL_GRANT_SECONDS": "120",
            "R2_ACCOUNT_ID": "flag-test-account",
            "R2_ACCESS_KEY_ID": "flag-test-access",
            "R2_SECRET_ACCESS_KEY": "flag-test-secret",
            "R2_BUCKET": "flag-test-bucket",
        }
    )

    readiness = evaluate_evidence_cutover_readiness(config)

    assert readiness.ready is True
    assert readiness.state == "ready"
    assert readiness.enabled_features == ("retrieval", "advisor_review", "timeline")
    assert readiness.storage_required is True


def test_unknown_raw_feature_flag_string_fails_closed_as_disabled():
    config = _base_config()
    config["EVIDENCE_IMAGE_INTAKE_ENABLED"] = "definitely-maybe"

    readiness = evaluate_evidence_cutover_readiness(config)

    assert readiness.enabled_features == ()
    assert readiness.storage_required is False
