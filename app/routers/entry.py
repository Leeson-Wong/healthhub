"""Mobile-first manual entry page + unified submit endpoint (JSON or form-encoded)."""
import uuid
from datetime import datetime, timezone
from html import escape as esc_d

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy.orm import Session

from app.dashboard import CSS
from app.deps import get_db, verify_token
from app.ingest import IngestError, ingest_report
from app.models import Observation, Person, SymptomNote

router = APIRouter(tags=["entry"])

# field name -> (canonical code, unit)
FIELDS = [
    ("bp_sys", "BP_SYS", "收缩压", "mmHg", "如 120"),
    ("bp_dia", "BP_DIA", "舒张压", "mmHg", "如 80"),
    ("hr", "HR", "心率", "bpm", "如 72"),
    ("spo2", "SPO2", "血氧饱和度", "%", "如 97"),
    ("temp", "TEMP", "体温", "°C", "如 36.5"),
    ("rr", "RR", "呼吸频率", "/min", "如 16"),
    ("weight", "WEIGHT", "体重", "kg", "如 65.0"),
    ("glu", "GLU_HOME", "血糖(家用)", "mmol/L", "如 5.6"),
    ("sleep", "SLEEP_HRS", "睡眠时长", "h", "如 7.5"),
    ("steps", "STEPS", "步数", "步", "如 6000"),
]

ENTRY_CSS = """
.wrap { max-width:540px; padding:24px 16px 56px; }
h1 { font-size:21px; margin-bottom:4px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:16px; }
h2 { font-size:14px; color:var(--info); margin:0 0 10px; }
.fcard { background:var(--card); border:1px solid var(--line); border-radius:12px;
  box-shadow:var(--shadow-1); padding:14px 14px 6px; margin-bottom:14px; }
select, input, textarea { width:100%; padding:11px 12px; font-size:16px;
  border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--ink);
  margin-bottom:12px; }
input:focus, select:focus, textarea:focus { outline:2px solid var(--info); outline-offset:0;
  border-color:var(--info); }
.frow { display:flex; gap:10px; } .frow > div { flex:1; }
label { display:block; font-size:12.5px; color:var(--muted); margin-bottom:3px; }
button[type=submit] { width:100%; padding:14px; font-size:17px; border:none; border-radius:10px;
  background:var(--info); color:#fff; font-weight:600; margin-top:6px; cursor:pointer; }
button[type=submit]:hover { filter:brightness(1.08); }
textarea { min-height:70px; resize:vertical; }
.okcard { background:var(--card); border:1px solid var(--line); border-left:4px solid var(--ok);
  border-radius:10px; padding:12px 16px; margin-bottom:16px; box-shadow:var(--shadow-1); }
.okcard .t { font-weight:700; color:var(--ok); font-size:14px; margin-bottom:6px; }
.okcard ul { list-style:none; }
.okcard li { font-size:13px; padding:4px 0; border-bottom:1px dashed var(--line);
  display:flex; justify-content:space-between; }
.okcard li:last-child { border-bottom:none; }
.okcard li b { font-variant-numeric:tabular-nums; }
.okcard .links { margin-top:8px; font-size:13px; }
.lastentry { font-size:12px; color:var(--muted); margin:-8px 0 14px; }
"""

SUCCESS_HTML = """<div class='okcard'><div class='t'>已保存 ✓</div><ul>{rows}</ul>
<div class='links'>查看 <a href='/dashboard/{pid}'>数据面板</a> · <a href='/entry'>继续录入</a></div></div>"""


