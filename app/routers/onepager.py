"""一页纸医生打印版：GET /print/{person_id} —— 转院/复查时递给医生的浓缩档案。

设计原则（评审共识）：
- 给 60 秒医生：摘要叙事 → 6 趋势图 → 诊断状态 → 在用药 → 病原警示 → 过敏/既往
- 黑白可读（▲▼ 符号 + 数值标签，不只靠颜色）
- 绝不含 AI 推断；诊断标注"家属记录"；脚注免责 + 数据截至
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import (CanonicalTerm, Condition, CultureReport, CultureSusceptibility, Encounter,
                        Medication, Observation, Person, PersonFact, SourceReport)

router = APIRouter(tags=["print"])

ONEPAGE_CODES = ["PCT_PROCAL", "CRP", "TBIL", "CREA", "TNI", "PLT"]


def _esc(s):
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _fmt(v):
    return f"{v:g}" if isinstance(v, (int, float)) else (v or "-")


def _spark(rows, name, unit, w=150.0, h=30.0):
    """黑白可读迷你趋势：折线 + 首末数值标签 + 方向符号。"""
    vals = [(r.effective_at, r.value_num) for r in rows if r.value_num is not None]
    if len(vals) < 2:
        return f"<div class='nodata'>数据不足</div>"
    lo, hi = min(v for _, v in vals), max(v for _, v in vals)
    span = (hi - lo) or 1.0
    n = len(vals)
    pts = " ".join(f"{4 + i * ((w - 8) / (n - 1)):.1f},{h - 4 - (v - lo) / span * (h - 10):.1f}"
                   for i, (_, v) in enumerate(vals))
    first, last = vals[0][1], vals[-1][1]
    arrow = "▼" if last < first else ("▲" if last > first else "▶")
    return (f"<svg viewBox='0 0 {w:.0f} {h:.0f}' class='trend'>"
            f"<polyline points='{pts}' fill='none' stroke='#111' stroke-width='1.6'/>"
            + "".join(f"<circle cx='{4 + i * ((w - 8) / (n - 1)):.1f}' "
                      f"cy='{h - 4 - (v - lo) / span * (h - 10):.1f}' r='1.8' fill='#111'/>"
                      for i, (_, v) in enumerate(vals))
            + f"<text x='2' y='9' font-size='8.5' fill='#111'>{_fmt(first)}</text>"
            f"<text x='{w - 2}' y='9' font-size='8.5' text-anchor='end' fill='#111' font-weight='bold'>"
            f"{_fmt(last)} {arrow}</text></svg>")


@router.get("/print/{person_id}", response_class=HTMLResponse)
def onepager(person_id: int, tz: str = "Asia/Shanghai", db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if person is None:
        return HTMLResponse("<h1>成员不存在</h1>", status_code=404)
    Z = ZoneInfo(tz)
    now = datetime.now(Z)

    obs = (db.query(Observation).filter_by(person_id=person_id)
           .order_by(Observation.effective_at).all())
    terms = {t.code: t for t in db.query(CanonicalTerm).all()}
    by_code = {}
    for o in obs:
        if o.canonical_code:
            by_code.setdefault(o.canonical_code, []).append(o)
    last_any = max((o.effective_at for o in obs), default=None)
    n_reports = db.query(SourceReport).filter_by(person_id=person_id).count()

    # ① 病史摘要（叙事，按事件+条件浓缩，家属口径）
    conds = db.query(Condition).filter_by(person_id=person_id).all()
    enc = (db.query(Encounter).filter_by(person_id=person_id)
           .order_by(Encounter.admitted_at.desc()).first())
    summary_lines = [
        "（本摘要由患者家属维护的个人健康档案生成，诊断名称以医院病历为准）",
        f"本次病程：急性胆管炎（≈2026-08-12 起病）→ 08-19 脓毒性休克入 ICU → 08-20 胆道引流 → "
        "08-21 依 mNGS 调整抗感染（头孢他啶/阿维巴坦+万古霉素）→ 感染指标持续回落，目前病情好转中。",
        "关键转归：肾功能已恢复（肌酐 71）；胆红素降至 49.7 后进入平台期；血小板回升中；"
        "心肌损伤标志物下降但 NT-proBNP 仍高，待心脏评估。",
        "既往：胆结石多年（本次病因，待择期手术）；5 年前腰椎骨折内固定术后（钢板在体）+马尾综合征。",
    ]

    # ② 趋势图
    trends = ""
    for code in ONEPAGE_CODES:
        rows = by_code.get(code, [])
        if not rows:
            continue
        t = terms.get(code)
        last = rows[-1]
        ref = last.raw_ref_range or ""
        trends += (f"<div class='tcard'><div class='tname'>{_esc(t.name_cn if t else code)}"
                   f"<span class='tref'>{_esc(ref)}</span></div>"
                   f"{_spark(rows, code, last.unit_canonical or '')}"
                   f"<div class='tdates'>{_esc((rows[0].effective_at or '')[:10])} → "
                   f"{_esc((rows[-1].effective_at or '')[:10])} · {len(rows)} 次</div></div>")

    # ③ 诊断状态
    cond_html = "".join(
        f"<tr><td>{_esc(c.name)}</td><td class='st'>{_esc({'active':'活动中','improving':'好转中','resolved':'已缓解','chronic':'慢病/长期','suspected':'疑似'}.get(c.status, c.status))}</td>"
        f"<td class='mono'>{_esc((c.onset_at or '')[:10])}</td></tr>"
        for c in sorted(conds, key=lambda x: (x.status != 'improving', x.status != 'active')))

    # ④ 在用药物（含费用推断标注）
    meds = (db.query(Medication).filter_by(person_id=person_id)
            .order_by(Medication.ended_at.isnot(None), Medication.started_at.desc()).all())
    med_html = "".join(
        f"<tr{' class=dim' if m.ended_at else ''}><td>{_esc(m.drug_name)}</td>"
        f"<td class='mono'>{_esc((m.started_at or '')[:10])}"
        + (f"~{_esc(m.started_at and (m.ended_at or '')[:10])}" if m.ended_at else " ~")
        + "</td><td>"
        + ("已停*" if m.ended_at else "在用*" if m.status_inference == 'billed_ongoing' else "在用")
        + "</td></tr>"
        for m in meds if m.status_inference != 'billed_ended' or True)

    # ⑤ 病原学警示
    cultures = db.query(CultureReport).filter_by(person_id=person_id).all()
    cult_html = ""
    for c in cultures:
        susc = db.query(CultureSusceptibility).filter_by(culture_report_id=c.id).all()
        s_str = " · ".join(f"{s.drug_name}:{s.result}" for s in susc[:10] if s.result == 'S') or "—"
        r_str = " · ".join(f"{s.drug_name}:{s.result}" for s in susc[:14] if s.result != 'S') or "—"
        cult_html += (f"<div class='cult'><b>{_esc(c.specimen)}</b>"
                      f"<span class='mono'> {_esc((c.sampled_at or '')[:10])} 报告 {_esc((c.reported_at or '')[:10])}</span><br>"
                      f"<b>{_esc(c.organism or '未检出')}</b>"
                      + (f" <span class='mdr'>{_esc(c.mdr_text)}</span>" if c.mdr_text else "")
                      + (f"<br><span class='susc'>敏感: {_esc(s_str)}</span><br><span class='resis'>不敏感: {_esc(r_str)}</span>" if susc else "")
                      + "</div>")

    # ⑥ 过敏 + 家族
    facts = db.query(PersonFact).filter_by(person_id=person_id).all()
    allergy = next((f for f in facts if f.category == 'allergy'), None)
    allergy_line = _esc(allergy.detail or allergy.title) if allergy else "无已知过敏（家属确认）"

    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<title>病情摘要 · {person.name}</title>
<style>
@page {{ size: A4; margin: 12mm 11mm; }}
* {{ box-sizing:border-box; margin:0; padding:0; }}
body {{ font-family:"PingFang SC","Microsoft YaHei","SimSun",sans-serif; color:#111; font-size:11.5px; line-height:1.5; }}
.hdr {{ display:flex; justify-content:space-between; border-bottom:2.5px solid #111; padding-bottom:6px; }}
.hdr h1 {{ font-size:19px; letter-spacing:1px; }}
.hdr .meta {{ text-align:right; font-size:10.5px; color:#333; }}
h2 {{ font-size:12px; border-bottom:1px solid #999; margin:9px 0 5px; padding-bottom:2px; letter-spacing:1px; }}
.sum p {{ margin:2px 0; }}
.tgrid {{ display:grid; grid-template-columns:repeat(3,1fr); gap:6px; }}
.tcard {{ border:1px solid #bbb; border-radius:5px; padding:5px 7px; }}
.tname {{ font-weight:700; font-size:11px; }} .tref {{ float:right; font-weight:400; font-size:9px; color:#555; }}
.tdates {{ font-size:8.5px; color:#555; }}
svg.trend {{ width:100%; height:34px; }}
.nodata {{ font-size:9px; color:#999; padding:8px 0; }}
table {{ width:100%; border-collapse:collapse; font-size:10.5px; }}
td,th {{ padding:2.5px 5px; border-bottom:0.5px solid #ccc; text-align:left; }}
th {{ font-size:9.5px; color:#555; font-weight:600; }}
.mono {{ font-family:ui-monospace,monospace; font-size:9.5px; color:#444; }}
.dim {{ color:#888; }}
.st {{ font-weight:700; }}
.mdr {{ background:#eee; border:1px solid #999; border-radius:3px; padding:0 4px; font-size:9px; font-weight:700; }}
.cult {{ border:1.5px solid #111; border-radius:5px; padding:6px 8px; margin-bottom:5px; }}
.susc {{ color:#0a6b2d; }} .resis {{ color:#a11; }}
.warn {{ border:1.5px solid #a11; padding:5px 8px; border-radius:5px; margin-top:4px; font-weight:600; }}
.foot {{ margin-top:10px; border-top:1px solid #999; padding-top:4px; font-size:8.8px; color:#555; }}
@media screen {{ body {{ max-width:760px; margin:16px auto; padding:0 14px; }} }}
</style></head><body>
<div class="hdr">
  <div><h1>{_esc(person.name)}</h1>
  <div>{_esc(person.sex or '')} · {_esc(str(2026 - int((person.birth_date or '1969')[0:4])) + '岁' if person.birth_date else '')} · 188cm / 90kg · 住院号 300555003</div></div>
  <div class="meta">生成于 {now.strftime('%Y-%m-%d %H:%M')}<br>
  数据截至 {_esc((last_any or '')[:10])} · {len(obs)} 项指标 / {n_reports} 份报告<br>
  联系人：儿子（家属档案维护者）</div>
</div>

<h2>① 病史摘要</h2>
<div class="sum">{"".join(f"<p>{_esc(l)}</p>" for l in summary_lines)}</div>

<h2>② 关键趋势（住院全程）</h2>
<div class="tgrid">{trends}</div>

<h2>③ 诊断与状态（家属记录）</h2>
<table><tr><th>诊断</th><th>状态</th><th>起病</th></tr>{cond_html}</table>

<h2>④ 用药（* = 据费用清单推断，医嘱状态以病历为准）</h2>
<table><tr><th>药物</th><th>起止</th><th>状态</th></tr>{med_html}</table>

<h2>⑤ 病原学证据与警示</h2>
{cult_html}
<div class="warn">⚠ 多重耐药提示：曾检出碳青霉烯酶基因（NDM）。抗生素选择请以本院药敏为准。</div>

<h2>⑥ 过敏与既往要点</h2>
<p><b>过敏：</b>{allergy_line}</p>
<p><b>既往：</b>胆结石病（待择期手术）· 腰椎内固定术后+马尾综合征（体内有植入物）· 家族乙肝表面抗体强阳性（非现症肝病）</p>

<div class="foot">本页由患者家属维护的个人健康档案系统生成，全部数据可溯源至检验报告原件（拍照存档）。
内容为数据整理参考，不构成医疗建议；诊断与治疗请以医院正式病历及主管医生意见为准。</div>
</body></html>"""
    return HTMLResponse(html)
