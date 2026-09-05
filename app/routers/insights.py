from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.deps import get_db, verify_token
from app.ingest import resolve_person, IngestError
from app.insights import generate_insight
from app.daily_report import get_daily_report
from app.llm import LLM, LLMNotConfigured
from app.models import InsightReport, Person
from app.schemas import InsightIn, InsightOut

router = APIRouter(tags=["insights"])


def _llm_or_503(request: Request) -> LLM:
    llm = LLM(request.app.state.settings)
    if not llm.configured:
        raise HTTPException(503, "LLM not configured (set LLM_API_KEY / ANTHROPIC_AUTH_TOKEN)")
    return llm


@router.post("/insights", response_model=InsightOut, dependencies=[Depends(verify_token)])
def create_insight(body: InsightIn, request: Request, db: Session = Depends(get_db)):
    try:
        person = resolve_person(db, body.person_id, body.person_ref)
    except IngestError as e:
        raise HTTPException(e.status, str(e))
    llm = _llm_or_503(request)
    try:
        row = generate_insight(db, llm, person, kind="on_demand",
                               date_from=body.date_from, date_to=body.date_to, topic=body.topic,
                               tz_name=request.app.state.settings.app_tz)
    except LLMNotConfigured:
        raise HTTPException(503, "LLM not configured")
    return row


@router.get("/reports/daily", response_model=InsightOut)
def get_daily(date: str, request: Request, person_id: int | None = None,
              person_ref: str | None = None, db: Session = Depends(get_db)):
    try:
        person = resolve_person(db, person_id, person_ref)
    except IngestError as e:
        raise HTTPException(e.status, str(e))
    row = get_daily_report(db, request.app.state.settings, _llm_or_503(request), person, date)
    return row


@router.post("/reports/daily/generate", response_model=InsightOut, dependencies=[Depends(verify_token)])
def force_daily(date: str, request: Request, person_id: int | None = None,
                person_ref: str | None = None, db: Session = Depends(get_db)):
    try:
        person = resolve_person(db, person_id, person_ref)
    except IngestError as e:
        raise HTTPException(e.status, str(e))
    row = get_daily_report(db, request.app.state.settings, _llm_or_503(request), person, date, force=True)
    return row


@router.get("/reports", response_model=list[InsightOut])
def list_reports(person_id: int, db: Session = Depends(get_db)):
    return db.query(InsightReport).filter_by(person_id=person_id).order_by(InsightReport.id.desc()).all()
