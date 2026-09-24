"""Certificate archive tests.

The importer reads the same file the /circle page is served from, so these
tests run it against a small slice of the real page rather than a fixture
invented here: if the page's shape changes, they fail.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import sessionmaker

from app.models import (
    AuditEvent,
    Certificate,
    CertificateClause,
    Clause,
    DoseRow,
    Endorsement,
    ImportSource,
    TextBlock,
)
from scripts.import_circle import load, read_page

#: Read once — the page is 7.6 MB and parsing it is the slow part.
PAGE_DATA = read_page()


def _slice(data: dict, certificates: int = 12, endorsements: int = 40) -> dict:
    """A small but structurally complete slice of the real dataset."""
    items = data["ITEMS"][:certificates]
    return {
        "ITEMS": items,
        "RECS": {str(i["n"]): data["RECS"][str(i["n"])] for i in items if str(i["n"]) in data["RECS"]},
        "ENDORSE": data["ENDORSE"][:endorsements],
        "SOURCES": data["SOURCES"][:10],
        "CLAUSES": data["CLAUSES"],
        "TXT": data["TXT"],
    }


@pytest.fixture
def imported(db_engine):
    """A database with the slice imported, as the importer would leave it."""
    factory = sessionmaker(bind=db_engine)
    with factory() as db:
        db.info["audit_disabled"] = True
        counts = load(db, _slice(PAGE_DATA))
        db.commit()
    return counts


class TestPageParsing:
    def test_every_dataset_is_found(self) -> None:
        assert set(PAGE_DATA) == {"ITEMS", "RECS", "ENDORSE", "SOURCES", "CLAUSES", "TXT"}
        assert len(PAGE_DATA["ITEMS"]) == 300
        assert len(PAGE_DATA["ENDORSE"]) == 7536
        assert len(PAGE_DATA["CLAUSES"]) == 125

    def test_records_carry_the_fields_the_import_relies_on(self) -> None:
        item = PAGE_DATA["ITEMS"][0]
        assert {"n", "title", "cir", "sec", "cat", "regType"} <= set(item)
        endorsement = PAGE_DATA["ENDORSE"][0]
        assert {"rc", "ap", "pr", "ty", "dc", "s"} <= set(endorsement)


class TestImport:
    def test_counts(self, imported: dict) -> None:
        assert imported["certificates"] == 12
        assert imported["endorsements"] == 40
        assert imported["dose_rows"] > 0
        assert imported["sections"] > 0

    def test_clause_text_is_stored_once_and_shared(self, imported, session) -> None:
        """300 certificates draw on 125 clauses; the text is not repeated."""
        links = session.scalar(select(func.count()).select_from(CertificateClause))
        distinct = session.scalar(select(func.count()).select_from(Clause))
        assert links > distinct, (links, distinct)

    def test_certificate_keeps_its_source_file(self, imported, session) -> None:
        """Every figure has to be traceable to the page it came from."""
        certificate = session.scalars(select(Certificate)).first()
        assert certificate.source_file
        assert certificate.source_dir

    def test_dose_cells_are_kept_as_printed(self, imported, session) -> None:
        # Filtered in Python on purpose: PostgreSQL's json type has no equality
        # operator, so comparing the column in SQL would pass on SQLite and
        # fail on the database production actually runs.
        row = next(
            row for row in session.scalars(select(DoseRow)) if row.cells
        )
        assert isinstance(row.cells, list)
        assert all(isinstance(cell, str) for cell in row.cells)

    def test_endorsement_confidence_is_decoded(self, imported, session) -> None:
        values = {
            value
            for (value,) in session.execute(select(Endorsement.confidence).distinct())
        }
        assert values <= {"verified", "best-effort", None}
        assert values & {"verified", "best-effort"}

    def test_empty_strings_become_null(self, imported, session) -> None:
        """The source uses "" for absent; SQL should say NULL."""
        blanks = session.scalar(
            select(func.count()).select_from(Endorsement).where(Endorsement.en_number == "")
        )
        assert blanks == 0

    def test_import_is_idempotent(self, db_engine, imported: dict) -> None:
        factory = sessionmaker(bind=db_engine)
        with factory() as db:
            db.info["audit_disabled"] = True
            again = load(db, _slice(PAGE_DATA))
            db.commit()
        assert again == imported

        with factory() as db:
            assert db.scalar(select(func.count()).select_from(Certificate)) == 12
            assert db.scalar(select(func.count()).select_from(Endorsement)) == 40

    def test_import_does_not_flood_the_audit_trail(self, db_engine) -> None:
        """An import is a copy of source documents, not a user's decisions."""
        factory = sessionmaker(bind=db_engine)
        with factory() as db:
            db.info["audit_disabled"] = True
            load(db, _slice(PAGE_DATA))
            db.commit()
        with factory() as db:
            assert db.scalar(select(func.count()).select_from(AuditEvent)) == 0

    def test_text_blocks_are_deduplicated(self, imported, session) -> None:
        fingerprints = session.scalar(
            select(func.count(func.distinct(TextBlock.fingerprint)))
        )
        total = session.scalar(select(func.count()).select_from(TextBlock))
        assert fingerprints == total


