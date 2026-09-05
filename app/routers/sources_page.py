"""数据管理台独立页：来源账本 + 待处理队列 + 原件画廊 + 修正留痕。GET /sources"""

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app.deps import get_db
from app.models import (Observation, ObservationRevision, PendingUpload, Person, SourceFile,
                        SourceReport)

router = APIRouter(tags=["sources"])

PROV_LABEL = {"upload_ocr": "📷拍照", "api": "推送", "entry": "录入", "csv_import": "导入"}

CSS = """
:root{color-scheme:light dark;--bg:#f2f5f8;--card:#ffffff;--ink:#1b2733;--muted:#5f7182;
--line:#dfe6ee;--info:#0f6aad;--ok:#2c7a4b;--danger:#c0392b;--accent:var(--info)}
@media(prefers-color-scheme:dark){:root{--bg:#0e141b;--card:#161e28;--ink:#d9e2ec;--muted:#8fa1b3;--line:#253140;--info:#6db3e8}}
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:"PingFang SC","Microsoft YaHei",system-ui,sans-serif;background:var(--bg);color:var(--ink);
font-size:16px;line-height:1.6;padding:18px;max-width:860px;margin:0 auto}
h1{font-size:22px;margin-bottom:2px} .sub{color:var(--muted);font-size:13.5px;margin-bottom:16px}
h2{font-size:15px;color:var(--info);margin:22px 0 10px}
.card{background:color-mix(in srgb,var(--card) 76%,transparent);-webkit-backdrop-filter:blur(16px) saturate(1.5);
backdrop-filter:blur(16px) saturate(1.5);border:1px solid color-mix(in srgb,var(--ink) 8%,transparent);
border-radius:16px;padding:12px 14px;margin-bottom:10px}
.tscroll{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:14.5px}
th{font-size:12px;color:var(--muted);text-align:left;font-weight:500;padding:6px 8px;border-bottom:1px solid var(--line)}
td{padding:7px 8px;border-bottom:1px solid var(--line);white-space:nowrap}
tr:last-child td{border-bottom:none}
.mono{font-family:ui-monospace,monospace;font-size:12.5px;color:var(--muted)}
a{color:var(--info)}
.pill{display:inline-block;font-size:11px;font-weight:700;border:1px solid;border-radius:999px;padding:1px 8px}
.pill.pending{color:#b66800;border-color:#b66800;background:rgba(255,149,0,.12)}
.pill.processing{color:var(--info);border-color:var(--info)}
.pill.done{color:var(--ok);border-color:var(--ok)}
.pill.error{color:var(--danger);border-color:var(--danger)}
.files{display:flex;gap:8px;flex-wrap:wrap;margin-top:6px}
.files img{width:64px;height:64px;object-fit:cover;border-radius:8px;border:1px solid var(--line)}
.files a.pdf{font-size:12px;padding:6px 10px;border:1px solid var(--line);border-radius:8px;color:var(--info);text-decoration:none}
.back{display:block;text-align:center;margin-top:20px;color:var(--info);text-decoration:none;font-size:14px}
.empty{color:var(--muted);font-size:14px;padding:12px 4px}
.rev{border-left:3px solid var(--danger);padding:8px 12px;margin-bottom:8px}
.rev b{color:var(--danger)} .rev .mono{margin-left:6px}
"""


def _lt(utc_iso: str | None) -> str:
    return (utc_iso or "")[:16].replace("T", " ")


