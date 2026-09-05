"""Load seed JSON files into DB and build runtime TermResolver."""
from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from app.models import CanonicalTerm, ReferenceRange, TermAlias, UnitConversion
from app.normalize import AliasRow, TermResolver, norm_code, norm_name, norm_unit


def _read(seed_dir: str | Path, name: str) -> list[dict]:
    p = Path(seed_dir) / f"{name}.json"
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def _auto_aliases(term: dict) -> list[dict]:
    out = []
    if norm_code(term["code"]):
        out.append({"code": norm_code(term["code"]), "name": None, "panel_hint": None, "unit_hint": None, "canonical": term["code"]})
    if norm_name(term.get("name_cn")):
        out.append({"code": None, "name": norm_name(term["name_cn"]), "panel_hint": None, "unit_hint": None, "canonical": term["code"]})
    return out


def _explicit_aliases(seed_dir) -> list[dict]:
    out = []
    for row in _read(seed_dir, "term_aliases"):
        out.append({
            "code": norm_code(row.get("code")),
            "name": norm_name(row.get("name")),
            "panel_hint": row.get("panel_hint"),
            "unit_hint": norm_unit(row.get("unit_hint")) or row.get("unit_hint"),
            "canonical": row["canonical"],
        })
    return out


def build_resolver_from_seed(seed_dir: str | Path) -> TermResolver:
    terms = _read(seed_dir, "canonical_terms")
    aliases: list[AliasRow] = []
    seen: set[tuple] = set()

    def add(code, name, panel_hint, unit_hint, canonical):
        key = (code, name, panel_hint, unit_hint)
        if (code is None and name is None) or key in seen:
            return
        seen.add(key)
        aliases.append(AliasRow(code=code, name=name, panel_hint=panel_hint, unit_hint=unit_hint, canonical=canonical))

    for row in _explicit_aliases(seed_dir):
        add(row["code"], row["name"], row["panel_hint"], row["unit_hint"], row["canonical"])
    for t in terms:  # auto self-aliases; explicit rows win because they were inserted first
        for a in _auto_aliases(t):
            add(a["code"], a["name"], None, None, t["code"])

    conversions = {}
    for row in _read(seed_dir, "unit_conversions"):
        cc = row.get("canonical")
        conversions[(norm_code(cc) if cc else None, norm_unit(row["from_unit"]))] = (row["factor"], row["to_unit"])

    range_rows = []
    for row in _read(seed_dir, "reference_ranges"):
        range_rows.append({
            "canonical": row["canonical"], "sex": row.get("sex"),
            "age_min": row.get("age_min"), "age_max": row.get("age_max"),
            "kind": row.get("kind", "numeric"), "low": row.get("low"),
            "high": row.get("high"), "expected": row.get("expected"),
        })

    resolver = TermResolver(
        aliases=aliases,
        conversions=conversions,
        terms={t["code"]: t for t in terms},
    )
    resolver.range_rows = range_rows
    return resolver


def ensure_seeded(session: Session, seed_dir: str | Path) -> None:
    """Idempotent: insert canonical terms, aliases (explicit + auto), conversions, curated ranges."""
    terms = _read(seed_dir, "canonical_terms")
    for t in terms:
        if session.get(CanonicalTerm, t["code"]) is None:
            session.add(CanonicalTerm(
                code=t["code"], name_cn=t["name_cn"], name_en=t.get("name_en"),
                category=t["category"], value_type=t.get("value_type", "numeric"),
                default_unit=t.get("default_unit"), loinc=t.get("loinc"),
            ))

    def alias_exists(code, name, panel, unit) -> bool:
        return session.query(TermAlias).filter_by(
            alias_code=code, alias_name=name, panel_hint=panel, unit_hint=unit).first() is not None

    for row in _explicit_aliases(seed_dir):
        if not alias_exists(row["code"], row["name"], row["panel_hint"], row["unit_hint"]):
            session.add(TermAlias(alias_code=row["code"], alias_name=row["name"],
                                  panel_hint=row["panel_hint"], unit_hint=row["unit_hint"],
                                  canonical_code=row["canonical"]))
    for t in terms:
        for a in _auto_aliases(t):
            if not alias_exists(a["code"], a["name"], None, None):
                session.add(TermAlias(alias_code=a["code"], alias_name=a["name"],
                                      panel_hint=None, unit_hint=None, canonical_code=t["code"]))

    for row in _read(seed_dir, "unit_conversions"):
        cc = norm_code(row.get("canonical")) if row.get("canonical") else None
        exists = session.query(UnitConversion).filter_by(canonical_code=cc, from_unit=row["from_unit"]).first()
        if not exists:
            session.add(UnitConversion(canonical_code=cc, from_unit=row["from_unit"],
                                       to_unit=row["to_unit"], factor=row["factor"]))

    for row in _read(seed_dir, "reference_ranges"):
        exists = session.query(ReferenceRange).filter_by(
            canonical_code=row["canonical"], sex=row.get("sex"), source="curated",
            low=row.get("low"), high=row.get("high"), expected_value=row.get("expected")).first()
        if not exists:
            session.add(ReferenceRange(
                canonical_code=row["canonical"], sex=row.get("sex"),
                age_min=row.get("age_min"), age_max=row.get("age_max"),
                kind=row.get("kind", "numeric"), low=row.get("low"), high=row.get("high"),
                expected_value=row.get("expected"), source="curated",
            ))
    session.commit()


def build_resolver_from_db(session: Session) -> TermResolver:
    aliases = [
        AliasRow(code=a.alias_code, name=a.alias_name, panel_hint=a.panel_hint,
                 unit_hint=a.unit_hint, canonical=a.canonical_code)
        for a in session.query(TermAlias).all()
    ]
    conversions = {}
    for c in session.query(UnitConversion).all():
        key = (c.canonical_code, norm_unit(c.from_unit))
        conversions[key] = (c.factor, c.to_unit)
    range_rows = [
        {"canonical": r.canonical_code, "sex": r.sex, "age_min": r.age_min, "age_max": r.age_max,
         "kind": r.kind, "low": r.low, "high": r.high, "expected": r.expected_value}
        for r in session.query(ReferenceRange).all()
    ]
    terms = {
        t.code: {"code": t.code, "name_cn": t.name_cn, "category": t.category,
                 "value_type": t.value_type, "default_unit": t.default_unit, "loinc": t.loinc}
        for t in session.query(CanonicalTerm).all()
    }
    resolver = TermResolver(aliases=aliases, conversions=conversions, terms=terms)
    resolver.range_rows = range_rows
    return resolver
