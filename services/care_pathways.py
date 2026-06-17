# services/care_pathways.py

CARE_PLAN_ACTIVE_MONITORING = "active_monitoring"
CARE_PLAN_PREVENTIVE = "preventive_coverage"
CARE_PLAN_PRIORITY = "priority_access"


CARE_PLAN_LABELS = {
    CARE_PLAN_ACTIVE_MONITORING: "Active Monitoring",
    CARE_PLAN_PREVENTIVE: "Preventive Coverage",
    CARE_PLAN_PRIORITY: "Priority Access",
}


def get_care_plan(ownership):
    """
    Safe care plan resolver.
    """

    if not ownership:
        return CARE_PLAN_ACTIVE_MONITORING

    return ownership.care_plan or CARE_PLAN_ACTIVE_MONITORING


def has_preventive_coverage(ownership):
    """
    Preventive pathways enabled.
    """

    return get_care_plan(ownership) in [
        CARE_PLAN_PREVENTIVE,
        CARE_PLAN_PRIORITY,
    ]


def has_priority_access(ownership):
    """
    Highest-tier accelerated care access.
    """

    return get_care_plan(ownership) == CARE_PLAN_PRIORITY
