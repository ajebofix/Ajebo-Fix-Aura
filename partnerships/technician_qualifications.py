"""Technician capability labels, separate from vehicle service class.

Class determines what size/type of job the worker can lead.
Brand specialisation restricts which makes the worker may be offered.
Human vehicle/OEM specification approval remains independently mandatory.
"""
SERVICE_CLASSES = frozenset({"car", "suv", "exception"})
BRAND_SPECIALTIES = frozenset({
    "mercedes", "bmw", "toyota", "lexus", "japanese_other", "other",
})


def classify_make(make: str) -> str:
    name = (make or "").strip().lower().replace("-", " ").replace("_", " ")
    name = " ".join(name.split())
    if name in {"mercedes", "mercedes benz", "benz", "mercedesbenz"}:
        return "mercedes"
    if name in {"bmw", "b m w"}:
        return "bmw"
    if name == "toyota":
        return "toyota"
    if name == "lexus":
        return "lexus"
    if name in {
        "honda", "nissan", "infiniti", "acura", "mazda", "mitsubishi",
        "suzuki", "subaru", "isuzu", "daihatsu",
    }:
        return "japanese_other"
    return "other"


def split_qualifications(raw: str, allowed: frozenset[str]) -> frozenset[str]:
    values = frozenset(x.strip().lower() for x in (raw or "").split(",") if x.strip())
    if not values or not values.issubset(allowed):
        raise ValueError("Select at least one valid capability option.")
    return values
