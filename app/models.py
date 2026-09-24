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
    CheckConstraint,
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

    registrations: Mapped[list["Registration"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )
    sale_permissions: Mapped[list["StateSalePermission"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
    )
    label_approvals: Mapped[list["LabelApproval"]] = relationship(
        back_populates="product", cascade="all, delete-orphan", passive_deletes=True
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
    sale_permissions: Mapped[list["StateSalePermission"]] = relationship(
        back_populates="registration", passive_deletes=True
    )
    label_approvals: Mapped[list["LabelApproval"]] = relationship(
        back_populates="registration", passive_deletes=True
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
