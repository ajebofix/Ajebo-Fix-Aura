"""Deterministic, non-diagnostic vehicle illustration selection.

The assets are original family-style concept silhouettes. A family match is
not proof of exact trim/generation/body kit. No VIN or health inference is
performed; never label a generic illustration as an exact verified vehicle.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class VehicleHero:
    asset: str
    family: str
    label: str
    match: str  # family, category, generic


def _tokens(value: object) -> str:
    value = str(value or "").casefold()
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9]+", " ", value)).strip()


# Ordered: GLK and GLS must not be confused with GL; GLE before G.
# Regex only selects an illustration family, never a chassis or trim.
_MERCEDES_FAMILIES = (
    ("glk", r"\bglk(?:\s*\d{3})?\b", "Mercedes-Benz GLK"),
    ("g-class", r"\b(?:g\s*(?:63|65|55|550|500|350|400|450|580)|g\s*class|gelandewagen)\b", "Mercedes-Benz G-Class"),
    ("gle", r"\bgle(?:\s*\d{2,3})?\b", "Mercedes-Benz GLE"),
    ("gl-gls", r"\b(?:gls|gl)(?:\s*\d{3})?\b", "Mercedes-Benz GL / GLS"),
    ("ml", r"\b(?:ml|m\s*class)(?:\s*\d{3})?\b", "Mercedes-Benz ML"),
    ("s-class", r"\b(?:s\s*\d{3}|s\s*class|maybach\s*s)\b", "Mercedes-Benz S-Class"),
    ("e-class", r"\b(?:e\s*\d{3}|e\s*class)\b", "Mercedes-Benz E-Class"),
    ("c-class", r"\b(?:c\s*\d{3}|c\s*class)\b", "Mercedes-Benz C-Class"),
)

_CATEGORY_ASSETS = {
    "suv": ("suv", "Luxury SUV"),
    "crossover": ("suv", "Luxury SUV"),
    "sport utility": ("suv", "Luxury SUV"),
    "jeep": ("suv", "Luxury SUV"),
    "wagon": ("sedan", "Passenger vehicle"),
    "saloon": ("sedan", "Executive sedan"),
    "sedan": ("sedan", "Executive sedan"),
    "coupe": ("sedan", "Passenger vehicle"),
    "hatchback": ("sedan", "Passenger vehicle"),
}


def resolve_vehicle_hero(car: object) -> VehicleHero:
    """Select only a bundled local asset. No remote call or new permission."""
    brand = _tokens(getattr(car, "brand", ""))
    model = _tokens(getattr(car, "model", ""))

    if brand in {"mercedes benz", "mercedes", "mercedesbenz"}:
        for key, pattern, label in _MERCEDES_FAMILIES:
            if re.search(pattern, model):
                return VehicleHero(
                    asset=f"vehicle-heroes/mercedes/{key}.svg",
                    family=key,
                    label=f"{label} family-style concept illustration",
                    match="family",
                )

    profile = getattr(car, "vehicle_profile", None)
    body = _tokens(getattr(profile, "body_style", "") if profile else "")
    for fragment, (key, label) in _CATEGORY_ASSETS.items():
        if fragment in body:
            return VehicleHero(
                asset=f"vehicle-heroes/fallback/{key}.svg",
                family=key,
                label=f"{label} category illustration",
                match="category",
            )

    return VehicleHero(
        asset="vehicle-heroes/fallback/generic.svg",
        family="generic",
        label="Unspecified vehicle concept illustration",
        match="generic",
    )
