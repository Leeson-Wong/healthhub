"""Deterministic prompt assembly + LLM advisory generation (reference only, not medical advice)."""
from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.llm import LLM
from app.models import CanonicalTerm, CultureReport, CultureSusceptibility, InsightReport, Observation, Person

SYSTEM_PROMPT = (
    "你是一名健康数据分析助手。用户会提供某位家庭成员的已标准化健康数据（检验、生命体征、用药、症状等，表格形式）。"
    "请基于数据给出参考性的趋势解读和需要关注的指标，用中文、结构化（分节+要点）输出。"
    "规则：1) 只依据给出的数据，不编造数值；2) flag 列中 HIGH/LOW/ABNORMAL_CAT 是按参考范围计算的，"
    "NEG/POS 是化验单原始判读（如乙肝抗体阳性可能是好事，不要一律当异常）；"
    "3) 家用设备（血压计、血氧仪、血糖仪等）数据精度与院内有差异，解读时关注趋势与持续异常而非单点；"
    "4) 明确说明这是数据整理参考，不构成医疗建议，具体诊疗请遵主管医生意见；"
    "5) 【诊断红线】只描述实验现象与趋势，禁止使用诊断性结论：不说\"心功能不全/心力衰竭/心梗/感染性休克加重\"等，"
    "应表述为\"标志物升高（数值），较前变化（幅度），需结合心电图/超声/临床评估判断\"。某指标升高可能有多种原因"
    "（如 NT-proBNP 在感染、肾功能变化、容量负荷时均可升高），必须列出可能性而非选定一个；"
    "6) 【证据引用】每个判断必须引用具体数值和日期；"
    "7) 【数据边界】如果某判断需要的数据（影像、体征、用药剂量）不在表中，明确说\"数据中未包含\"，不要推测。"
)


def _age_years(person: Person) -> str:
    if not person.birth_date:
        return "未知年龄"
    born = datetime.fromisoformat(person.birth_date)
    if born.tzinfo is None:
        born = born.replace(tzinfo=timezone.utc)
    age = (datetime.now(timezone.utc) - born).days / 365.25
    return f"{int(age)}岁"


def _fmt_val(o: Observation) -> str:
    if o.value_num is not None:
        return f"{o.value_num:g}"
    return o.value_text or o.raw_value


def _local_ts(utc_iso: str, tz_name: str) -> str:
    from zoneinfo import ZoneInfo
    try:
        return datetime.fromisoformat(utc_iso).astimezone(ZoneInfo(tz_name)).isoformat(timespec="minutes")
    except (ValueError, TypeError):
        return utc_iso[:16]


