from fastapi import Header, HTTPException, Request

from app.db import Database


def get_db(request: Request):
    db: Database = request.app.state.db
    with db.SessionLocal() as session:
        yield session


def verify_token(request: Request, authorization: str = Header(default="")):
    token = request.app.state.settings.ingest_token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")
