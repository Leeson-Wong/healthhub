"""数据导出：FHIR R4 Bundle（给机器）+ CSV zip（给人/Excel）。

FHIR 资源子集：Patient / Condition / Encounter / Observation / MedicationStatement /
DiagnosticReport / DocumentReference（原件）。LOINC 优先用 canonical_terms，缺失时留空（code 只给本地码）。
"""

import csv
import io
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import (CanonicalTerm, Condition, Encounter, Medication, Observation, Person,
                        SourceFile, SourceReport)

router = APIRouter(prefix="/export", tags=["export"])

# 核心指标 LOINC 补充映射（term 表 loinc 字段为空时的兜底）
LOINC = {
    "WBC": "26464-8", "RBC": "789-8", "HGB": "718-7", "HCT": "4544-3", "PLT": "777-3",
    "CREA": "2160-0", "BUN": "6299-2", "UA": "3084-1", "GLU": "2345-7",
    "NA": "2947-0", "K": "2823-3", "CL": "2075-0", "CA": "17861-6",
    "TBIL": "1975-2", "DBIL": "1969-5", "ALT": "1742-6", "AST": "1920-8", "ALB": "1751-7",
    "CRP": "1988-5", "PCT_PROCAL": "75269-7", "TNI": "89579-7", "NT_PROBNP": "33762-6",
    "DDIMER": "48065-7", "FIB": "3255-7", "PT": "5902-2", "APTT": "14979-9",
    "TEMP": "8310-5", "HR": "8867-4", "SPO2": "59408-5", "BP_SYS": "8480-6", "BP_DIA": "8462-4",
    "WEIGHT": "29463-7", "LACT": "2524-7", "GGT": "2324-2", "SAA": "30522-7",
}

GENDER = {"M": "male", "F": "female"}


def _fhir_ts(iso: str | None):
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso).astimezone(timezone.utc).isoformat()
    except ValueError:
        return iso


