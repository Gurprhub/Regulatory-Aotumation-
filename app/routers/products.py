"""Product register endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app import models, schemas, services
from app.database import get_session
from app.reference import FormulationType, ProductCategory

router = APIRouter(prefix="/api/products", tags=["products"])


@router.get("", response_model=list[schemas.ProductRead])
def list_products(
    session: Session = Depends(get_session),
    q: str | None = Query(default=None, description="Match name, brand or active ingredient."),
    category: ProductCategory | None = None,
    formulation_type: FormulationType | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[models.Product]:
    stmt = select(models.Product).order_by(models.Product.name)
    if q:
        pattern = f"%{q.strip()}%"
        stmt = stmt.where(
            or_(
                models.Product.name.ilike(pattern),
                models.Product.brand_name.ilike(pattern),
                models.Product.active_ingredient.ilike(pattern),
            )
        )
    if category is not None:
        stmt = stmt.where(models.Product.category == category)
    if formulation_type is not None:
        stmt = stmt.where(models.Product.formulation_type == formulation_type)
    return list(session.scalars(stmt.limit(limit).offset(offset)))


@router.get("/{product_id}", response_model=schemas.ProductRead)
def get_product(
    product_id: int, session: Session = Depends(get_session)
) -> models.Product:
    return services.get_or_404(session, models.Product, product_id, "Product")


@router.post("", response_model=schemas.ProductRead, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: schemas.ProductCreate, session: Session = Depends(get_session)
) -> models.Product:
    product = models.Product(**payload.model_dump())
    session.add(product)
    services.commit_or_conflict(
        session, context=f"Product {payload.name!r} ({payload.formulation_type.value})"
    )
    session.refresh(product)
    return product


@router.patch("/{product_id}", response_model=schemas.ProductRead)
def update_product(
    product_id: int,
    payload: schemas.ProductUpdate,
    session: Session = Depends(get_session),
) -> models.Product:
    product = services.get_or_404(session, models.Product, product_id, "Product")
    services.apply_updates(product, payload.model_dump(exclude_unset=True))
    services.commit_or_conflict(session, context=f"Product {product_id}")
    session.refresh(product)
    return product


@router.delete("/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, session: Session = Depends(get_session)) -> Response:
    """Delete a product together with its registrations, permissions and labels."""
    product = services.get_or_404(session, models.Product, product_id, "Product")
    session.delete(product)
    services.commit_or_conflict(session, context=f"Product {product_id}")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
