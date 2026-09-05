from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.deps import get_db, verify_token
from app.ingest import IngestError, ingest_report
from app.schemas import IngestSummary, ReportIn

router = APIRouter(prefix="/ingest", tags=["ingest"])


@router.post("/reports", response_model=IngestSummary, dependencies=[Depends(verify_token)])
def post_report(body: ReportIn, db: Session = Depends(get_db)):
    try:
        return ingest_report(db, body.model_dump())
    except IngestError as e:
        raise HTTPException(e.status, str(e))
