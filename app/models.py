"""ORM models for the compliance registers.

Four registers are tracked — registrations, state sale permissions, licences and
label approvals. They differ in their identifying fields but share the same
validity envelope (``valid_from`` / ``valid_until`` / ``status``), which is
factored into :class:`ValidityMixin` so the compliance engine and the alert feed
can treat them uniformly.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    JSON,
    Column,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, declarative_mixin, mapped_column, relationship

from app.database import Base
from app.reference import (
    ComplianceStatus,
    FormulationType,
    LicenceType,
    ProductCategory,
    RegistrationPurpose,
    RegistrationSection,
    Role,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum(python_enum: type) -> Enum:
    """Store enums by value, and validate on the way in."""
    return Enum(
        python_enum,
        values_callable=lambda e: [member.value for member in e],
        native_enum=False,
        length=64,
    )


@declarative_mixin
class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow, nullable=False
    )


@declarative_mixin
class ValidityMixin:
    """The shared validity envelope of every certificate, licence and approval."""

    @staticmethod
    def _validity_constraint(table_name: str) -> CheckConstraint:
        return CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until >= valid_from",
            name=f"ck_{table_name}_validity_window",
        )

    issue_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    #: ``None`` means the item carries no expiry (e.g. a perpetual registration).
    valid_until: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
    status: Mapped[ComplianceStatus] = mapped_column(
        _enum(ComplianceStatus), default=ComplianceStatus.ACTIVE, nullable=False
    )
    document_url: Mapped[str | None] = mapped_column(String(500), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)


#: A manufacturing or sale licence typically lists the products it covers.
licence_products = Table(
    "licence_products",
    Base.metadata,
    Column(
        "licence_id",
        Integer,
        ForeignKey("licences.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "product_id",
        Integer,
        ForeignKey("products.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Product(TimestampMixin, Base):
    """An agrochemical product — a technical grade or a formulation."""

    __tablename__ = "products"
    __table_args__ = (
        UniqueConstraint("name", "formulation_type", name="uq_products_name_formulation"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), index=True, nullable=False)
    brand_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    active_ingredient: Mapped[str] = mapped_column(String(300), nullable=False)
    concentration: Mapped[str | None] = mapped_column(String(50), nullable=True)
    cas_number: Mapped[str | None] = mapped_column(String(50), index=True, nullable=True)
    category: Mapped[ProductCategory] = mapped_column(_enum(ProductCategory), nullable=False)
    formulation_type: Mapped[FormulationType] = mapped_column(
        _enum(FormulationType), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    # ``passive_deletes`` is deliberately off on these three. The database would
    # cascade them away by itself, but then the ORM never sees the children and
    # the audit trail records only "product deleted" while several compliance
    # records vanish unrecorded. Letting SQLAlchemy delete them costs a query
    # and buys one audit event per record removed. The ON DELETE CASCADE in the
    # schema stays as a backstop for deletes that do not go through the ORM.
    registrations: Mapped[list["Registration"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    sale_permissions: Mapped[list["StateSalePermission"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    label_approvals: Mapped[list["LabelApproval"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )
    licences: Mapped[list["Licence"]] = relationship(
        secondary=licence_products, back_populates="products"
    )


class Registration(TimestampMixin, ValidityMixin, Base):
    """A CIB&RC certificate of registration held against a product."""

    __tablename__ = "registrations"
    __table_args__ = (
        UniqueConstraint("registration_number", name="uq_registrations_number"),
        ValidityMixin._validity_constraint("registrations"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False
    )
    registration_number: Mapped[str] = mapped_column(String(120), nullable=False)
    section: Mapped[RegistrationSection] = mapped_column(
        _enum(RegistrationSection), nullable=False
    )
    purpose: Mapped[RegistrationPurpose] = mapped_column(
        _enum(RegistrationPurpose), default=RegistrationPurpose.MANUFACTURE, nullable=False
    )
    registrant_name: Mapped[str] = mapped_column(String(200), nullable=False)
    issuing_authority: Mapped[str] = mapped_column(
        String(200), default="CIB&RC", nullable=False
    )

    product: Mapped[Product] = relationship(back_populates="registrations")
    # Likewise off here: deleting a registration detaches its permissions and
    # labels (ON DELETE SET NULL), which is a change each of them should carry.
    sale_permissions: Mapped[list["StateSalePermission"]] = relationship(
        back_populates="registration"
    )
    label_approvals: Mapped[list["LabelApproval"]] = relationship(
        back_populates="registration"
    )


class StateSalePermission(TimestampMixin, ValidityMixin, Base):
    """Permission to sell a registered product in a particular state."""

    __tablename__ = "state_sale_permissions"
    __table_args__ = (
        UniqueConstraint(
            "state", "permission_number", name="uq_sale_permission_state_number"
        ),
        Index("ix_sale_permissions_product_state", "product_id", "state"),
        ValidityMixin._validity_constraint("state_sale_permissions"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False
    )
    registration_id: Mapped[int | None] = mapped_column(
        ForeignKey("registrations.id", ondelete="SET NULL"), index=True, nullable=True
    )
    state: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    permission_number: Mapped[str] = mapped_column(String(120), nullable=False)
    licensing_authority: Mapped[str | None] = mapped_column(String(200), nullable=True)

    product: Mapped[Product] = relationship(back_populates="sale_permissions")
    registration: Mapped[Registration | None] = relationship(back_populates="sale_permissions")


class Licence(TimestampMixin, ValidityMixin, Base):
    """A state licence to manufacture, sell, stock or store agrochemicals."""

    __tablename__ = "licences"
    __table_args__ = (
        UniqueConstraint("state", "licence_number", name="uq_licences_state_number"),
        ValidityMixin._validity_constraint("licences"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    licence_number: Mapped[str] = mapped_column(String(120), nullable=False)
    licence_type: Mapped[LicenceType] = mapped_column(_enum(LicenceType), nullable=False)
    holder_name: Mapped[str] = mapped_column(String(200), nullable=False)
    site_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    site_address: Mapped[str | None] = mapped_column(Text, nullable=True)
    state: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    issuing_authority: Mapped[str | None] = mapped_column(String(200), nullable=True)

    products: Mapped[list[Product]] = relationship(
        secondary=licence_products, back_populates="licences"
    )


class LabelApproval(TimestampMixin, ValidityMixin, Base):
    """An approved label and leaflet for a product."""

    __tablename__ = "label_approvals"
    __table_args__ = (
        UniqueConstraint(
            "approval_number", "label_version", name="uq_label_approval_number_version"
        ),
        ValidityMixin._validity_constraint("label_approvals"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True, nullable=False
    )
    registration_id: Mapped[int | None] = mapped_column(
        ForeignKey("registrations.id", ondelete="SET NULL"), index=True, nullable=True
    )
    approval_number: Mapped[str] = mapped_column(String(120), nullable=False)
    label_version: Mapped[str] = mapped_column(String(50), default="1.0", nullable=False)
    leaflet_version: Mapped[str | None] = mapped_column(String(50), nullable=True)
    approving_authority: Mapped[str] = mapped_column(
        String(200), default="CIB&RC", nullable=False
    )
    languages: Mapped[str | None] = mapped_column(String(200), nullable=True)

    product: Mapped[Product] = relationship(back_populates="label_approvals")
    registration: Mapped[Registration | None] = relationship(back_populates="label_approvals")


# --------------------------------------------------------------------------- #
# Accounts and credentials
# --------------------------------------------------------------------------- #
class User(TimestampMixin, Base):
    """A person who may sign in.

    Deactivating an account (``is_active = False``) is preferred over deleting
    it: a compliance register should keep referring to accounts that once acted
    on it, and reactivation is a single flag.
    """

    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("email", name="uq_users_email"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    full_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(_enum(Role), default=Role.VIEWER, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    #: Consecutive failed sign-in attempts; reset on success.
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    #: Set while the account is temporarily locked after repeated failures.
    locked_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    sessions: Mapped[list["UserSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    api_tokens: Mapped[list["ApiToken"]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserSession(Base):
    """A browser session. Only the hash of the session id is stored."""

    __tablename__ = "user_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    session_hash: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="sessions")


class ApiToken(Base):
    """A long-lived bearer token for scripts and integrations.

    The plaintext is shown once at creation and never stored. ``lookup`` holds
    the token's leading characters so verification is an indexed read rather
    than a scan, and ``token_hash`` is compared in constant time.
    """

    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    lookup: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, nullable=False
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[User] = relationship(back_populates="api_tokens")


# --------------------------------------------------------------------------- #
# Audit trail
# --------------------------------------------------------------------------- #
class AuditEvent(Base):
    """One recorded change to the registers or to an account.

    The trail is append-only and self-contained: the actor's name and address
    and the subject's label are copied in at the time of the change, so an event
    still reads correctly after the account that made it, or the record it
    describes, has been deleted. Nothing here points at a row that may vanish.
    """

    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_occurred", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, index=True, nullable=False
    )

    #: Who acted. Denormalised on purpose — see the class docstring.
    actor_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_email: Mapped[str] = mapped_column(String(320), index=True, nullable=False)
    actor_name: Mapped[str] = mapped_column(String(200), nullable=False)

    action: Mapped[str] = mapped_column(String(20), index=True, nullable=False)
    entity_type: Mapped[str] = mapped_column(String(60), index=True, nullable=False)
    entity_id: Mapped[int | None] = mapped_column(Integer, index=True, nullable=True)
    entity_label: Mapped[str] = mapped_column(String(300), nullable=False)

    #: ``{field: {"from": old, "to": new}}``. Empty for a delete.
    changes: Mapped[dict] = mapped_column(JSON, default=dict, nullable=False)


# --------------------------------------------------------------------------- #
# The certificate archive
#
# Read from the CIB&RC certificates and the RC meeting minutes, and imported
# rather than entered: these tables are a faithful copy of source documents, so
# they are deliberately *not* in the audit trail's AUDITED set. Nothing here is
# edited by a user — a correction means re-running the import against a
# corrected source, and the source file is named on every certificate so a row
# can always be traced back to the page it came from.
# --------------------------------------------------------------------------- #
class Clause(Base):
    """One condition of registration, shared by every certificate that carries it.

    The certificates repeat the same standard conditions over and over; 300
    certificates draw on 125 distinct clauses, so they are stored once.
    """

    __tablename__ = "clauses"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class TextBlock(Base):
    """A block of approved label or leaflet text, shared the same way."""

    __tablename__ = "text_blocks"

    id: Mapped[int] = mapped_column(primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)


class Certificate(Base):
    """A CIB&RC certificate of registration, as printed."""

    __tablename__ = "certificates"
    __table_args__ = (
        UniqueConstraint("item_no", name="uq_certificates_item_no"),
        Index("ix_certificates_cir", "cir_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Position in the source set; the stable key the import matches on.
    item_no: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(400), index=True, nullable=False)
    cir_number: Mapped[str | None] = mapped_column(String(300), nullable=True)
    file_number: Mapped[str | None] = mapped_column(String(200), nullable=True)
    section: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    category: Mapped[str | None] = mapped_column(String(80), index=True, nullable=True)
    kind: Mapped[str | None] = mapped_column(String(120), nullable=True)
    #: domestic, export or import.
    reg_type: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    formulation: Mapped[str | None] = mapped_column(String(40), nullable=True)
    shelf_life: Mapped[str | None] = mapped_column(String(80), nullable=True)

    #: Where the row came from, so any figure can be checked against the page.
    source_file: Mapped[str | None] = mapped_column(String(400), nullable=True)
    source_dir: Mapped[str | None] = mapped_column(String(400), nullable=True)

    #: The dose table as printed: the column headings, the raw block, and
    #: whether the columns could be read confidently.
    dose_head: Mapped[str | None] = mapped_column(Text, nullable=True)
    dose_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    dose_parsed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    crops_summary: Mapped[str | None] = mapped_column(Text, nullable=True)

    clauses: Mapped[list["CertificateClause"]] = relationship(
        back_populates="certificate",
        cascade="all, delete-orphan",
        order_by="CertificateClause.position",
    )
    sections: Mapped[list["CertificateSection"]] = relationship(
        back_populates="certificate", cascade="all, delete-orphan"
    )
    dose_rows: Mapped[list["DoseRow"]] = relationship(
        back_populates="certificate",
        cascade="all, delete-orphan",
        order_by="DoseRow.position",
    )


class CertificateClause(Base):
    """A clause on a certificate, in the order it is printed."""

    __tablename__ = "certificate_clauses"
    __table_args__ = (
        UniqueConstraint(
            "certificate_id", "position", name="uq_certificate_clause_position"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    certificate_id: Mapped[int] = mapped_column(
        ForeignKey("certificates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    clause_id: Mapped[int] = mapped_column(
        ForeignKey("clauses.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    certificate: Mapped[Certificate] = relationship(back_populates="clauses")
    clause: Mapped[Clause] = relationship()


class CertificateSection(Base):
    """A named section of the approved label or leaflet.

    ``name`` is the source's own key — ``L_`` for label, ``F_`` for leaflet —
    kept as printed rather than renamed, so a section can be matched back to
    the document without a lookup table.
    """

    __tablename__ = "certificate_sections"
    __table_args__ = (
        UniqueConstraint("certificate_id", "name", name="uq_certificate_section_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    certificate_id: Mapped[int] = mapped_column(
        ForeignKey("certificates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    text_block_id: Mapped[int] = mapped_column(
        ForeignKey("text_blocks.id", ondelete="RESTRICT"), index=True, nullable=False
    )

    certificate: Mapped[Certificate] = relationship(back_populates="sections")
    text_block: Mapped[TextBlock] = relationship()


class DoseRow(Base):
    """One row of a certificate's crop, pest and dose table.

    The columns differ from certificate to certificate, so the cells are kept
    as a list against the certificate's own ``dose_head`` rather than forced
    into fixed columns the source does not have.
    """

    __tablename__ = "dose_rows"

    id: Mapped[int] = mapped_column(primary_key=True)
    certificate_id: Mapped[int] = mapped_column(
        ForeignKey("certificates.id", ondelete="CASCADE"), index=True, nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str | None] = mapped_column(Text, nullable=True)
    cells: Mapped[list] = mapped_column(JSON, default=list, nullable=False)

    certificate: Mapped[Certificate] = relationship(back_populates="dose_rows")


class Endorsement(Base):
    """One item from the minutes of a Registration Committee meeting."""

    __tablename__ = "endorsements"
    __table_args__ = (
        Index("ix_endorsements_meeting", "rc_meeting"),
        Index("ix_endorsements_en", "en_number"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    rc_meeting: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Agenda item reference within the meeting, e.g. "8.2".
    agenda_ref: Mapped[str | None] = mapped_column(String(40), nullable=True)
    page: Mapped[str | None] = mapped_column(String(20), nullable=True)
    #: Unbounded on purpose: a single item can carry a list of EN numbers
    #: ("EN-55483, EN-55485, ..."), and the longest in the current minutes is
    #: 148 characters. A fixed ceiling here is a bug waiting for the next
    #: import, and PostgreSQL enforces one where SQLite silently ignores it.
    en_number: Mapped[str | None] = mapped_column(Text, nullable=True)
    applicant: Mapped[str | None] = mapped_column(String(400), index=True, nullable=True)
    product: Mapped[str | None] = mapped_column(String(400), index=True, nullable=True)
    cir_number: Mapped[str | None] = mapped_column(String(300), nullable=True)
    endorsement_type: Mapped[str | None] = mapped_column(String(200), index=True, nullable=True)
    request: Mapped[str | None] = mapped_column(Text, nullable=True)
    decision: Mapped[str | None] = mapped_column(String(120), index=True, nullable=True)
    #: The committee's remark accompanying the decision, where there was one.
    remark: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: How confidently the row was read from the minutes: the source marks each
    #: item, and ``caveat`` says what was uncertain when it is not clean.
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)
    caveat: Mapped[str | None] = mapped_column(Text, nullable=True)


class ImportSource(Base):
    """An approved source of import, or an indigenous manufacturer."""

    __tablename__ = "import_sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    technical: Mapped[str] = mapped_column(String(400), index=True, nullable=False)
    cir_number: Mapped[str | None] = mapped_column(String(300), nullable=True)
    #: "certificate" when read off one of our own certificates, else the list.
    via: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    supplier: Mapped[str | None] = mapped_column(String(400), nullable=True)
    #: The named manufacturers; a plain list, since it is only ever read whole.
    manufacturers: Mapped[list] = mapped_column(JSON, default=list, nullable=False)



# Importing the audit module here registers its ``before_flush`` listener. It
# lives at the bottom of this file, after every model is defined, so that any
# code touching the ORM gets the trail without having to remember to ask for it.
from app import audit as _audit  # noqa: E402,F401
