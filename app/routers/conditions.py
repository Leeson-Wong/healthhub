"""诊断（Condition）API —— 病的一等公民：生命周期管理。"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, verify_token
from app.models import Condition, Person
from app.schemas import ConditionIn, ConditionOut

router = APIRouter(prefix="/conditions", tags=["conditions"])

STATUSES = {"active", "improving", "resolved", "chronic", "suspected"}


def _resolve_person(db: Session, person_id: int | None, person_ref: str | None) -> int:
    if person_id is not None:
        if db.get(Person, person_id) is None:
            raise HTTPException(404, f"person {person_id} not found")
        return person_id
    if person_ref:
        p = db.query(Person).filter_by(external_ref=person_ref).first()
        if p is None:
            raise HTTPException(404, f"person_ref '{person_ref}' not found")
        return p.id
    raise HTTPException(422, "person_id or person_ref required")


@router.post("", response_model=ConditionOut, status_code=201,
             dependencies=[Depends(verify_token)])
def create_condition(body: ConditionIn, db: Session = Depends(get_db)):
    if body.status not in STATUSES:
        raise HTTPException(422, f"status 必须是 {sorted(STATUSES)} 之一")
    pid = _resolve_person(db, body.person_id, body.person_ref)
    dup = db.query(Condition).filter_by(person_id=pid, name=body.name).first()
    if dup:
        raise HTTPException(409, f"duplicate condition ({dup.id})")
    c = Condition(person_id=pid, name=body.name, status=body.status,
                  category=body.category, onset_at=body.onset_at,
                  resolved_at=body.resolved_at, note=body.note, source=body.source)
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


@router.get("", response_model=list[ConditionOut])
def list_conditions(person_id: int, db: Session = Depends(get_db)):
    return (db.query(Condition).filter_by(person_id=person_id)
            .order_by(Condition.status.desc(), Condition.onset_at.desc()).all())


@router.patch("/{cid}", response_model=ConditionOut, dependencies=[Depends(verify_token)])
def update_condition(cid: int, body: dict, db: Session = Depends(get_db)):
    """生命周期更新：{status, resolved_at?, note?}"""
    c = db.get(Condition, cid)
    if c is None:
        raise HTTPException(404, "condition not found")
    if "status" in body:
        if body["status"] not in STATUSES:
            raise HTTPException(422, f"status 必须是 {sorted(STATUSES)} 之一")
        c.status = body["status"]
    if "resolved_at" in body:
        c.resolved_at = body["resolved_at"]
    if "note" in body:
        c.note = body["note"]
    db.commit()
    db.refresh(c)
    return c


@router.delete("/{cid}", dependencies=[Depends(verify_token)])
def delete_condition(cid: int, db: Session = Depends(get_db)):
    c = db.get(Condition, cid)
    if c is None:
        raise HTTPException(404, "condition not found")
    db.delete(c)
    db.commit()
    return {"ok": True}
