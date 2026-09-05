from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.dashboard import render_dashboard
from app.deps import get_db
from app.insights import _age_years
from app.models import Observation, Person

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/{person_id}", response_class=HTMLResponse)
def dashboard(person_id: int, request: Request, window: str = "auto", db: Session = Depends(get_db)):
    """No-auth dashboard page, addressed by numeric person id, shareable by URL."""
    person = db.get(Person, person_id)
    if person is None:
        raise HTTPException(404, f"person {person_id} not found")
    return HTMLResponse(render_dashboard(person, request.app.state.settings.app_tz, db,
                                         request.app.state.settings.seed_dir, window=window))


@router.get("/", response_class=HTMLResponse)
def index(db: Session = Depends(get_db)):
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo

    from app.dashboard import CSS
    from fastapi.responses import HTMLResponse as HR

    persons = db.query(Person).order_by(Person.id).all()
    tz = ZoneInfo("Asia/Shanghai")
    cards = []
    for p in persons:
        n = db.query(Observation).filter_by(person_id=p.id).count()
        unresolved = db.query(Observation).filter(
            Observation.person_id == p.id, Observation.canonical_code.is_(None)).count()
        last = (db.query(Observation).filter_by(person_id=p.id)
                .order_by(Observation.effective_at.desc()).first())
        last_disp = "—"
        if last:
            try:
                last_disp = datetime.fromisoformat(last.effective_at).astimezone(tz).strftime("%m-%d %H:%M")
            except ValueError:
                last_disp = last.effective_at[:16]
        badge = (f"<span style='color:var(--danger);font-size:12px'> · {unresolved} 条待映射</span>"
                 if unresolved else "")
        cards.append(
            f"<a class='pcard' href='/dashboard/{p.id}'>"
            f"<div class='pid'>#{p.id}</div>"
            f"<div class='pname'>{p.name}<span class='pmeta'>{p.sex or '?'} · {_age_years(p)}</span></div>"
            f"<div class='pstats'>{n} 条数据 · 最近更新 {last_disp}{badge}</div>"
            f"<div class='pgo'>进入面板 →</div></a>")
    body = (f"<div class='pgrid'>{''.join(cards)}</div>" if cards else
            "<div class='empty'>暂无成员档案 · 由上游服务 POST /persons 创建</div>")
    extra_css = CSS + """
.wrap { max-width:900px; padding:40px 22px 60px; }
h1 { font-size:22px; margin-bottom:4px; }
.sub { color:var(--muted); font-size:13px; margin-bottom:22px; }
.pgrid { display:grid; grid-template-columns:repeat(auto-fill,minmax(250px,1fr)); gap:14px; }
.pcard { display:block; background:var(--card); border:1px solid var(--line); border-radius:12px;
  padding:16px 18px; text-decoration:none; color:var(--ink); box-shadow:var(--shadow-1);
  transition:box-shadow .15s, transform .15s; }
.pcard:hover { box-shadow:var(--shadow-2); transform:translateY(-1px); }
.pcard .pid { color:var(--muted); font-size:12px; }
.pcard .pname { font-size:19px; font-weight:700; margin:4px 0 6px; }
.pcard .pmeta { color:var(--muted); font-size:13px; font-weight:400; margin-left:8px; }
.pcard .pstats { color:var(--muted); font-size:12.5px; }
.pcard .pgo { color:var(--info); font-size:13px; margin-top:10px; }
"""
    return HR(f"""<!doctype html><html lang='zh'><head><meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='color-scheme' content='light dark'>
<title>HealthHub</title><style>{extra_css}</style></head>
<body><div class='wrap'><h1>HealthHub 成员</h1>
<div class='sub'>家庭健康数据面板 · 数据仅供参考，不构成医疗建议</div>
{body}</div></body></html>""")
