from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, verify_token
from app.models import Observation, Person
from app.schemas import PersonIn, PersonOut

router = APIRouter(prefix="/persons", tags=["persons"])


@router.post("", response_model=PersonOut, status_code=201, dependencies=[Depends(verify_token)])
def create_person(body: PersonIn, db: Session = Depends(get_db)):
    if db.query(Person).filter_by(external_ref=body.external_ref).first():
        raise HTTPException(409, f"person '{body.external_ref}' already exists")
    p = Person(external_ref=body.external_ref, name=body.name, sex=body.sex,
               birth_date=body.birth_date, note=body.note)
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@router.get("", response_model=list[PersonOut])
def list_persons(db: Session = Depends(get_db)):
    return db.query(Person).all()


@router.get("/{person_id}", response_model=PersonOut)
def get_person(person_id: int, db: Session = Depends(get_db)):
    p = db.get(Person, person_id)
    if p is None:
        raise HTTPException(404, "person not found")
    return p


@router.patch("/{person_id}/phase")
def set_phase(person_id: int, body: dict, db: Session = Depends(get_db)):
    """body: {mode: auto|monitoring|followup|archive} — auto=清除手动覆盖"""
    p = db.get(Person, person_id)
    if p is None:
        raise HTTPException(404, "person not found")
    mode = body.get("mode", "auto")
    if mode not in ("auto", "monitoring", "followup", "archive"):
        raise HTTPException(422, "mode 必须是 auto/monitoring/followup/archive")
    p.phase_mode = None if mode == "auto" else mode
    db.commit()
    return {"person_id": person_id, "phase_mode": p.phase_mode, "effective": p.phase_mode}


@router.get("/{person_id}/counts")
def person_counts(person_id: int, db: Session = Depends(get_db)):
    total = db.query(Observation).filter_by(person_id=person_id).count()
    unresolved = db.query(Observation).filter(
        Observation.person_id == person_id, Observation.canonical_code.is_(None)).count()
    return {"observations": total, "unresolved": unresolved}