class TestArchiveApi:
    def test_the_archive_is_not_public(self, raw_client: TestClient) -> None:
        for path in (
            "/api/archive/certificates",
            "/api/archive/endorsements",
            "/api/archive/sources",
            "/api/archive/summary",
        ):
            assert raw_client.get(path).status_code == 401, path

    def test_viewer_may_read_it(self, viewer_client: TestClient, imported) -> None:
        response = viewer_client.get("/api/archive/certificates")
        assert response.status_code == 200
        assert len(response.json()) == 12

    def test_certificate_detail_assembles_the_document(
        self, viewer_client: TestClient, imported, session
    ) -> None:
        certificate = session.scalars(
            select(Certificate).order_by(Certificate.item_no)
        ).first()
        body = viewer_client.get(f"/api/archive/certificates/{certificate.id}").json()

        assert body["title"] == certificate.title
        assert body["source_file"]
        # Clauses come back in the order the certificate prints them.
        positions = [clause["position"] for clause in body["clauses"]]
        assert positions == sorted(positions)
        if body["clauses"]:
            assert body["clauses"][0]["text"]
        if body["sections"]:
            assert body["sections"][0]["name"]
            assert body["sections"][0]["text"]

    def test_missing_certificate_is_404(self, viewer_client: TestClient, imported) -> None:
        assert viewer_client.get("/api/archive/certificates/999999").status_code == 404

    def test_certificate_search(self, viewer_client: TestClient, imported, session) -> None:
        certificate = session.scalars(select(Certificate)).first()
        word = certificate.title.split()[0]
        found = viewer_client.get("/api/archive/certificates", params={"q": word}).json()
        assert any(item["id"] == certificate.id for item in found)

    def test_endorsement_filters(self, viewer_client: TestClient, imported, session) -> None:
        meeting = session.scalars(select(Endorsement.rc_meeting)).first()
        rows = viewer_client.get(
            "/api/archive/endorsements", params={"rc_meeting": meeting}
        ).json()
        assert rows
        assert {row["rc_meeting"] for row in rows} == {meeting}

    def test_sources_carry_their_manufacturers(
        self, viewer_client: TestClient, imported
    ) -> None:
        rows = viewer_client.get("/api/archive/sources").json()
        assert rows
        assert any(isinstance(row["manufacturers"], list) for row in rows)

    def test_summary(self, viewer_client: TestClient, imported) -> None:
        body = viewer_client.get("/api/archive/summary").json()
        assert body["certificates"] == 12
        assert body["endorsements"] == 40
        assert body["rc_meetings"] >= 1
        assert body["certificates_by_category"]

    def test_summary_is_empty_before_an_import(self, viewer_client: TestClient) -> None:
        body = viewer_client.get("/api/archive/summary").json()
        assert body["certificates"] == 0
        assert body["endorsements"] == 0

    def test_the_archive_is_read_only(self) -> None:
        """Corrections belong in the source document and a re-import."""
        from app.main import app

        for route in app.routes:
            if getattr(route, "path", "").startswith("/api/archive"):
                assert set(getattr(route, "methods", set())) <= {"GET", "HEAD", "OPTIONS"}


class TestColumnWidths:
    """Every value in the *whole* dataset must fit the column that receives it.

    This exists because it did not: the importer ran green on SQLite and on a
    40-row test slice, then failed on PostgreSQL against the full 7,536
    endorsements, where one field was three times its declared width. SQLite
    ignores VARCHAR limits; PostgreSQL enforces them. Checking the source
    lengths costs nothing and catches the whole class before a deploy does.
    """

    @staticmethod
    def _limits(model) -> dict[str, int]:
        from sqlalchemy import String

        return {
            column.name: column.type.length
            for column in model.__table__.columns
            if isinstance(column.type, String) and column.type.length is not None
        }

    @pytest.mark.parametrize(
        ("dataset", "model_name", "fields"),
        [
            (
                "ENDORSE",
                "Endorsement",
                {
                    "rf": "agenda_ref",
                    "pg": "page",
                    "en": "en_number",
                    "ap": "applicant",
                    "pr": "product",
                    "cir": "cir_number",
                    "ty": "endorsement_type",
                    "dc": "decision",
                },
            ),
            (
                "ITEMS",
                "Certificate",
                {
                    "title": "title",
                    "cir": "cir_number",
                    "fno": "file_number",
                    "sec": "section",
                    "cat": "category",
                    "kindFull": "kind",
                    "regType": "reg_type",
                    "form": "formulation",
                    "shelf": "shelf_life",
                    "file": "source_file",
                    "dir": "source_dir",
                },
            ),
            (
                "SOURCES",
                "ImportSource",
                {
                    "tech": "technical",
                    "cir": "cir_number",
                    "via": "via",
                    "supplier": "supplier",
                },
            ),
        ],
    )
    def test_source_values_fit(self, dataset: str, model_name: str, fields: dict) -> None:
        import app.models as models_module

        limits = self._limits(getattr(models_module, model_name))
        too_long: list[str] = []

        for row in PAGE_DATA[dataset]:
            for source_key, column in fields.items():
                limit = limits.get(column)
                if limit is None:  # unbounded column, nothing to outgrow
                    continue
                value = row.get(source_key)
                if value is None:
                    continue
                length = len(str(value).strip())
                if length > limit:
                    too_long.append(f"{model_name}.{column}: {length} > {limit}")

        assert not too_long, "values exceed their column: " + "; ".join(sorted(set(too_long))[:5])

    def test_section_names_fit(self) -> None:
        from app.models import CertificateSection

        limit = self._limits(CertificateSection)["name"]
        names = {
            name
            for record in PAGE_DATA["RECS"].values()
            for name in (record.get("sections") or {})
        }
        assert names and max(len(name) for name in names) <= limit
