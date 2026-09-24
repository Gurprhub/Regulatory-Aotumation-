"""Controlled vocabularies for the compliance registers.

The vocabularies below reflect the Indian agrochemical regime (the Insecticides
Act 1968 and the Insecticides Rules 1971): registrations are granted centrally
by the CIB&RC, while licences to manufacture, sell, stock or store are granted
by state licensing authorities.
"""

from __future__ import annotations

from enum import StrEnum


class ProductCategory(StrEnum):
    INSECTICIDE = "insecticide"
    HERBICIDE = "herbicide"
    FUNGICIDE = "fungicide"
    RODENTICIDE = "rodenticide"
    NEMATICIDE = "nematicide"
    PLANT_GROWTH_REGULATOR = "plant_growth_regulator"
    BIOPESTICIDE = "biopesticide"
    OTHER = "other"


class FormulationType(StrEnum):
    """Technical grade plus the common GIFAP/CropLife formulation codes."""

    TC = "TC"  # technical concentrate
    TK = "TK"  # technical, in the form of a paste or melt
    EC = "EC"  # emulsifiable concentrate
    SC = "SC"  # suspension concentrate
    SL = "SL"  # soluble concentrate
    WP = "WP"  # wettable powder
    WG = "WG"  # water dispersible granule
    GR = "GR"  # granule
    DP = "DP"  # dustable powder
    ULV = "ULV"  # ultra low volume liquid
    OTHER = "OTHER"


class RegistrationSection(StrEnum):
    """Section of the Insecticides Act under which registration was granted."""

    SEC_9_3 = "9(3)"  # regular registration
    SEC_9_3B = "9(3B)"  # provisional registration
    SEC_9_4 = "9(4)"  # "me-too" registration
    SEC_9_3_IMPORT = "9(3)-import"
    SEC_9_4_IMPORT = "9(4)-import"


class RegistrationPurpose(StrEnum):
    MANUFACTURE = "manufacture"
    FORMULATION = "formulation"
    IMPORT = "import"
    EXPORT = "export"
    REPACKING = "repacking"


class LicenceType(StrEnum):
    MANUFACTURE = "manufacture"  # Form IV
    SALE = "sale"  # Form II / III
    STOCK_AND_SALE = "stock_and_sale"
    STORAGE = "storage"
    IMPORT_EXPORT = "import_export"
    PRINCIPAL_CERTIFICATE = "principal_certificate"


class ComplianceStatus(StrEnum):
    """Administrative status recorded against a certificate or licence."""

    ACTIVE = "active"
    UNDER_RENEWAL = "under_renewal"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    SURRENDERED = "surrendered"


class ComplianceState(StrEnum):
    """Derived health of an item, computed from its dates and status."""

    VALID = "valid"
    EXPIRING_SOON = "expiring_soon"
    CRITICAL = "critical"
    EXPIRED = "expired"
    NOT_YET_EFFECTIVE = "not_yet_effective"
    NON_COMPLIANT = "non_compliant"  # suspended / cancelled / surrendered


#: States and union territories that issue agrochemical licences.
INDIAN_STATES: tuple[str, ...] = (
    "Andhra Pradesh",
    "Arunachal Pradesh",
    "Assam",
    "Bihar",
    "Chhattisgarh",
    "Goa",
    "Gujarat",
    "Haryana",
    "Himachal Pradesh",
    "Jharkhand",
    "Karnataka",
    "Kerala",
    "Madhya Pradesh",
    "Maharashtra",
    "Manipur",
    "Meghalaya",
    "Mizoram",
    "Nagaland",
    "Odisha",
    "Punjab",
    "Rajasthan",
    "Sikkim",
    "Tamil Nadu",
    "Telangana",
    "Tripura",
    "Uttar Pradesh",
    "Uttarakhand",
    "West Bengal",
    "Andaman and Nicobar Islands",
    "Chandigarh",
    "Dadra and Nagar Haveli and Daman and Diu",
    "Delhi",
    "Jammu and Kashmir",
    "Ladakh",
    "Lakshadweep",
    "Puducherry",
)

STATE_LOOKUP: dict[str, str] = {state.casefold(): state for state in INDIAN_STATES}


def normalise_state(value: str) -> str:
    """Return the canonical state name, raising ``ValueError`` if unknown."""
    canonical = STATE_LOOKUP.get(value.strip().casefold())
    if canonical is None:
        raise ValueError(f"Unknown state or union territory: {value!r}")
    return canonical
