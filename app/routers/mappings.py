import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.deps import get_db, verify_token
from app.llm import LLM, LLMNotConfigured
from app.models import CanonicalTerm, TermAlias, UnresolvedTerm
from app.normalize import norm_code, norm_name, norm_unit
from app.schemas import MappingIn

router = APIRouter(prefix="/mappings", tags=["mappings"])


@router.get("/unresolved")
def list_unresolved(db: Session = Depends(get_db)):
    rows = db.query(UnresolvedTerm).filter_by(status="open").all()
    return [{
        "id": r.id, "raw_code": r.raw_code, "raw_name": r.raw_name, "panel": r.panel,
        "unit": r.unit, "sample_values": json.loads(r.sample_values or "[]"),
        "suggested_canonical": r.suggested_canonical, "suggested_by": r.suggested_by,
    } for r in rows]


class SuggestIn(BaseModel):
    unresolved_id: int


@router.post("/suggest", dependencies=[Depends(verify_token)])
def suggest(body: SuggestIn, request: Request, db: Session = Depends(get_db)):
    """LLM (light model) PROPOSES a mapping. Human must confirm via POST /mappings."""
    row = db.get(UnresolvedTerm, body.unresolved_id)
    if row is None or row.status != "open":
        raise HTTPException(404, "unresolved term not found")
    llm = LLM(request.app.state.settings)
    if not llm.configured:
        raise HTTPException(503, "LLM not configured (set LLM_API_KEY / ANTHROPIC_AUTH_TOKEN)")

    terms = db.query(CanonicalTerm).all()
    catalog = "\n".join(f"- {t.code} | {t.name_cn} | {t.category} | unit={t.default_unit or '-'}" for t in terms)
    user = (
        f"待映射的检验指标：\n- 代号: {row.raw_code or '无'}\n- 名称: {row.raw_name or '无'}\n"
        f"- 组合/panel: {row.panel or '无'}\n- 单位: {row.unit or '无'}\n"
        f"- 出现过的值: {row.sample_values}\n\n"
        f"标准术语目录（code | 中文名 | 类别 | 默认单位）：\n{catalog}\n\n"
        "从目录中选出唯一最可能对应的标准 code。只输出 JSON："
        '{"suggested": "<code 或 null>", "rationale": "<一句话理由>"}'
    )
    try:
        text, _ = llm.complete(
            "你是医学检验术语映射助手。只输出要求的 JSON，不要输出其他内容。",
            user, model=llm.model_light, max_tokens=512)
    except LLMNotConfigured:
        raise HTTPException(503, "LLM not configured")
    try:
        parsed = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
    except json.JSONDecodeError:
        raise HTTPException(502, f"LLM returned non-JSON: {text[:200]}")
    suggested = parsed.get("suggested")
    if suggested and suggested not in {t.code for t in terms}:
        raise HTTPException(502, f"LLM suggested unknown code '{suggested}'")
    row.suggested_canonical = suggested
    row.suggested_by = f"llm:{llm.model_light}"
    from app.models import utcnow_iso
    row.suggested_at = utcnow_iso()
    db.commit()
    return {"id": row.id, "suggested_canonical": suggested, "rationale": parsed.get("rationale"),
            "note": "proposal only — confirm via POST /mappings"}


@router.post("", dependencies=[Depends(verify_token)])
def add_mapping(body: MappingIn, db: Session = Depends(get_db)):
    """Human confirms an alias. Marks matching open unresolved terms resolved."""
    if db.get(CanonicalTerm, body.canonical_code) is None:
        raise HTTPException(404, f"canonical '{body.canonical_code}' not found")
    code, name = norm_code(body.raw_code), norm_name(body.raw_name)
    if code is None and name is None:
        raise HTTPException(400, "raw_code or raw_name required")
    panel_hint = body.panel_hint or None
    unit_hint = norm_unit(body.unit_hint) or body.unit_hint
    exists = db.query(TermAlias).filter_by(alias_code=code, alias_name=name,
                                           panel_hint=panel_hint, unit_hint=unit_hint).first()
    if not exists:
        db.add(TermAlias(alias_code=code, alias_name=name, panel_hint=panel_hint,
                         unit_hint=unit_hint, canonical_code=body.canonical_code))
    resolved = 0
    for row in db.query(UnresolvedTerm).filter_by(status="open").all():
        if (norm_code(row.raw_code) or None) == (code or None) and (norm_name(row.raw_name) or None) == (name or None):
            row.status = "resolved"
            resolved += 1
    db.commit()
    return {"alias": {"code": code, "name": name, "panel_hint": panel_hint,
                      "unit_hint": unit_hint, "canonical": body.canonical_code},
            "unresolved_marked_resolved": resolved,
            "hint": "run POST /admin/reprocess to backfill observations"}
