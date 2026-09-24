"""Populate the database with a realistic sample portfolio.

Dates are generated relative to today so the seeded portfolio always spans every
urgency band — valid, expiring soon, critical, expired and non-compliant.

Usage::

    python -m scripts.seed          # seed, refusing to touch a non-empty database
    python -m scripts.seed --reset  # drop everything first
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta

from sqlalchemy import select

from app.database import Base, SessionLocal, engine, init_db
from app.models import LabelApproval, Licence, Product, Registration, StateSalePermission
from app.reference import (
    ComplianceStatus,
    FormulationType,
    LicenceType,
    ProductCategory,
    RegistrationPurpose,
    RegistrationSection,
)

TODAY = date.today()


def day(offset: int) -> date:
    return TODAY + timedelta(days=offset)


def seed(reset: bool = False) -> None:
    if reset:
        Base.metadata.drop_all(bind=engine)
    init_db()

    with SessionLocal() as session:
        if session.scalar(select(Product).limit(1)) is not None:
            print(
                "Database already contains products; re-run with --reset to start over.",
                file=sys.stderr,
            )
            raise SystemExit(1)

        imidacloprid = Product(
            name="Imidacloprid 17.8% SL",
            brand_name="Scimida",
            active_ingredient="Imidacloprid",
            concentration="17.8% w/w",
            cas_number="138261-41-3",
            category=ProductCategory.INSECTICIDE,
            formulation_type=FormulationType.SL,
        )
        glyphosate = Product(
            name="Glyphosate 41% SL",
            brand_name="Scimglyph",
            active_ingredient="Glyphosate IPA salt",
            concentration="41% w/w",
            cas_number="38641-94-0",
            category=ProductCategory.HERBICIDE,
            formulation_type=FormulationType.SL,
        )
        mancozeb = Product(
            name="Mancozeb 75% WP",
            brand_name="Scimzeb",
            active_ingredient="Mancozeb",
            concentration="75% w/w",
            cas_number="8018-01-7",
            category=ProductCategory.FUNGICIDE,
            formulation_type=FormulationType.WP,
        )
        azadirachtin = Product(
            name="Azadirachtin 1500 ppm",
            brand_name="Scimneem",
            active_ingredient="Azadirachtin",
            concentration="0.15% w/w",
            category=ProductCategory.BIOPESTICIDE,
            formulation_type=FormulationType.EC,
        )
        technical = Product(
            name="Imidacloprid Technical",
            active_ingredient="Imidacloprid",
            concentration="95% min",
            cas_number="138261-41-3",
            category=ProductCategory.INSECTICIDE,
            formulation_type=FormulationType.TC,
        )
        products = [imidacloprid, glyphosate, mancozeb, azadirachtin, technical]
        session.add_all(products)
        session.flush()

        registrations = [
            Registration(
                product=technical,
                registration_number="CIR-115234/2019-Imidacloprid(TC)",
                section=RegistrationSection.SEC_9_3,
                purpose=RegistrationPurpose.MANUFACTURE,
                registrant_name="Scimplify Agro Pvt Ltd",
                issue_date=day(-1800),
                valid_from=day(-1800),
                valid_until=None,  # granted in perpetuity
            ),
            Registration(
                product=imidacloprid,
                registration_number="CIR-120981/2021-Imidacloprid(SL)",
                section=RegistrationSection.SEC_9_4,
                purpose=RegistrationPurpose.FORMULATION,
                registrant_name="Scimplify Agro Pvt Ltd",
                issue_date=day(-1200),
                valid_from=day(-1200),
                valid_until=day(420),
            ),
            Registration(
                product=glyphosate,
                registration_number="CIR-118455/2020-Glyphosate(SL)",
                section=RegistrationSection.SEC_9_4,
                purpose=RegistrationPurpose.FORMULATION,
                registrant_name="Scimplify Agro Pvt Ltd",
                issue_date=day(-1500),
                valid_from=day(-1500),
                valid_until=day(74),  # inside the warning window
            ),
            Registration(
                product=mancozeb,
                registration_number="CIR-121760/2022-Mancozeb(WP)",
                section=RegistrationSection.SEC_9_3B,
                purpose=RegistrationPurpose.FORMULATION,
                registrant_name="Scimplify Agro Pvt Ltd",
                issue_date=day(-900),
                valid_from=day(-900),
                valid_until=day(21),  # critical
                status=ComplianceStatus.UNDER_RENEWAL,
                notes="Renewal filed; acknowledgement awaited from the Secretariat.",
            ),
            Registration(
                product=azadirachtin,
                registration_number="CIR-109872/2018-Azadirachtin(EC)",
                section=RegistrationSection.SEC_9_3,
                purpose=RegistrationPurpose.FORMULATION,
                registrant_name="Scimplify Agro Pvt Ltd",
                issue_date=day(-2200),
                valid_from=day(-2200),
                valid_until=day(-45),  # lapsed
                status=ComplianceStatus.UNDER_RENEWAL,
                notes="Lapsed while the renewal application is pending.",
            ),
            Registration(
                product=glyphosate,
                registration_number="CIR-124900/2024-Glyphosate(EXP)",
                section=RegistrationSection.SEC_9_3,
                purpose=RegistrationPurpose.EXPORT,
                registrant_name="Scimplify Agro Pvt Ltd",
                issue_date=day(-60),
                valid_from=day(30),  # not yet effective
                valid_until=day(1125),
            ),
        ]
        session.add_all(registrations)
        session.flush()

        by_number = {r.registration_number: r for r in registrations}
        imida_reg = by_number["CIR-120981/2021-Imidacloprid(SL)"]
        glypho_reg = by_number["CIR-118455/2020-Glyphosate(SL)"]
        mancozeb_reg = by_number["CIR-121760/2022-Mancozeb(WP)"]

        session.add_all(
            [
                StateSalePermission(
                    product=imidacloprid,
                    registration=imida_reg,
                    state="Maharashtra",
                    permission_number="MH/SP/2024/004512",
                    licensing_authority="Commissioner of Agriculture, Maharashtra",
                    issue_date=day(-500),
                    valid_from=day(-500),
                    valid_until=day(230),
                ),
                StateSalePermission(
                    product=imidacloprid,
                    registration=imida_reg,
                    state="Punjab",
                    permission_number="PB/SP/2023/001188",
                    licensing_authority="Director of Agriculture, Punjab",
                    issue_date=day(-700),
                    valid_from=day(-700),
                    valid_until=day(18),
                ),
                StateSalePermission(
                    product=glyphosate,
                    registration=glypho_reg,
                    state="Telangana",
                    permission_number="TS/SP/2023/000904",
                    licensing_authority="Commissioner of Agriculture, Telangana",
                    issue_date=day(-820),
                    valid_from=day(-820),
                    valid_until=day(-22),
                ),
                StateSalePermission(
                    product=glyphosate,
                    registration=glypho_reg,
                    state="Kerala",
                    permission_number="KL/SP/2022/000317",
                    licensing_authority="Director of Agriculture, Kerala",
                    issue_date=day(-1000),
                    valid_from=day(-1000),
                    valid_until=day(310),
                    status=ComplianceStatus.SUSPENDED,
                    notes="Sale suspended by state order pending review of usage data.",
                ),
                StateSalePermission(
                    product=mancozeb,
                    registration=mancozeb_reg,
                    state="Karnataka",
                    permission_number="KA/SP/2024/002201",
                    licensing_authority="Commissioner of Agriculture, Karnataka",
                    issue_date=day(-400),
                    valid_from=day(-400),
                    valid_until=day(65),
                ),
                StateSalePermission(
                    product=azadirachtin,
                    state="Uttar Pradesh",
                    permission_number="UP/SP/2023/007740",
                    licensing_authority="Director of Agriculture, Uttar Pradesh",
                    issue_date=day(-600),
                    valid_from=day(-600),
                    valid_until=day(140),
                ),
            ]
        )

        manufacturing = Licence(
            licence_number="GJ/AGRI/MFG/2022/00419",
            licence_type=LicenceType.MANUFACTURE,
            holder_name="Scimplify Agro Pvt Ltd",
            site_name="Dahej Formulation Plant",
            site_address="Plot 42, GIDC Estate, Dahej, Bharuch, Gujarat 392130",
            state="Gujarat",
            issuing_authority="Directorate of Agriculture, Gujarat",
            issue_date=day(-950),
            valid_from=day(-950),
            valid_until=day(26),
            products=[imidacloprid, glyphosate, mancozeb, technical],
            notes="Covers formulation and repacking lines 1-3.",
        )
        session.add_all(
            [
                manufacturing,
                Licence(
                    licence_number="MH/AGRI/SALE/2023/01188",
                    licence_type=LicenceType.STOCK_AND_SALE,
                    holder_name="Scimplify Agro Pvt Ltd",
                    site_name="Nagpur Depot",
                    site_address="Warehouse 7, MIDC Hingna, Nagpur, Maharashtra 440016",
                    state="Maharashtra",
                    issuing_authority="Commissioner of Agriculture, Maharashtra",
                    issue_date=day(-620),
                    valid_from=day(-620),
                    valid_until=day(180),
                    products=[imidacloprid, mancozeb],
                ),
                Licence(
                    licence_number="PB/AGRI/STORE/2021/00733",
                    licence_type=LicenceType.STORAGE,
                    holder_name="Scimplify Agro Pvt Ltd",
                    site_name="Ludhiana Cold Store",
                    state="Punjab",
                    issuing_authority="Director of Agriculture, Punjab",
                    issue_date=day(-1300),
                    valid_from=day(-1300),
                    valid_until=day(-8),
                    notes="Renewal application to be filed with the district officer.",
                ),
                Licence(
                    licence_number="TS/AGRI/PRIN/2024/00092",
                    licence_type=LicenceType.PRINCIPAL_CERTIFICATE,
                    holder_name="Scimplify Agro Pvt Ltd",
                    state="Telangana",
                    issuing_authority="Commissioner of Agriculture, Telangana",
                    issue_date=day(-200),
                    valid_from=day(-200),
                    valid_until=day(520),
                    products=[glyphosate],
                ),
            ]
        )

        session.add_all(
            [
                LabelApproval(
                    product=imidacloprid,
                    registration=imida_reg,
                    approval_number="LBL/IMD-SL/2021/0431",
                    label_version="3.0",
                    leaflet_version="2.2",
                    languages="English, Hindi, Marathi",
                    issue_date=day(-380),
                    valid_from=day(-380),
                    valid_until=day(350),
                ),
                LabelApproval(
                    product=glyphosate,
                    registration=glypho_reg,
                    approval_number="LBL/GLY-SL/2020/0288",
                    label_version="2.4",
                    leaflet_version="2.0",
                    languages="English, Hindi, Telugu",
                    issue_date=day(-500),
                    valid_from=day(-500),
                    valid_until=day(52),
                ),
                LabelApproval(
                    product=mancozeb,
                    registration=mancozeb_reg,
                    approval_number="LBL/MNZ-WP/2022/0511",
                    label_version="1.3",
                    languages="English, Hindi, Kannada",
                    issue_date=day(-300),
                    valid_from=day(-300),
                    valid_until=day(-3),
                    notes="Reprint blocked until the revised label is approved.",
                ),
                LabelApproval(
                    product=azadirachtin,
                    approval_number="LBL/AZA-EC/2019/0177",
                    label_version="1.1",
                    languages="English, Hindi",
                    issue_date=day(-1400),
                    valid_from=day(-1400),
                    valid_until=day(640),
                ),
            ]
        )

        session.commit()

    with SessionLocal() as session:
        print("Seeded:")
        for label, model in (
            ("products", Product),
            ("registrations", Registration),
            ("state sale permissions", StateSalePermission),
            ("licences", Licence),
            ("label approvals", LabelApproval),
        ):
            total = len(list(session.scalars(select(model))))
            print(f"  {total:>3} {label}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset", action="store_true", help="Drop all tables before seeding."
    )
    seed(reset=parser.parse_args().reset)


if __name__ == "__main__":
    main()