def build_prompt(db: Session, person: Person, date_from: str | None, date_to: str | None,
                 topic: str | None = None, tz_name: str = "Asia/Shanghai") -> str:
    q = db.query(Observation).filter(Observation.person_id == person.id,
                                     Observation.canonical_code.isnot(None))
    if date_from:
        q = q.filter(Observation.effective_at >= date_from)
    if date_to:
        q = q.filter(Observation.effective_at <= date_to)
    obs = q.order_by(Observation.effective_at).all()
    terms = {t.code: t for t in db.query(CanonicalTerm).all()}

    header = (f"## 成员档案\n- 姓名：{person.name}（{person.sex or '?'}，{_age_years(person)}）\n"
              f"- 数据窗口：{date_from or '最早'} ~ {date_to or '最新'}\n"
              f"- 指标记录数：{len(obs)}\n")
    if topic:
        header += f"- 重点关注：{topic}\n"

    # ---- long-term context: vitals summary / meds / symptoms / encounters ----
    from app.models import Encounter, Medication, SymptomNote
    VITAL_CODES = ("BP_SYS", "BP_DIA", "HR", "SPO2", "TEMP", "RR", "WEIGHT", "GLU_HOME", "SLEEP_HRS", "STEPS")
    extra_parts = []
    vitals = [o for o in obs if o.canonical_code in VITAL_CODES]
    if vitals:
        vlines = ["| 指标 | 次数 | 均值 | 最低 | 最高 | 最新 | 单位 | 异常次数 |",
                  "|---|---|---|---|---|---|---|---|"]
        for code in VITAL_CODES:
            rows_v = [o for o in vitals if o.canonical_code == code]
            if not rows_v:
                continue
            nums = [o.value_num for o in rows_v if o.value_num is not None]
            if not nums:
                continue
            term = terms.get(code)
            abnormal = sum(1 for o in rows_v if o.flag_computed in ("HIGH", "LOW"))
            last = rows_v[-1]
            last_disp = last.value_num if last.value_num is not None else (last.value_text or "")
            vlines.append(
                f"| {term.name_cn if term else code} | {len(nums)} | {sum(nums) / len(nums):.1f} | "
                f"{min(nums):g} | {max(nums):g} | {last_disp:g} | {last.unit_canonical or ''} | {abnormal} |")
        extra_parts.append("## 生命体征摘要（窗口内）\n" + "\n".join(vlines))

    meds_active = [m for m in db.query(Medication).filter(
        Medication.person_id == person.id, Medication.ended_at.is_(None)).all()]
    if meds_active:
        med_desc = "；".join(
            f"{m.drug_name}{(' ' + m.dose) if m.dose else ''}{(' ' + m.frequency) if m.frequency else ''}"
            for m in meds_active)
        extra_parts.append(f"## 当前用药\n{med_desc}")

    symps = (db.query(SymptomNote).filter(SymptomNote.person_id == person.id)
             .order_by(SymptomNote.occurred_at.desc()).limit(5).all())
    if symps:
        sym_desc = "；".join(f"{s.occurred_at[:10]}{'（程度' + str(s.severity) + '）' if s.severity else ''}：{s.text}"
                             for s in reversed(symps))
        extra_parts.append(f"## 近期症状\n{sym_desc}")

    encs = (db.query(Encounter).filter(Encounter.person_id == person.id)
            .order_by(Encounter.admitted_at.desc()).limit(3).all())
    if encs:
        e_desc = "；".join(
            f"{e.kind} {e.hospital or ''}{e.department or ''} {(e.admitted_at or '')[:10]}~"
            f"{(e.discharged_at or '在院')[:10]}，诊断：{e.diagnosis or '未记录'}"
            + (f"，主治：{e.doctor_name}（{e.doctor_phone}）" if e.doctor_name else "")
            for e in encs)
        extra_parts.append(f"## 就诊档案\n{e_desc}")

    by_code: dict[str, list[Observation]] = {}
    for o in obs:
        by_code.setdefault(o.canonical_code, []).append(o)

    abnormal_lines = ["| 指标 | 最新值 | 单位 | 参考范围 | 计算标记 | 原始标记 | 前值 | 变化 |",
                      "|---|---|---|---|---|---|---|---|"]
    trend_lines = ["| 指标 | 序列（时间:值） |", "|---|---|"]
    abnormal_count = 0
    for code, rows in sorted(by_code.items()):
        term = terms.get(code)
        unit = next((r.unit_canonical for r in rows if r.unit_canonical), "") or ""
        ref = next((r.raw_ref_range for r in reversed(rows) if r.raw_ref_range), "")
        last = rows[-1]
        flag_c = last.flag_computed or "-"
        flag_s = last.flag_source or "-"
        if last.flag_computed in ("HIGH", "LOW", "ABNORMAL_CAT"):
            abnormal_count += 1
            prev = _fmt_val(rows[-2]) if len(rows) > 1 else "-"
            delta = ""
            if len(rows) > 1 and last.value_num is not None and rows[-2].value_num is not None and rows[-2].value_num != 0:
                delta = f"{(last.value_num - rows[-2].value_num) / rows[-2].value_num * 100:+.1f}%"
            abnormal_lines.append(
                f"| {term.name_cn if term else code} | {_fmt_val(last)} | {unit} | {ref} | {flag_c} | {flag_s} | {prev} | {delta} |")
        if len(rows) >= 3:
            seq = "; ".join(f"{_local_ts(r.effective_at, tz_name)}:{_fmt_val(r)}" for r in rows)
            trend_lines.append(f"| {term.name_cn if term else code} | {seq} |")

    parts = [header]
    parts.extend(extra_parts)
    if abnormal_count:
        parts.append(f"## 当前异常指标（{abnormal_count} 项）\n" + "\n".join(abnormal_lines))
    else:
        parts.append("## 当前异常指标\n窗口内无计算异常项。")
    parts.append("## 趋势序列\n" + "\n".join(trend_lines))

    cultures = db.query(CultureReport).filter(CultureReport.person_id == person.id)
    if date_from:
        cultures = cultures.filter(CultureReport.reported_at >= date_from)
    culture_rows = cultures.all()
    if culture_rows:
        clines = []
        for c in culture_rows:
            susc = db.query(CultureSusceptibility).filter_by(culture_report_id=c.id).all()
            r_drugs = [f"{s.drug_name}({s.drug_code})" for s in susc if s.result == "R"]
            s_drugs = [f"{s.drug_name}({s.drug_code})" for s in susc if s.result == "S"]
            clines.append(f"- 标本【{c.specimen}】{c.reported_at[:10] if c.reported_at else ''}："
                          f"{c.organism or '未检出'} {c.organism_comment or ''}；MDR：{c.mdr_text or '无'}；"
                          f"耐药：{('、'.join(r_drugs)) or '无记录'}；敏感：{('、'.join(s_drugs)) or '无记录'}")
        parts.append("## 微生物培养与药敏\n" + "\n".join(clines))
    return "\n\n".join(parts)


def generate_insight(db: Session, llm: LLM, person: Person, *, kind: str,
                     date_from: str | None, date_to: str | None,
                     report_date: str | None = None, topic: str | None = None,
                     model: str | None = None, tz_name: str = "Asia/Shanghai") -> InsightReport:
    prompt = build_prompt(db, person, date_from, date_to, topic, tz_name=tz_name)
    text, usage = llm.complete(SYSTEM_PROMPT, prompt, model=model or llm.model_main)
    if not text.strip():
        raise RuntimeError(f"LLM 返回空正文 usage={usage}（疑似 thinking 耗尽 max_tokens）")
    row = InsightReport(
        person_id=person.id, kind=kind, report_date=report_date,
        window_from=date_from, window_to=date_to,
        model=model or llm.model_main, prompt_md=prompt, response_md=text,
        usage_json=json.dumps(usage), status="ok",
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
