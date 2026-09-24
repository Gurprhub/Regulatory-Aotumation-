"""Import the CIB&RC certificate archive into the database.

The data is read straight out of ``app/static/circle.html`` — the same file the
``/circle`` page is served from — so the page and the database cannot drift
apart. Re-run it after refreshing that file from the published artifact.

The import is idempotent: it matches certificates on their item number and
replaces their contents, and it clears the endorsement and source tables before
reloading them. Running it twice leaves the same rows.

Usage::

    python -m scripts.import_circle            # import, skipping if already loaded
    python -m scripts.import_circle --replace  # reload even when rows exist
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.database import SessionLocal, init_db
from app.models import (
    Certificate,
    CertificateClause,
    CertificateSection,
    Clause,
    DoseRow,
    Endorsement,
    ImportSource,
    TextBlock,
)

PAGE = Path(__file__).resolve().parent.parent / "app" / "static" / "circle.html"

#: The datasets the page declares, in the order the import needs them.
DATASETS = ("ITEMS", "RECS", "ENDORSE", "SOURCES", "CLAUSES", "TXT")

#: The source marks each endorsement 'n' or 't' for how it was read.
CONFIDENCE = {"n": "verified", "t": "best-effort"}

logger = logging.getLogger("app.import_circle")


# --------------------------------------------------------------------------- #
# Reading the page
# --------------------------------------------------------------------------- #
def _literal(script: str, name: str) -> Any:
    """Pull one top-level JSON literal out of the page's script block.

    The datasets are plain JSON, so they are sliced by matching delimiters and
    parsed rather than executed — nothing from the page is evaluated.
    """
    match = re.search(rf"^(?:const|let|var)\s+{name}\s*=\s*", script, re.M)
    if match is None:
        raise ValueError(f"{name} not found in {PAGE.name}")

    start = match.end()
    opener = script[start]
    closer = {"{": "}", "[": "]"}[opener]

    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(script)):
        char = script[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return json.loads(script[start : index + 1])
    raise ValueError(f"{name} is not terminated in {PAGE.name}")


def read_page(path: Path = PAGE) -> dict[str, Any]:
    """Return every dataset the page carries."""
    html = path.read_text(encoding="utf-8", errors="replace")
    script = re.search(r"<script[^>]*>(.*?)</script>", html, re.S)
    if script is None:
        raise ValueError(f"no script block in {path}")
    body = script.group(1)
    return {name: _literal(body, name) for name in DATASETS}


def _fingerprint(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _clean(value: Any) -> Any:
    """Normalise the source's empty strings to NULL."""
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return value


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def _intern(db: Session, model: type, texts: list[str]) -> dict[int, int]:
    """Store a deduplicated text table, returning source index -> row id."""
    existing = {row.fingerprint: row.id for row in db.scalars(select(model))}
    mapping: dict[int, int] = {}
    pending: dict[str, Any] = {}

    for index, text in enumerate(texts):
        text = text or ""
        digest = _fingerprint(text)
        if digest in existing:
            mapping[index] = existing[digest]
            continue
        if digest not in pending:
            pending[digest] = model(fingerprint=digest, text=text)
            db.add(pending[digest])
        mapping[index] = digest  # resolved to an id after the flush

    db.flush()
    for index, value in list(mapping.items()):
        if isinstance(value, str):
            mapping[index] = pending[value].id
    return mapping