def _entry_page(db: Session, success: dict | None = None, person_id: int | None = None) -> HTMLResponse:
    persons = db.query(Person).order_by(Person.id).all()
    if not persons:
        return HTMLResponse("<html><body style='font-family:system-ui;padding:40px'>"
                            "暂无成员档案，请先 POST /persons 创建。</body></html>", status_code=200)
    opts = "".join(
        f"<option value='{p.id}'{' selected' if p.id == person_id else ''}>#{p.id} {p.name}</option>"
        for p in persons)
    now_local = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M")

    last_line = ""
    if person_id:
        last = (db.query(Observation).filter(Observation.person_id == person_id)
                .order_by(Observation.effective_at.desc()).first())
        if last:
            try:
                lt = datetime.fromisoformat(last.effective_at).astimezone().strftime("%m-%d %H:%M")
            except ValueError:
                lt = last.effective_at[:16]
            last_line = f"<div class='lastentry'>上次录入：{lt}</div>"

    primary, secondary = [], []
    for key, code, label, unit, ph in FIELDS:
        field = (f"<div><label>{label}（{unit}）</label>"
                 f"<input name='{key}' inputmode='decimal' placeholder='{ph}'></div>")
        (primary if code in ("BP_SYS", "BP_DIA", "HR", "SPO2", "TEMP", "WEIGHT") else secondary).append(field)
    extra_toggle = ""
    if secondary:
        extra_toggle = ("<details class='fcard' style='padding:10px 14px;'>"
                        "<summary style='cursor:pointer;color:var(--muted);font-size:13px;'>"
                        "展开更多指标（呼吸/血糖/睡眠/步数）</summary>"
                        f"<div style='padding-top:10px;'>{''.join(secondary)}</div></details>")

    msg = ""
    if success:
        rows = "".join(f"<li><span>{esc_d(l)}</span><b>{esc_d(v)} {esc_d(u)}</b></li>"
                       for l, v, u in success.get("detail", []))
        if success.get("symptom"):
            rows += f"<li><span>症状记录</span><b>{esc_d(success.get('symptom_text', ''))}</b></li>"
        if success.get("duplicate"):
            rows += "<li><span>说明</span><b>重复提交，未新增</b></li>"
        msg = SUCCESS_HTML.format(rows=rows or "<li><span>无</span><b></b></li>", pid=person_id)

    return HTMLResponse(f"""<!doctype html><html lang='zh'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='color-scheme' content='light dark'>
<title>健康录入</title><style>{CSS}{ENTRY_CSS}</style></head><body><div class='wrap'>
<h1>健康数据录入</h1>
<div class='sub'>填什么记什么，留空即跳过 · 家用设备数据</div>{msg}{last_line}
<form method='post' action='/entry/submit'>
<div class='fcard'>
<label>成员</label>
<select name='person_id' required>{opts}</select>
<label>测量时间</label>
<input type='datetime-local' name='sampled_at' value='{now_local}'>
<h2>生命体征</h2>
<div class='frow'>{''.join(primary[:2])}</div>
<div class='frow'>{''.join(primary[2:4])}</div>
<div class='frow'>{''.join(primary[4:6])}</div>
</div>
{extra_toggle}
<div class='fcard'>
<h2>症状记录（可选）</h2>
<div class='frow'><div>
<label>程度（1轻-5重）</label>
<select name='symptom_sev'><option value=''>—</option>
<option value='1'>1 轻微</option><option value='2'>2</option><option value='3'>3 中等</option>
<option value='4'>4</option><option value='5'>5 严重</option></select>
</div></div>
<textarea name='symptom_text' placeholder='如：晚饭后头晕 20 分钟，休息后缓解'></textarea>
</div>
<input type='hidden' name='client_id' value='{uuid.uuid4()}'>
<button type='submit'>保存记录</button>
</form></div></body></html>""")


@router.get("/entry", response_class=HTMLResponse)
def entry_page(db: Session = Depends(get_db)):
    return _entry_page(db)


def _build_items(form: dict) -> list[dict]:
    items = []
    for key, code, label, unit, _ph in FIELDS:
        raw = (form.get(key) or "").strip()
        if not raw:
            continue
        items.append({"code": code, "name": label, "value": raw, "unit": unit})
    return items


def _submit(db: Session, form: dict):
    person_id = form.get("person_id")
    if not person_id:
        raise IngestError("person_id required", 400)
    items = _build_items(form)
    symptom_text = (form.get("symptom_text") or "").strip()
    symptom_sev = form.get("symptom_sev") or None
    if not items and not symptom_text:
        raise IngestError("nothing to record: fill at least one vital or symptom", 400)

    sampled_at = (form.get("sampled_at") or "").strip() or None
    client_id = (form.get("client_id") or "").strip() or uuid.uuid4().hex
    summary = {"items": 0, "symptom": False, "detail": [], "symptom_text": symptom_text}
    if items:
        payload = {
            "person_id": int(person_id),
            "external_id": f"entry:{person_id}:{client_id}",
            "panel": "家庭监测",
            "sampled_at": sampled_at,
            "items": items,
        }
        result = ingest_report(db, payload)
        summary["items"] = result["items_total"]
        summary["duplicate"] = not result["created"]
        label_map = {code: (label, unit) for _k, code, label, unit, _p in FIELDS}
        summary["detail"] = [(label_map[i["code"]][0], i["value"], label_map[i["code"]][1]) for i in items]
    if symptom_text:
        occurred = sampled_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
        db.add(SymptomNote(person_id=int(person_id), occurred_at=occurred,
                           severity=int(symptom_sev) if symptom_sev else None, text=symptom_text))
        db.commit()
        summary["symptom"] = True
    return int(person_id), summary


@router.post("/entry/submit", dependencies=[Depends(verify_token)])
async def entry_submit(request: Request, db: Session = Depends(get_db)):
    content_type = request.headers.get("content-type", "")
    if "application/json" in content_type:
        body = await request.json()
        form = {k: ("" if v is None else str(v)) for k, v in body.items()}
        # JSON API form: allow items dict {code: value}
        if isinstance(body.get("items"), dict):
            form.update({k: ("" if v is None else str(v)) for k, v in body["items"].items()})
        is_json = True
    else:
        form = dict(await request.form())
        is_json = False
    try:
        person_id, summary = _submit(db, form)
    except IngestError as e:
        if is_json:
            return JSONResponse({"error": str(e)}, status_code=e.status)
        return HTMLResponse(f"<p style='color:#c62828;padding:20px'>{e}</p>", status_code=e.status)
    if is_json:
        return {"person_id": person_id, **{k: v for k, v in summary.items() if k != "detail"},
                "detail": summary["detail"]}
    return _entry_page(db, success=summary, person_id=person_id)
