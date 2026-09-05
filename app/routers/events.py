"""病程时间线事件 + 背景档案 API。

事件（clinical_events）与检验曲线同轴可视化：引流/培养回报/转科等钉在时间轴上。
背景档案（person_facts）：既往史/家族史/基础状态 —— 解读数据时的关键上下文。
"""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, verify_token
from app.models import ClinicalEvent, Person, PersonFact
from app.schemas import ClinicalEventIn, ClinicalEventOut, PersonFactIn, PersonFactOut

router = APIRouter(prefix="/events", tags=["events"])

KINDS = {"onset", "intervention", "medication", "culture_result", "test_result",
         "transfer", "condition_change", "surgery", "followup", "other"}
PRECISIONS = {"approx", "day", "hour", "minute"}


def _norm_utc(iso: str) -> str:
    """归一化为 UTC ISO（与其他表一致，便于字符串比较/排序）。"""
    dt = datetime.fromisoformat(iso)
    if dt.tzinfo is None:
        raise HTTPException(422, f"occurred_at 需带时区，如 2026-08-20T14:00:00+08:00")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


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


@router.post("", response_model=ClinicalEventOut, status_code=201,
             dependencies=[Depends(verify_token)])
def create_event(body: ClinicalEventIn, db: Session = Depends(get_db)):
    pid = _resolve_person(db, body.person_id, body.person_ref)
    if body.kind not in KINDS:
        raise HTTPException(422, f"kind 必须是 {sorted(KINDS)} 之一")
    if body.time_precision not in PRECISIONS:
        raise HTTPException(422, f"time_precision 必须是 {sorted(PRECISIONS)} 之一")
    occurred = _norm_utc(body.occurred_at)
    dup = db.query(ClinicalEvent).filter_by(
        person_id=pid, occurred_at=occurred, kind=body.kind, title=body.title).first()
    if dup:
        raise HTTPException(409, f"duplicate event ({dup.id}) — 同一时刻/类型/标题已存在")
    e = ClinicalEvent(person_id=pid, occurred_at=occurred,
                      time_precision=body.time_precision, kind=body.kind,
                      title=body.title, detail=body.detail, source=body.source)
    db.add(e)
    db.commit()
    db.refresh(e)
    return e


@router.get("", response_model=list[ClinicalEventOut])
def list_events(person_id: int, db: Session = Depends(get_db)):
    return (db.query(ClinicalEvent).filter_by(person_id=person_id)
            .order_by(ClinicalEvent.occurred_at).all())


@router.delete("/{event_id}", dependencies=[Depends(verify_token)])
def delete_event(event_id: int, db: Session = Depends(get_db)):
    e = db.get(ClinicalEvent, event_id)
    if e is None:
        raise HTTPException(404, "event not found")
    db.delete(e)
    db.commit()
    return {"ok": True}


# ---------- 背景档案 ----------

FACT_CATEGORIES = {"past_history", "family_history", "baseline", "care", "social", "allergy"}


@router.post("/facts", response_model=PersonFactOut, status_code=201,
             dependencies=[Depends(verify_token)])
def create_fact(body: PersonFactIn, db: Session = Depends(get_db)):
    pid = _resolve_person(db, body.person_id, body.person_ref)
    if body.category not in FACT_CATEGORIES:
        raise HTTPException(422, f"category 必须是 {sorted(FACT_CATEGORIES)} 之一")
    dup = db.query(PersonFact).filter_by(person_id=pid, category=body.category,
                                         title=body.title).first()
    if dup:
        raise HTTPException(409, f"duplicate fact ({dup.id})")
    f = PersonFact(person_id=pid, category=body.category, title=body.title,
                   detail=body.detail, sort_order=body.sort_order)
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


@router.get("/facts", response_model=list[PersonFactOut])
def list_facts(person_id: int, db: Session = Depends(get_db)):
    return (db.query(PersonFact).filter_by(person_id=person_id)
            .order_by(PersonFact.category, PersonFact.sort_order, PersonFact.id).all())
