"""Request and response schemas.

Read schemas expose the derived compliance fields as computed properties, so
every representation of a record carries an up-to-date renewal verdict without
the caller having to ask for it separately.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Self

from pydantic import (
    AliasChoices,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.compliance import days_remaining as _days_remaining
from app.compliance import evaluate as _evaluate
from app.reference import (
    ComplianceState,
    ComplianceStatus,
    FormulationType,
    LicenceType,
    ProductCategory,
    RegistrationPurpose,
    RegistrationSection,
    Role,
    normalise_state,
)

NonEmptyStr = Annotated[str, Field(min_length=1, max_length=500)]

#: Email addresses are stored folded to lower case so sign-in is case-insensitive.
EmailLike = Annotated[
    str,
    Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$"),
    BeforeValidator(lambda v: v.strip().casefold() if isinstance(v, str) else v),
]

#: Minimum password length. Length is the control that matters most here; the
#: app does not impose composition rules, which push people towards predictable
#: substitutions without adding real entropy.
MIN_PASSWORD_LENGTH = 12


class _Base(BaseModel):
    # extra="forbid" makes a misspelt field an error rather than a silently
    # dropped one, which matters when the payload carries statutory dates.
    model_config = ConfigDict(
        from_attributes=True, str_strip_whitespace=True, extra="forbid"
    )


class ValidityFields(_Base):
    """The validity envelope shared by every register."""

    issue_date: date | None = None
    valid_from: date | None = None
    valid_until: date | None = Field(
        default=None, description="Leave empty for an item with no expiry."
    )
    status: ComplianceStatus = ComplianceStatus.ACTIVE
    document_url: str | None = Field(default=None, max_length=500)
    notes: str | None = None

    @model_validator(mode="after")
    def _check_window(self) -> Self:
        if (
            self.valid_from is not None
            and self.valid_until is not None
            and self.valid_until < self.valid_from
        ):
            raise ValueError("valid_until must not be earlier than valid_from")
        return self


class ValidityUpdateFields(_Base):
    """Partial-update counterpart of :class:`ValidityFields`."""

    issue_date: date | None = None
    valid_from: date | None = None
    valid_until: date | None = None
    status: ComplianceStatus | None = None
    document_url: str | None = Field(default=None, max_length=500)
    notes: str | None = None


class ComplianceReadMixin(_Base):
    """Adds the derived renewal verdict to a read schema."""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def compliance_state(self) -> ComplianceState:
        return _evaluate(self.valid_until, self.status, valid_from=self.valid_from)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def days_remaining(self) -> int | None:
        return _days_remaining(self.valid_until)


# --------------------------------------------------------------------------- #
# Products
# --------------------------------------------------------------------------- #
class ProductBase(_Base):
    name: NonEmptyStr
    brand_name: str | None = Field(default=None, max_length=200)
    active_ingredient: NonEmptyStr
    concentration: str | None = Field(default=None, max_length=50)
    cas_number: str | None = Field(default=None, max_length=50)
    category: ProductCategory
    formulation_type: FormulationType
    notes: str | None = None


class ProductCreate(ProductBase):
    pass


class ProductUpdate(_Base):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    brand_name: str | None = Field(default=None, max_length=200)
    active_ingredient: str | None = Field(default=None, min_length=1, max_length=300)
    concentration: str | None = Field(default=None, max_length=50)
    cas_number: str | None = Field(default=None, max_length=50)
    category: ProductCategory | None = None
    formulation_type: FormulationType | None = None
    notes: str | None = None


class ProductRead(ProductBase):
    id: int


# --------------------------------------------------------------------------- #
# Registrations
# --------------------------------------------------------------------------- #
class RegistrationBase(ValidityFields):
    product_id: int
    registration_number: NonEmptyStr
    section: RegistrationSection
    purpose: RegistrationPurpose = RegistrationPurpose.MANUFACTURE
    registrant_name: NonEmptyStr
    issuing_authority: str = Field(default="CIB&RC", max_length=200)


class RegistrationCreate(RegistrationBase):
    pass


class RegistrationUpdate(ValidityUpdateFields):
    product_id: int | None = None
    registration_number: str | None = Field(default=None, min_length=1, max_length=120)
    section: RegistrationSection | None = None
    purpose: RegistrationPurpose | None = None
    registrant_name: str | None = Field(default=None, min_length=1, max_length=200)
    issuing_authority: str | None = Field(default=None, max_length=200)


class RegistrationRead(ComplianceReadMixin, RegistrationBase):
    id: int
    product_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_product(cls, data):  # noqa: ANN001 - ORM object or mapping
        return _attach_product_name(cls, data)


# --------------------------------------------------------------------------- #
# State sale permissions
# --------------------------------------------------------------------------- #
class SalePermissionBase(ValidityFields):
    product_id: int
    registration_id: int | None = None
    state: NonEmptyStr
    permission_number: NonEmptyStr
    licensing_authority: str | None = Field(default=None, max_length=200)

    @field_validator("state")
    @classmethod
    def _valid_state(cls, value: str) -> str:
        return normalise_state(value)


class SalePermissionCreate(SalePermissionBase):
    pass


class SalePermissionUpdate(ValidityUpdateFields):
    product_id: int | None = None
    registration_id: int | None = None
    state: str | None = Field(default=None, min_length=1, max_length=80)
    permission_number: str | None = Field(default=None, min_length=1, max_length=120)
    licensing_authority: str | None = Field(default=None, max_length=200)

    @field_validator("state")
    @classmethod
    def _valid_state(cls, value: str | None) -> str | None:
        return None if value is None else normalise_state(value)


class SalePermissionRead(ComplianceReadMixin, SalePermissionBase):
    id: int
    product_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_product(cls, data):  # noqa: ANN001
        return _attach_product_name(cls, data)


# --------------------------------------------------------------------------- #
# Licences
# --------------------------------------------------------------------------- #
class LicenceBase(ValidityFields):
    licence_number: NonEmptyStr
    licence_type: LicenceType
    holder_name: NonEmptyStr
    site_name: str | None = Field(default=None, max_length=200)
    site_address: str | None = None
    state: NonEmptyStr
    issuing_authority: str | None = Field(default=None, max_length=200)

    @field_validator("state")
    @classmethod
    def _valid_state(cls, value: str) -> str:
        return normalise_state(value)


class LicenceCreate(LicenceBase):
    product_ids: list[int] = Field(
        default_factory=list, description="Products covered by this licence."
    )


class LicenceUpdate(ValidityUpdateFields):
    licence_number: str | None = Field(default=None, min_length=1, max_length=120)
    licence_type: LicenceType | None = None
    holder_name: str | None = Field(default=None, min_length=1, max_length=200)
    site_name: str | None = Field(default=None, max_length=200)
    site_address: str | None = None
    state: str | None = Field(default=None, min_length=1, max_length=80)
    issuing_authority: str | None = Field(default=None, max_length=200)
    product_ids: list[int] | None = None

    @field_validator("state")
    @classmethod
    def _valid_state(cls, value: str | None) -> str | None:
        return None if value is None else normalise_state(value)


class LicenceRead(ComplianceReadMixin, LicenceBase):
    id: int
    product_ids: list[int] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _flatten_products(cls, data):  # noqa: ANN001
        products = getattr(data, "products", None)
        if products is None:
            return data
        payload = {
            field: getattr(data, field)
            for field in cls.model_fields
            if field != "product_ids" and hasattr(data, field)
        }
        payload["product_ids"] = [product.id for product in products]
        return payload


# --------------------------------------------------------------------------- #
# Label approvals
# --------------------------------------------------------------------------- #
class LabelApprovalBase(ValidityFields):
    product_id: int
    registration_id: int | None = None
    approval_number: NonEmptyStr
    label_version: str = Field(default="1.0", max_length=50)
    leaflet_version: str | None = Field(default=None, max_length=50)
    approving_authority: str = Field(default="CIB&RC", max_length=200)
    languages: str | None = Field(default=None, max_length=200)


class LabelApprovalCreate(LabelApprovalBase):
    pass


class LabelApprovalUpdate(ValidityUpdateFields):
    product_id: int | None = None
    registration_id: int | None = None
    approval_number: str | None = Field(default=None, min_length=1, max_length=120)
    label_version: str | None = Field(default=None, max_length=50)
    leaflet_version: str | None = Field(default=None, max_length=50)
    approving_authority: str | None = Field(default=None, max_length=200)
    languages: str | None = Field(default=None, max_length=200)


class LabelApprovalRead(ComplianceReadMixin, LabelApprovalBase):
    id: int
    product_name: str | None = None

    @model_validator(mode="before")
    @classmethod
    def _flatten_product(cls, data):  # noqa: ANN001
        return _attach_product_name(cls, data)


# --------------------------------------------------------------------------- #
# Dashboard and alerts
# --------------------------------------------------------------------------- #
class ComplianceItemRead(_Base):
    #: Named ``register_type`` because a field called ``register`` would shadow
    #: ``ABCMeta.register`` on the model class.
    register_type: str = Field(
        validation_alias=AliasChoices("register", "register_type")
    )
    register_label: str
    record_id: int
    title: str
    reference_number: str
    product_name: str | None
    state_name: str | None
    valid_from: date | None
    valid_until: date | None
    status: ComplianceStatus
    compliance_state: ComplianceState
    days_remaining: int | None


class RegisterSummary(_Base):
    register_type: str = Field(
        validation_alias=AliasChoices("register", "register_type")
    )
    register_label: str
    total: int
    by_state: dict[str, int]
    actionable: int


class DashboardSummary(_Base):
    generated_on: date
    critical_days: int
    warning_days: int
    totals: dict[str, int]
    registers: list[RegisterSummary]
    upcoming: list[ComplianceItemRead]


def _attach_product_name(model: type[BaseModel], data):  # noqa: ANN001
    """Copy ``product.name`` onto the payload when validating an ORM object.

    Only fields declared on ``model`` are copied across: the schemas forbid
    extra keys, and the ORM row also carries bookkeeping columns such as
    ``created_at`` that the API does not expose.
    """
    product = getattr(data, "product", None)
    if product is None:
        return data
    payload = {
        field: getattr(data, field)
        for field in model.model_fields
        if hasattr(data, field)
    }
    payload["product_name"] = product.name
    return payload


# --------------------------------------------------------------------------- #
# Accounts, sign-in and API tokens
# --------------------------------------------------------------------------- #
class LoginRequest(_Base):
    email: EmailLike
    password: NonEmptyStr


class UserBase(_Base):
    email: EmailLike
    full_name: NonEmptyStr
    role: Role = Role.VIEWER
    is_active: bool = True


class UserCreate(UserBase):
    password: Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)]


class UserUpdate(_Base):
    email: EmailLike | None = None
    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    role: Role | None = None
    is_active: bool | None = None
    password: Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)] | None = (
        None
    )


class UserRead(UserBase):
    id: int
    last_login_at: datetime | None = None
    created_at: datetime | None = None


class PasswordChange(_Base):
    current_password: NonEmptyStr
    new_password: Annotated[str, Field(min_length=MIN_PASSWORD_LENGTH, max_length=200)]


class ApiTokenCreate(_Base):
    name: NonEmptyStr
    expires_in_days: int | None = Field(
        default=None, ge=1, le=3650, description="Leave empty for a token that never expires."
    )


class ApiTokenRead(_Base):
    id: int
    name: str
    created_at: datetime | None = None
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class ApiTokenCreated(ApiTokenRead):
    """Returned once, at creation: the only time the plaintext is available."""

    token: str


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #
class AuditEventRead(_Base):
    id: int
    occurred_at: datetime
    actor_id: int | None
    actor_email: str
    actor_name: str
    action: str
    entity_type: str
    entity_id: int | None
    entity_label: str
    changes: dict


# --------------------------------------------------------------------------- #
# The certificate archive
#
# Read-only: these records are imported from source documents, so there are no
# create or update schemas to go with them.
# --------------------------------------------------------------------------- #
class ClauseRead(_Base):
    position: int
    text: str


class SectionRead(_Base):
    name: str
    text: str


class DoseRowRead(_Base):
    position: int
    label: str | None
    cells: list[str]


class CertificateSummary(_Base):
    id: int
    item_no: int
    title: str
    cir_number: str | None = None
    section: str | None = None
    category: str | None = None
    reg_type: str | None = None
    formulation: str | None = None
    shelf_life: str | None = None


class CertificateDetail(CertificateSummary):
    file_number: str | None = None
    kind: str | None = None
    source_file: str | None = None
    source_dir: str | None = None
    dose_head: str | None = None
    dose_parsed: bool = False
    crops_summary: str | None = None
    clauses: list[ClauseRead] = Field(default_factory=list)
    sections: list[SectionRead] = Field(default_factory=list)
    dose_rows: list[DoseRowRead] = Field(default_factory=list)


class EndorsementRead(_Base):
    id: int
    rc_meeting: int
    agenda_ref: str | None = None
    page: str | None = None
    en_number: str | None = None
    applicant: str | None = None
    product: str | None = None
    cir_number: str | None = None
    endorsement_type: str | None = None
    request: str | None = None
    decision: str | None = None
    remark: str | None = None
    #: "verified" or "best-effort" — how confidently the row was read from the
    #: minutes. ``caveat`` says what was uncertain when it is not clean.
    confidence: str | None = None
    caveat: str | None = None


class ImportSourceRead(_Base):
    id: int
    technical: str
    cir_number: str | None = None
    via: str | None = None
    supplier: str | None = None
    manufacturers: list[str] = Field(default_factory=list)