def load(db: Session, data: dict[str, Any]) -> dict[str, int]:
    """Replace the archive with the page's contents. Returns row counts."""
    clause_ids = _intern(db, Clause, data["CLAUSES"])
    text_ids = _intern(db, TextBlock, data["TXT"])

    # Certificates are rebuilt wholesale: simpler to reason about than a
    # field-by-field merge, and their children cascade away with them.
    db.execute(delete(Certificate))
    db.flush()

    recs = data["RECS"]
    dose_rows = 0
    sections = 0
    for item in data["ITEMS"]:
        item_no = item["n"]
        rec = recs.get(str(item_no)) or {}

        certificate = Certificate(
            item_no=item_no,
            title=_clean(item.get("title")) or f"Certificate {item_no}",
            cir_number=_clean(item.get("cir")),
            file_number=_clean(item.get("fno")),
            section=_clean(item.get("sec")),
            category=_clean(item.get("cat")),
            kind=_clean(item.get("kindFull")),
            reg_type=_clean(item.get("regType")),
            formulation=_clean(item.get("form")),
            shelf_life=_clean(item.get("shelf")),
            source_file=_clean(item.get("file")),
            source_dir=_clean(item.get("dir")),
            dose_head=_clean(rec.get("doseHead")),
            dose_raw=_clean(rec.get("doseRaw")),
            dose_parsed=bool(rec.get("doseOk")),
            crops_summary=_clean(rec.get("crops")),
        )
        db.add(certificate)
        db.flush()

        for position, clause_index in enumerate(rec.get("cond") or []):
            clause_id = clause_ids.get(clause_index)
            if clause_id is None:
                logger.warning(
                    "certificate %s refers to clause %s, which the page does not define",
                    item_no,
                    clause_index,
                )
                continue
            db.add(
                CertificateClause(
                    certificate_id=certificate.id,
                    clause_id=clause_id,
                    position=position,
                )
            )

        for name, text_index in (rec.get("sections") or {}).items():
            text_id = text_ids.get(text_index)
            if text_id is None:
                continue
            db.add(
                CertificateSection(
                    certificate_id=certificate.id, name=name, text_block_id=text_id
                )
            )
            sections += 1

        for position, row in enumerate(rec.get("dose") or []):
            db.add(
                DoseRow(
                    certificate_id=certificate.id,
                    position=position,
                    label=_clean(row.get("c")),
                    cells=list(row.get("d") or []),
                )
            )
            dose_rows += 1

    db.execute(delete(Endorsement))
    db.flush()
    for row in data["ENDORSE"]:
        db.add(
            Endorsement(
                rc_meeting=int(row["rc"]),
                agenda_ref=_clean(row.get("rf")),
                page=_clean(str(row.get("pg", ""))),
                en_number=_clean(row.get("en")),
                applicant=_clean(row.get("ap")),
                product=_clean(row.get("pr")),
                cir_number=_clean(row.get("cir")),
                endorsement_type=_clean(row.get("ty")),
                request=_clean(row.get("rq")),
                decision=_clean(row.get("dc")),
                remark=_clean(row.get("pc")),
                confidence=CONFIDENCE.get(row.get("s")),
                caveat=_clean(row.get("u")),
            )
        )

    db.execute(delete(ImportSource))
    db.flush()
    for row in data["SOURCES"]:
        db.add(
            ImportSource(
                technical=_clean(row.get("tech")) or "(unnamed)",
                cir_number=_clean(row.get("cir")),
                via=_clean(row.get("via")),
                supplier=_clean(row.get("supplier")),
                manufacturers=list(row.get("imports") or []),
            )
        )

    return {
        "certificates": len(data["ITEMS"]),
        "clauses": len(data["CLAUSES"]),
        "text_blocks": len(data["TXT"]),
        "sections": sections,
        "dose_rows": dose_rows,
        "endorsements": len(data["ENDORSE"]),
        "import_sources": len(data["SOURCES"]),
    }


def run(replace: bool = False) -> dict[str, int] | None:
    init_db()
    with SessionLocal() as db:
        # The archive is a copy of source documents, not something users edit.
        # Recording ~12,000 creations would bury the trail of actual decisions
        # under an import, so the import is exempt.
        db.info["audit_disabled"] = True

        if not replace and db.scalar(select(Certificate).limit(1)) is not None:
            logger.info("Archive already imported; pass --replace to reload it.")
            return None

        counts = load(db, read_page())
        db.commit()
        return counts


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace", action="store_true", help="Reload even if rows already exist."
    )
    counts = run(replace=parser.parse_args().replace)
    if counts is None:
        return
    print("Imported:")
    for label, total in counts.items():
        print(f"  {total:>6,} {label.replace('_', ' ')}")


if __name__ == "__main__":
    main()