@router.get("/sources", response_class=HTMLResponse)
def sources_page(person_id: int, db: Session = Depends(get_db)):
    person = db.get(Person, person_id)
    if person is None:
        return HTMLResponse("<h1>成员不存在</h1>", status_code=404)

    # 待处理队列
    pend = (db.query(PendingUpload).filter_by(person_id=person_id)
            .order_by(PendingUpload.id.desc()).limit(30).all())
    pend_html = ""
    for u in pend:
        n_files = 0  # 文件数在队列目录按前缀计数，页面侧从 source_files 无法反查 —— 显示状态与备注即可
        pend_html += (
            f"<div class='card' style='display:flex;gap:10px;align-items:baseline;flex-wrap:wrap'>"
            f"<span class='pill {u.status}'>{u.status}</span>"
            f"<b>#{u.id}</b><span>{_esc(u.note or '(无备注)')}</span>"
            f"<span class='mono'>{_esc(u.submitted_by or '')} · {_lt(u.created_at)}</span>"
            + (f"<span class='mono'>→ report #{u.source_report_id}</span>" if u.source_report_id else "")
            + (f"<span style='color:var(--danger);font-size:13px'>{_esc(u.error or '')}</span>" if u.error else "")
            + "</div>")
    if not pend_html:
        pend_html = "<div class='empty'>队列为空 · 面板 📷 按钮上传报告单</div>"

    # 来源账本（含原件缩略图）
    reports = (db.query(SourceReport).filter_by(person_id=person_id)
               .order_by(SourceReport.sampled_at.desc(), SourceReport.id.desc()).all())
    led_rows = ""
    for r in reports:
        files = db.query(SourceFile).filter_by(source_report_id=r.id).order_by(SourceFile.id).all()
        n_obs = db.query(Observation).filter_by(source_report_id=r.id).count()
        thumbs = ""
        for f in files[:6]:
            if f.mime.startswith("image/"):
                thumbs += f"<a href='/files/{f.id}' target='_blank'><img src='/files/{f.id}' alt='原件' loading='lazy'></a>"
            else:
                thumbs += f"<a class='pdf' href='/files/{f.id}' target='_blank'>📄 PDF</a>"
        if len(files) > 6:
            thumbs += f"<span class='mono'>+{len(files) - 6}</span>"
        led_rows += (
            f"<tr><td class='mono'>{(r.sampled_at or '')[:10]}</td>"
            f"<td>{_esc(r.panel or r.external_id[:18])}</td>"
            f"<td>{PROV_LABEL.get(r.provenance or 'api', r.provenance)}</td>"
            f"<td class='mono'>{n_obs}</td>"
            f"<td class='mono'>{len(files)}</td></tr>"
            + (f"<tr><td colspan='5' style='padding-top:2px'><div class='files'>{thumbs}</div></td></tr>"
               if thumbs else ""))

    # 修正留痕（最近 30 条）
    revs = (db.query(ObservationRevision, Observation)
            .join(Observation, ObservationRevision.observation_id == Observation.id)
            .filter(Observation.person_id == person_id)
            .order_by(ObservationRevision.id.desc()).limit(30).all())
    rev_html = ""
    for rv, ob in revs:
        rev_html += (
            f"<div class='card rev'><b>{_esc(ob.raw_name or ob.canonical_code or '')}</b> "
            f"{_esc(rv.old_raw_value)} → {_esc(rv.new_raw_value)}"
            f"<span class='mono'>{_esc(rv.edited_by or '?')} · {_lt(rv.created_at)}</span>"
            + (f"<div style='font-size:13px;color:var(--muted)'>{_esc(rv.reason or '')}</div>" if rv.reason else "")
            + "</div>")
    if not rev_html:
        rev_html = "<div class='empty'>暂无修正记录</div>"

    n_files_total = db.query(SourceFile).count()
    html = f"""<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="light dark">
<title>数据管理台 · {person.name}</title><style>{CSS}</style></head><body>
<h1>🗂 数据管理台</h1>
<div class="sub">{person.name} · {len(reports)} 份报告 · {n_files_total} 张原件 · 每个数值可回溯</div>
<h2>待处理上传队列</h2>{pend_html}
<h2>来源账本</h2>
<div class="card tscroll"><table>
<tr><th>采样日期</th><th>报告</th><th>渠道</th><th>指标</th><th>原件</th></tr>
{led_rows}</table></div>
<h2>修正留痕</h2>{rev_html}
<a class="back" href="/dashboard/{person_id}">← 返回健康面板</a>
</body></html>"""
    return HTMLResponse(html)


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
