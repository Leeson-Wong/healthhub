from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, verify_token
from app.models import Encounter, Medication, SymptomNote

router = APIRouter(tags=["records"])


# ---------- medications ----------

class MedicationIn(BaseModel):
    person_id: int
    drug_name: str
    dose: str | None = None
    unit: str | None = None
    frequency: str | None = None
    route: str | None = None
    started_at: str | None = None
    ended_at: str | None = None
    time_precision: str = "day"  # day/datetime
    status_inference: str | None = None  # billed_ongoing/billed_ended/order
    note: str | None = None


@router.post("/medications", status_code=201, dependencies=[Depends(verify_token)])
def add_medication(body: MedicationIn, db: Session = Depends(get_db)):
    m = Medication(**body.model_dump())
    db.add(m)
    db.commit()
    db.refresh(m)
    return _med_out(m)


def _med_out(m: Medication) -> dict:
    return {"id": m.id, "person_id": m.person_id, "drug_name": m.drug_name, "dose": m.dose,
            "unit": m.unit, "frequency": m.frequency, "route": m.route,
            "started_at": m.started_at, "ended_at": m.ended_at, "note": m.note,
            "time_precision": m.time_precision or "day",
            "status_inference": m.status_inference,
            "active": m.ended_at is None}


@router.get("/medications")
def list_medications(person_id: int, active: bool | None = None, db: Session = Depends(get_db)):
    q = db.query(Medication).filter(Medication.person_id == person_id)
    rows = q.order_by(Medication.ended_at.isnot(None), Medication.started_at.desc(), Medication.id.desc()).all()
    if active is True:
        rows = [m for m in rows if m.ended_at is None]
    return [_med_out(m) for m in rows]


@router.post("/medications/{med_id}/stop", dependencies=[Depends(verify_token)])
def stop_medication(med_id: int, ended_at: str | None = None, db: Session = Depends(get_db)):
    m = db.get(Medication, med_id)
    if m is None:
        raise HTTPException(404, "medication not found")
    if ended_at is None:
        from datetime import datetime, timezone
        ended_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    m.ended_at = ended_at
    db.commit()
    return _med_out(m)


# ---------- symptoms ----------

class SymptomIn(BaseModel):
    person_id: int
    occurred_at: str | None = None
    severity: int | None = None
    text: str


@router.post("/symptoms", status_code=201, dependencies=[Depends(verify_token)])
def add_symptom(body: SymptomIn, db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    occurred = body.occurred_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    s = SymptomNote(person_id=body.person_id, occurred_at=occurred,
                    severity=body.severity, text=body.text)
    db.add(s)
    db.commit()
    db.refresh(s)
    return {"id": s.id, "person_id": s.person_id, "occurred_at": s.occurred_at,
            "severity": s.severity, "text": s.text}


@router.get("/symptoms")
def list_symptoms(person_id: int, limit: int = Query(default=50, le=500),
                  db: Session = Depends(get_db)):
    rows = (db.query(SymptomNote).filter(SymptomNote.person_id == person_id)
            .order_by(SymptomNote.occurred_at.desc()).limit(limit).all())
    return [{"id": s.id, "person_id": s.person_id, "occurred_at": s.occurred_at,
             "severity": s.severity, "text": s.text} for s in rows]


# ---------- encounters ----------

class EncounterIn(BaseModel):
    person_id: int
    kind: str = "住院"
    hospital: str | None = None
    department: str | None = None
    doctor_name: str | None = None
    doctor_phone: str | None = None
    diagnosis: str | None = None
    admitted_at: str | None = None
    discharged_at: str | None = None
    note: str | None = None


def _enc_out(e: Encounter) -> dict:
    return {"id": e.id, "person_id": e.person_id, "kind": e.kind, "hospital": e.hospital,
            "department": e.department, "doctor_name": e.doctor_name, "doctor_phone": e.doctor_phone,
            "diagnosis": e.diagnosis, "admitted_at": e.admitted_at,
            "discharged_at": e.discharged_at, "note": e.note}


@router.post("/encounters", status_code=201, dependencies=[Depends(verify_token)])
def add_encounter(body: EncounterIn, db: Session = Depends(get_db)):
    e = Encounter(**body.model_dump())
    db.add(e)
    db.commit()
    db.refresh(e)
    return _enc_out(e)


@router.get("/encounters")
def list_encounters(person_id: int, db: Session = Depends(get_db)):
    rows = (db.query(Encounter).filter(Encounter.person_id == person_id)
            .order_by(Encounter.admitted_at.desc(), Encounter.id.desc()).all())
    return [_enc_out(e) for e in rows]
