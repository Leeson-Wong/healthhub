"""Import the historical CSV (日期,时间,检验组合,代号,项目名称,结果,单位,参考范围,标记) via the normal ingest path.

Usage: python -m app.csv_import <csv> [--database PATH] [--seed-dir DIR]
      [--person-ref father --person-name 王维首 --sex M --birth 1969-01-01]
"""
from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

from app.config import Settings
from app.db import Database
from app.ingest import ingest_report
from app.models import Base, Person
from app.seeds import ensure_seeded

_MICRO_CODES = {"ORG", "MDR"}


def _sampled_at(date: str, time: str) -> str | None:
    if re.match(r"^\d{1,2}:\d{2}$", time or ""):
        hh, mm = time.split(":")
        return f"{date}T{int(hh):02d}:{int(mm):02d}:00+08:00"
    return None


def _culture_sampled_at(panel: str, date: str) -> str | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})采", panel or "")
    if m:
        return f"2026-{int(m.group(1)):02d}-{int(m.group(2)):02d}T12:00:00+08:00"
    return f"{date}T12:00:00+08:00"


def _specimen(panel: str) -> str:
    if "厌氧" in panel:
        return "厌氧血培养"
    if "引流" in panel:
        return "胆汁(引流液)"
    return re.sub(r"[（(].*?$", "", panel)


def build_payloads(csv_path: str | Path) -> list[dict]:
    rows = list(csv.DictReader(open(csv_path, encoding="utf-8-sig")))
    groups: dict[tuple, list[dict]] = {}
    for r in rows:
        key = (r["日期"].strip(), r["时间"].strip(), r["检验组合"].strip())
        groups.setdefault(key, []).append(r)

    payloads = []
    for (date, time, panel), grp in groups.items():
        if "培养" in panel:
            culture = {"specimen": _specimen(panel),
                       "sampled_at": _culture_sampled_at(panel, date),
                       "reported_at": f"{date}T12:00:00+08:00",
                       "susceptibilities": []}
            for r in grp:
                code, name, value = r["代号"].strip(), r["项目名称"].strip(), r["结果"].strip()
                if code == "ORG":
                    parts = value.split(None, 1)
                    culture["organism"] = parts[0]
                    culture["organism_comment"] = parts[1] if len(parts) > 1 else None
                elif code == "MDR":
                    culture["mdr_text"] = value
                else:
                    result = "I"
                    if "耐药" in value:
                        result = "R"
                    elif "SDD" in value:
                        result = "SDD"
                    elif "敏感" in value:
                        result = "S"
                    culture["susceptibilities"].append({
                        "drug_code": code, "drug_name": name,
                        "result": result, "result_raw": value,
                        "mic_text": r["单位"].strip() or None,
                    })
            payloads.append({"external_id": f"csv:{date}:culture:{panel}", "panel": panel,
                             "sampled_at": culture["sampled_at"],
                             "reported_at": culture["reported_at"],
                             "items": [], "cultures": [culture]})
        else:
            items = [{
                "code": r["代号"].strip() or None,
                "name": r["项目名称"].strip() or None,
                "value": r["结果"].strip(),
                "unit": r["单位"].strip() or None,
                "ref_range_text": r["参考范围"].strip() or None,
                "flag": r["标记"].strip() or None,
            } for r in grp]
            payloads.append({
                "external_id": f"csv:{date}:{time}:{panel}",
                "panel": panel,
                "sampled_at": _sampled_at(date, time) or f"{date}T12:00:00+08:00",
                "items": items, "cultures": [],
            })
    return payloads


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("csv")
    ap.add_argument("--database", default=None)
    ap.add_argument("--seed-dir", default=str(Path(__file__).resolve().parent.parent / "seed"))
    ap.add_argument("--person-ref", default="father")
    ap.add_argument("--person-name", default="王维首")
    ap.add_argument("--sex", default="M")
    ap.add_argument("--birth", default="1969-01-01")
    args = ap.parse_args()

    settings = Settings()
    if args.database:
        settings = settings.model_copy(update={"database_path": args.database, "seed_dir": args.seed_dir,
                                               "scheduler_enabled": False})
    db = Database(settings)
    Base.metadata.create_all(db.engine)
    with db.SessionLocal() as session:
        ensure_seeded(session, settings.seed_dir)
        person = session.query(Person).filter_by(external_ref=args.person_ref).first()
        if person is None:
            person = Person(external_ref=args.person_ref, name=args.person_name,
                            sex=args.sex, birth_date=args.birth)
            session.add(person)
            session.commit()
            session.refresh(person)

        total_items = unresolved = 0
        for payload in build_payloads(args.csv):
            payload["person_id"] = person.id
            summary = ingest_report(session, payload)
            total_items += summary["items_total"]
            unresolved += summary["items_unresolved"]
            mark = "NEW" if summary["created"] else "DUP"
            print(f"[{mark}] {payload['external_id']}: {summary['items_total']} items, "
                  f"{summary['items_unresolved']} unresolved, {summary['cultures']} cultures")
        print(f"DONE person={person.id} items={total_items} unresolved={unresolved}")


if __name__ == "__main__":
    main()