@router.get("/fhir/{person_id}")
def export_fhir(person_id: int, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if person is None:
        return JSONResponse({"error": "person not found"}, status_code=404)
    terms = {t.code: t for t in db.query(CanonicalTerm).all()}
    base = f"urn:healthhub:{person_id}"
    entry = []

    def add(res):
        entry.append({"fullUrl": f"{base}:{res['resourceType']}/{res['id']}",
                      "resource": res})

    add({"resourceType": "Patient", "id": "p1", "name": [{"text": person.name}],
         "gender": GENDER.get(person.sex or "", "unknown"),
         "birthDate": person.birth_date or None})

    for c in db.query(Condition).filter_by(person_id=person_id).all():
        add({"resourceType": "Condition", "id": f"cond{c.id}", "subject": {"reference": f"{base}:Patient/p1"},
             "code": {"text": c.name}, "clinicalStatus": {"text": c.status},
             "onsetDateTime": _fhir_ts(c.onset_at), "abatementDateTime": _fhir_ts(c.resolved_at),
             "note": [{"text": c.note}] if c.note else None})

    for e in db.query(Encounter).filter_by(person_id=person_id).all():
        add({"resourceType": "Encounter", "id": f"enc{e.id}", "subject": {"reference": f"{base}:Patient/p1"},
             "status": "finished" if e.discharged_at else "in-progress",
             "class": {"code": e.kind or "住院"},
             "period": {"start": _fhir_ts(e.admitted_at), "end": _fhir_ts(e.discharged_at)},
             "serviceProvider": {"display": e.hospital} if e.hospital else None})

    for o in (db.query(Observation).filter_by(person_id=person_id)
              .order_by(Observation.effective_at).all()):
        t = terms.get(o.canonical_code)
        loinc = (t.loinc if t else None) or LOINC.get(o.canonical_code or "")
        coding = [{"system": "http://loinc.org", "code": loinc}] if loinc else []
        ob = {"resourceType": "Observation", "id": f"obs{o.id}",
              "subject": {"reference": f"{base}:Patient/p1"},
              "code": {"coding": coding, "text": o.raw_name or o.canonical_code},
              "effectiveDateTime": _fhir_ts(o.effective_at)}
        if o.value_num is not None:
            ob["valueQuantity"] = {"value": o.value_num, "unit": o.unit_canonical or None}
        elif o.value_text:
            ob["valueString"] = o.value_text
        if o.raw_ref_range:
            hi_lo = o.raw_ref_range.replace("，", "-")
            ob["referenceRange"] = [{"text": o.raw_ref_range}]
        add(ob)

    for m in db.query(Medication).filter_by(person_id=person_id).all():
        add({"resourceType": "MedicationStatement", "id": f"med{m.id}",
             "subject": {"reference": f"{base}:Patient/p1"},
             "status": "active" if not m.ended_at else "completed",
             "medicationCodeableConcept": {"text": m.drug_name},
             "effectivePeriod": {"start": _fhir_ts(m.started_at), "end": _fhir_ts(m.ended_at)},
             "note": [{"text": (m.note or "") + (f" | 状态推断:{m.status_inference}" if m.status_inference else "")}]})

    for r in db.query(SourceReport).filter_by(person_id=person_id).all():
        add({"resourceType": "DiagnosticReport", "id": f"rpt{r.id}",
             "subject": {"reference": f"{base}:Patient/p1"}, "status": "final",
             "code": {"text": r.panel or r.external_id},
             "effectiveDateTime": _fhir_ts(r.sampled_at)})
    for f in db.query(SourceFile).all():
        rep = db.get(SourceReport, f.source_report_id)
        if rep is None or rep.person_id != person_id:
            continue
        add({"resourceType": "DocumentReference", "id": f"doc{f.id}",
             "subject": {"reference": f"{base}:Patient/p1"}, "status": "current",
             "content": [{"attachment": {"contentType": f.mime, "url": f"/files/{f.id}",
                                         "title": f.filename}}]})

    bundle = {"resourceType": "Bundle", "type": "collection",
              "timestamp": datetime.now(timezone.utc).isoformat(),
              "entry": entry}
    fname = f"healthhub-{person_id}-fhir.json"
    return StreamingResponse(
        iter([__import__("json").dumps(bundle, ensure_ascii=False, indent=1)]),
        media_type="application/fhir+json",
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})


@router.get("/csv/{person_id}")
def export_csv(person_id: int, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if person is None:
        return JSONResponse({"error": "person not found"}, status_code=404)

    def rows_of(model, order):
        return (db.query(model).filter_by(person_id=person_id).order_by(order).all())

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        def add_csv(name, header, lines):
            s = io.StringIO()
            w = csv.writer(s)
            w.writerow(header)
            w.writerows(lines)
            z.writestr(name, s.getvalue())

        add_csv("observations.csv",
                ["effective_at", "code", "name", "value", "unit", "ref_range", "flag", "report_id"],
                [[o.effective_at, o.canonical_code, o.raw_name, o.value_num or o.value_text,
                  o.unit_canonical or o.raw_unit, o.raw_ref_range, o.flag_computed, o.source_report_id]
                 for o in rows_of(Observation, Observation.effective_at)])
        add_csv("medications.csv", ["drug", "start", "end", "route", "status_inference", "note"],
                [[m.drug_name, m.started_at, m.ended_at, m.route, m.status_inference, m.note]
                 for m in rows_of(Medication, Medication.started_at)])
        add_csv("conditions.csv", ["name", "status", "category", "onset", "resolved", "note"],
                [[c.name, c.status, c.category, c.onset_at, c.resolved_at, c.note]
                 for c in rows_of(Condition, Condition.onset_at)])
        add_csv("reports.csv", ["id", "panel", "sampled_at", "provenance", "hospital"],
                [[r.id, r.panel, r.sampled_at, r.provenance, r.hospital]
                 for r in rows_of(SourceReport, SourceReport.sampled_at)])
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="healthhub-{person_id}.zip"'})
