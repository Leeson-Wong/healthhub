from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, or_
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import CanonicalTerm, Observation
from app.schemas import ObservationOut, TrendOut, TrendPoint

router = APIRouter(tags=["observations"])


def _term_name(db: Session) -> dict[str, str]:
    return {t.code: t.name_cn for t in db.query(CanonicalTerm).all()}


@router.get("/observations", response_model=list[ObservationOut])
def list_observations(
    person_id: int | None = None,
    code: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    flag: str | None = None,
    limit: int = Query(default=1000, le=10000),
    db: Session = Depends(get_db),
):
    q = db.query(Observation)
    if person_id:
        q = q.filter(Observation.person_id == person_id)
    if code:
        q = q.filter(Observation.canonical_code == code)
    if date_from:
        q = q.filter(Observation.effective_at >= date_from)
    if date_to:
        q = q.filter(Observation.effective_at <= date_to)
    if flag == "abnormal":
        q = q.filter(or_(Observation.flag_computed.in_(["HIGH", "LOW", "ABNORMAL_CAT"]),
                         Observation.flag_source.in_(["HIGH", "LOW"])))
    elif flag:
        conds = [Observation.flag_computed == flag, Observation.flag_source == flag.upper()]
        q = q.filter(or_(*conds))
    rows = q.order_by(Observation.effective_at, Observation.canonical_code).limit(limit).all()
    names = _term_name(db)
    out = []
    for r in rows:
        d = {c.name: getattr(r, c.name) for c in r.__table__.columns}
        d["name_cn"] = names.get(r.canonical_code)
        out.append(d)
    return out


@router.get("/cultures")
def list_cultures(person_id: int | None = None, db: Session = Depends(get_db)):
    from app.models import CultureReport, CultureSusceptibility
    q = db.query(CultureReport)
    if person_id:
        q = q.filter(CultureReport.person_id == person_id)
    out = []
    for c in q.order_by(CultureReport.reported_at).all():
        susc = db.query(CultureSusceptibility).filter_by(culture_report_id=c.id).all()
        out.append({
            "id": c.id, "person_id": c.person_id, "specimen": c.specimen,
            "sampled_at": c.sampled_at, "reported_at": c.reported_at,
            "organism": c.organism, "organism_comment": c.organism_comment,
            "mdr_text": c.mdr_text,
            "susceptibilities": [{"drug_code": s.drug_code, "drug_name": s.drug_name,
                                  "result": s.result, "result_raw": s.result_raw, "mic_text": s.mic_text}
                                 for s in susc],
        })
    return out


@router.get("/trends/{code}", response_model=TrendOut)
def trend(code: str, person_id: int | None = None,
          date_from: str | None = None, date_to: str | None = None,
          db: Session = Depends(get_db)):
    q = db.query(Observation).filter(Observation.canonical_code == code)
    if person_id:
        q = q.filter(Observation.person_id == person_id)
    if date_from:
        q = q.filter(Observation.effective_at >= date_from)
    if date_to:
        q = q.filter(Observation.effective_at <= date_to)
    rows = q.order_by(Observation.effective_at).all()
    if not rows:
        raise HTTPException(404, f"no observations for code '{code}'")
    term = db.get(CanonicalTerm, code)
    unit = next((r.unit_canonical for r in rows if r.unit_canonical), None)
    points = [TrendPoint(t=r.effective_at, value=r.value_num, text=r.value_text,
                         flag=r.flag_computed or r.flag_source) for r in rows]
    nums = [r.value_num for r in rows if r.value_num is not None]
    first, last = (nums[0], nums[-1]) if nums else (None, None)
    delta = delta_pct = None
    if first is not None and last is not None:
        delta = round(last - first, 6)
        delta_pct = round((last - first) / first * 100, 2) if first != 0 else None
    return TrendOut(code=code, name_cn=term.name_cn if term else None, unit=unit,
                    points=points, first=first, last=last, delta=delta, delta_pct=delta_pct)
