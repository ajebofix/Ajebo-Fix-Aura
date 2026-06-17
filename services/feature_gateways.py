# services/feature_gateways.py


FEATURE_CONSULTATIONS = "consultations"
FEATURE_CONCERN_REPORTING = "concern_reporting"
FEATURE_ASSESSMENTS = "assessments"
FEATURE_HISTORY = "history"
FEATURE_RINA_CHAT = "rina_chat"

FEATURE_PREVENTIVE_TRACKING = "preventive_tracking"
FEATURE_PROACTIVE_REMINDERS = "proactive_reminders"

FEATURE_PRIORITY_SCHEDULING = "priority_scheduling"
FEATURE_PRIORITY_COORDINATION = "priority_coordination"
FEATURE_EMERGENCY_REVIEW = "emergency_review"


# {care_plan: [features]}
CARE_PLAN_FEATURES = {
    "active_monitoring": [
        "consultations",
        "concern_reporting",
        "assessments",
        "history",
        "rina_chat",
    ],
    "preventive_coverage": [
        "consultations",
        "concern_reporting",
        "assessments",
        "history",
        "rina_chat",
        "preventive_tracking",
        "proactive_reminders",
    ],
    "priority_access": [
        "consultations",
        "concern_reporting",
        "assessments",
        "history",
        "rina_chat",
        "preventive_tracking",
        "proactive_reminders",
        "priority_scheduling",
        "emergency_review",
        "priority_coordination",
    ],
}


# {feature: {label: str, status: str}}
FEATURE_METADATA = {
    FEATURE_CONSULTATIONS: {
        "label": "Consultations",
        "status": "Included",
    },
    FEATURE_CONCERN_REPORTING: {
        "label": "Concern Reporting",
        "status": "Included",
    },
    FEATURE_ASSESSMENTS: {
        "label": "Vehicle Assessments",
        "status": "Included",
    },
    FEATURE_HISTORY: {
        "label": "Vehicle History",
        "status": "Included",
    },
    FEATURE_RINA_CHAT: {
        "label": "A.J. Rina Access",
        "status": "Included",
    },
    FEATURE_PREVENTIVE_TRACKING: {
        "label": "Preventive Tracking",
        "status": "Active",
    },
    FEATURE_PROACTIVE_REMINDERS: {
        "label": "Proactive Reminders",
        "status": "Active",
    },
    FEATURE_PRIORITY_SCHEDULING: {
        "label": "Priority Scheduling",
        "status": "Enabled",
    },
    FEATURE_PRIORITY_COORDINATION: {
        "label": "Priority Coordination",
        "status": "Enabled",
    },
    FEATURE_EMERGENCY_REVIEW: {
        "label": "Emergency Escalation",
        "status": "Enabled",
    },
}


def has_feature(ownership, feature):

    if not ownership:
        return False

    care_plan = ownership.care_plan if ownership.care_plan else "active_monitoring"

    features = CARE_PLAN_FEATURES.get(care_plan, [])

    return feature in features
