"""The certificate archive: certificates, endorsements and sources of import.

Read-only. These records are imported from the CIB&RC certificates and the RC
meeting minutes by ``scripts.import_circle``; a correction means fixing the
source and re-importing, not editing a row here, so there is nothing to POST to.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from app import auth, schemas, services
from app.database import get_session
from app.models import (
    Certificate,
    CertificateClause,
    CertificateSection,
    Endorsement,
    ImportSource,
)

router = APIRouter(
    prefix="/api/archive",
    tags=["certificate archive"],
    dependencies=[Depends(auth.require_viewer)],
)


@router.get("/certificates", response_model=list[schemas.CertificateSummary])
def list_certificates(
    db: Session = Depends(get_session),
    q: str | None = Query(default=None, description="Match title or CIR number."),
    category: str | None = None,
    reg_type: str | None = Query(default=None, description="domestic, export or import."),
    section: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Certificate]:
    stmt = select(Certificate).order_by(Certificate.title)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(Certificate.title.ilike(pattern), Certificate.cir_number.ilike(pattern))
        )
    if category:
        stmt = stmt.where(Certificate.category == category)
    if reg_type:
        stmt = stmt.where(Certificate.reg_type == reg_type)
    if section:
        stmt = stmt.where(Certificate.section == section)
    return list(db.scalars(stmt.limit(limit).offset(offset)))


@router.get("/certificates/{certificate_id}", response_model=schemas.CertificateDetail)
def get_certificate(
    certificate_id: int, db: Session = Depends(get_session)
) -> dict:
    """One certificate with its conditions, label and leaflet text and dose table."""
    certificate = db.scalar(
        select(Certificate)
        .where(Certificate.id == certificate_id)
        .options(
            selectinload(Certificate.clauses).selectinload(CertificateClause.clause),
            selectinload(Certificate.sections).selectinload(
                CertificateSection.text_block
            ),
            selectinload(Certificate.dose_rows),
        )
    )
    if certificate is None:
        services.get_or_404(db, Certificate, certificate_id, "Certificate")

    return {
        **{
            field: getattr(certificate, field)
            for field in schemas.CertificateSummary.model_fields
        },
        "file_number": certificate.file_number,
        "kind": certificate.kind,
        "source_file": certificate.source_file,
        "source_dir": certificate.source_dir,
        "dose_head": certificate.dose_head,
        "dose_parsed": certificate.dose_parsed,
        "crops_summary": certificate.crops_summary,
        "clauses": [
            {"position": link.position, "text": link.clause.text}
            for link in certificate.clauses
        ],
        "sections": [
            {"name": section.name, "text": section.text_block.text}
            for section in certificate.sections
        ],
        "dose_rows": [
            {"position": row.position, "label": row.label, "cells": row.cells}
            for row in certificate.dose_rows
        ],
    }


@router.get("/endorsements", response_model=list[schemas.EndorsementRead])
def list_endorsements(
    db: Session = Depends(get_session),
    q: str | None = Query(default=None, description="Match applicant, product or EN number."),
    rc_meeting: int | None = None,
    decision: str | None = None,
    endorsement_type: str | None = None,
    confidence: str | None = Query(
        default=None, description="verified or best-effort."
    ),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[Endorsement]:
    stmt = select(Endorsement).order_by(
        Endorsement.rc_meeting.desc(), Endorsement.id
    )
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                Endorsement.applicant.ilike(pattern),
                Endorsement.product.ilike(pattern),
                Endorsement.en_number.ilike(pattern),
            )
        )
    if rc_meeting is not None:
        stmt = stmt.where(Endorsement.rc_meeting == rc_meeting)
    if decision:
        stmt = stmt.where(Endorsement.decision == decision)
    if endorsement_type:
        stmt = stmt.where(Endorsement.endorsement_type == endorsement_type)
    if confidence:
        stmt = stmt.where(Endorsement.confidence == confidence)
    return list(db.scalars(stmt.limit(limit).offset(offset)))


@router.get("/sources", response_model=list[schemas.ImportSourceRead])
def list_sources(
    db: Session = Depends(get_session),
    q: str | None = Query(default=None, description="Match technical or supplier."),
    via: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ImportSource]:
    stmt = select(ImportSource).order_by(ImportSource.technical)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                ImportSource.technical.ilike(pattern),
                ImportSource.supplier.ilike(pattern),
            )
        )
    if via:
        stmt = stmt.where(ImportSource.via == via)
    return list(db.scalars(stmt.limit(limit).offset(offset)))


@router.get("/summary")
def summary(db: Session = Depends(get_session)) -> dict[str, object]:
    """Headline counts, and what the archive holds — empty until it is imported."""

    def count(model) -> int:  # noqa: ANN001
        return db.scalar(select(func.count()).select_from(model)) or 0

    by_category = dict(
        db.execute(
            select(Certificate.category, func.count())
            .group_by(Certificate.category)
            .order_by(func.count().desc())
        ).all()
    )
    by_decision = dict(
        db.execute(
            select(Endorsement.decision, func.count())
            .group_by(Endorsement.decision)
            .order_by(func.count().desc())
        ).all()
    )

    return {
        "certificates": count(Certificate),
        "endorsements": count(Endorsement),
        "import_sources": count(ImportSource),
        "rc_meetings": db.scalar(
            select(func.count(func.distinct(Endorsement.rc_meeting)))
        )
        or 0,
        "certificates_by_category": {k or "(unstated)": v for k, v in by_category.items()},
        "endorsements_by_decision": {k or "(none recorded)": v for k, v in by_decision.items()},
    }
