from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.deps import get_db, verify_token
from app.ingest import person_age_years
from app.models import Observation, Person, SourceReport
from app.normalize import normalize_item
from app.seeds import build_resolver_from_db

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(verify_token)])


@router.post("/reprocess")
def reprocess(request: Request, db: Session = Depends(get_db)):
    """Re-run normalization over observations whose canonical_code is NULL."""
    resolver = build_resolver_from_db(db)
    rows = db.query(Observation).filter(Observation.canonical_code.is_(None)).all()
    fixed = conflicts = 0
    for o in rows:
        report = db.get(SourceReport, o.source_report_id)
        person = db.get(Person, o.person_id)
        age = person_age_years(person.birth_date if person else None, o.effective_at)
        n = normalize_item(
            resolver,
            code=o.raw_code, name=o.raw_name, value=o.raw_value, unit=o.raw_unit,
            ref_range_text=o.raw_ref_range, flag=o.raw_flag,
            panel=report.panel if report else None,
            person_sex=person.sex if person else None, person_age=age,
        )
        if not n["canonical_code"]:
            continue
        clash = db.query(Observation).filter(
            Observation.person_id == o.person_id,
            Observation.canonical_code == n["canonical_code"],
            Observation.effective_at == o.effective_at,
            Observation.source_report_id == o.source_report_id,
            Observation.id != o.id,
        ).first()
        if clash:
            conflicts += 1  # same canonical already exists in this report; leave row unresolved
            continue
        for k, v in n.items():
            setattr(o, k, v)
        fixed += 1
    db.commit()
    return {"candidates": len(rows), "resolved": fixed,
            "conflicts": conflicts, "still_unresolved": len(rows) - fixed - conflicts}
