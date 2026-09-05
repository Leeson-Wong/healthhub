"""Idempotent report ingestion with rule-based normalization."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    CultureReport, CultureSusceptibility, Observation, Person, ReferenceRange, SourceReport, UnresolvedTerm,
)
from app.normalize import normalize_item
from app.seeds import build_resolver_from_db


class IngestError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def resolve_person(session: Session, person_id: int | None, person_ref: str | None) -> Person:
    if person_id:
        p = session.get(Person, person_id)
        if p is None:
            raise IngestError(f"person {person_id} not found", 404)
        return p
    if person_ref:
        p = session.query(Person).filter_by(external_ref=person_ref).first()
        if p is None:
            raise IngestError(f"person '{person_ref}' not found", 404)
        return p
    raise IngestError("person_id or person_ref required", 400)


def _content_hash(payload: dict) -> str:
    body = {k: v for k, v in payload.items() if k != "external_id"}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def _norm_iso(dt: str | None, fallback: str | None = None) -> str | None:
    if not dt:
        return fallback
    try:
        return datetime.fromisoformat(dt).astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return fallback


def person_age_years(birth_date: str | None, at: str | None) -> float | None:
    if not birth_date:
        return None
    try:
        born = datetime.fromisoformat(birth_date)
        ref = datetime.fromisoformat(at) if at else datetime.now(timezone.utc)
    except ValueError:
        return None
    if born.tzinfo is None:
        born = born.replace(tzinfo=timezone.utc)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=timezone.utc)
    return (ref - born).days / 365.25


def ingest_report(session: Session, payload: dict) -> dict:
    person = resolve_person(session, payload.get("person_id"), payload.get("person_ref"))
    external_id = payload["external_id"]
    sha = _content_hash(payload)

    existing = session.query(SourceReport).filter_by(person_id=person.id, external_id=external_id).first()
    if existing is not None:
        if existing.content_sha256 == sha:
            obs_count = session.query(Observation).filter_by(source_report_id=existing.id).count()
            unresolved = session.query(Observation).filter(
                Observation.source_report_id == existing.id, Observation.canonical_code.is_(None)).count()
            return {"report_id": existing.id, "created": False, "items_total": obs_count,
                    "items_normalized": obs_count - unresolved, "items_unresolved": unresolved,
                    "cultures": session.query(CultureReport).filter_by(source_report_id=existing.id).count()}
        raise IngestError(f"external_id '{external_id}' already exists with different content", 409)

    effective = _norm_iso(payload.get("sampled_at")) or _norm_iso(payload.get("reported_at")) or _utcnow_iso()
    report = SourceReport(
        person_id=person.id, external_id=external_id, content_sha256=sha,
        hospital=payload.get("hospital"), report_no=payload.get("report_no"),
        panel=payload.get("panel"),
        sampled_at=_norm_iso(payload.get("sampled_at")),
        reported_at=_norm_iso(payload.get("reported_at")),
        encounter_id=payload.get("encounter_id"),
        provenance=payload.get("provenance") or "api",
        raw_payload=json.dumps(payload, ensure_ascii=False),
    )
    session.add(report)
    session.flush()

    resolver = build_resolver_from_db(session)
    age = person_age_years(person.birth_date, effective)

    items_total = items_normalized = items_unresolved = 0
    for item in payload.get("items", []):
        items_total += 1
        n = normalize_item(
            resolver,
            code=item.get("code"), name=item.get("name"), value=item.get("value"),
            unit=item.get("unit"), ref_range_text=item.get("ref_range_text"),
            flag=item.get("flag"), panel=payload.get("panel"),
            person_sex=person.sex, person_age=age,
        )
        if n["canonical_code"] is None:
            items_unresolved += 1
            queue_unresolved(session, item, payload.get("panel"))
        else:
            items_normalized += 1
            harvest_range(session, n)
        obs = Observation(
            person_id=person.id, source_report_id=report.id,
            effective_at=effective,
            **{k: v for k, v in n.items()},
        )
        session.add(obs)

    cultures = 0
    for c in payload.get("cultures", []):
        cultures += 1
        cr = CultureReport(
            person_id=person.id, source_report_id=report.id,
            specimen=c["specimen"],
            sampled_at=_norm_iso(c.get("sampled_at"), report.sampled_at),
            reported_at=_norm_iso(c.get("reported_at"), report.reported_at),
            organism=c.get("organism"), organism_comment=c.get("organism_comment"),
            mdr_text=c.get("mdr_text"),
            raw_payload=json.dumps(c, ensure_ascii=False),
        )
        session.add(cr)
        session.flush()
        for s in c.get("susceptibilities", []):
            session.add(CultureSusceptibility(
                culture_report_id=cr.id, drug_code=s.get("drug_code"),
                drug_name=s["drug_name"], result=s.get("result", "").strip() or "I",
                result_raw=s.get("result_raw"), mic_text=s.get("mic_text"),
            ))

    session.commit()
    return {"report_id": report.id, "created": True, "items_total": items_total,
            "items_normalized": items_normalized, "items_unresolved": items_unresolved,
            "cultures": cultures}


def queue_unresolved(session: Session, item: dict, panel: str | None) -> None:
    code = (item.get("code") or "").strip() or None
    name = (item.get("name") or "").strip() or None
    row = session.query(UnresolvedTerm).filter_by(raw_code=code, raw_name=name, status="open").first()
    if row is None:
        row = UnresolvedTerm(raw_code=code, raw_name=name, panel=panel, unit=item.get("unit"),
                             sample_values=json.dumps([item.get("value")], ensure_ascii=False))
        session.add(row)
        session.flush()
    else:
        try:
            samples = json.loads(row.sample_values or "[]")
        except json.JSONDecodeError:
            samples = []
        if item.get("value") not in samples and len(samples) < 5:
            samples.append(item.get("value"))
            row.sample_values = json.dumps(samples, ensure_ascii=False)


def harvest_range(session: Session, n: dict) -> None:
    """Persist the lab's own reference range for future fallback use."""
    if n["canonical_code"] is None:
        return
    has_bounds = n["source_ref_low"] is not None or n["source_ref_high"] is not None or n["source_ref_expected"]
    if not has_bounds:
        return
    exists = session.query(ReferenceRange).filter_by(
        canonical_code=n["canonical_code"], sex=None, source="lab_report",
        low=n["source_ref_low"], high=n["source_ref_high"], expected_value=n["source_ref_expected"]).first()
    if exists is None:
        session.add(ReferenceRange(
            canonical_code=n["canonical_code"], sex=None, age_min=None, age_max=None,
            kind="categorical" if n["source_ref_expected"] else "numeric",
            low=n["source_ref_low"], high=n["source_ref_high"],
            low_inclusive=n["source_ref_low_inclusive"], high_inclusive=n["source_ref_high_inclusive"],
            expected_value=n["source_ref_expected"], source="lab_report",
        ))
