"""数据溯源 + 自助上传 API。

- POST /uploads           家人手机拍照上传（multipart，多文件）→ pending 队列
- GET  /uploads           队列列表（面板"待处理 N 张"数据源）
- POST /uploads/{id}/claim  agent 认领（pending → processing）
- POST /uploads/{id}/done   agent 完成（关联 source_report_id）
- POST /reports/{rid}/files  给已入库报告补挂原件
- GET  /files/{fid}          取原件（img/pdf 直出）
- GET  /reports              来源账本（全部 source_report + 渠道 + 附件数）
- PATCH /observations/{id}   指标值修正（写 observation_revisions 留痕）
"""

import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy.orm import Session

from app.deps import get_db, verify_token
from app.models import (Observation, ObservationRevision, PendingUpload, Person,
                        SourceFile, SourceReport, utcnow_iso)
from app.config import get_settings

router = APIRouter(tags=["provenance"])

ALLOWED_MIME = {"image/jpeg", "image/png", "application/pdf"}
MAX_FILE_BYTES = 10 * 1024 * 1024
SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


def _files_dir(request: Request) -> Path:
    # 从 app.state 取库路径（跟随部署环境），不用全局 get_settings
    db_path = Path(request.app.state.db.settings.database_path)
    p = db_path.parent / "files"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _save_upload(raw: bytes, orig_name: str, subdir: str, request) -> tuple[str, str, int, str]:
    ext = Path(orig_name).suffix.lower() or ".jpg"
    if ext not in (".jpg", ".jpeg", ".png", ".pdf"):
        ext = ".jpg"
    mime = "application/pdf" if ext == ".pdf" else f"image/{'png' if ext == '.png' else 'jpeg'}"
    safe = SAFE_NAME.sub("_", Path(orig_name).stem)[:60] or "photo"
    name = f"{subdir}_{safe}{ext}"
    path = _files_dir(request) / name
    i = 1
    while path.exists():
        path = _files_dir(request) / f"{name[:-len(ext)]}_{i}{ext}"
        i += 1
    path.write_bytes(raw)
    return path.name, mime, len(raw), hashlib.sha256(raw).hexdigest()


@router.post("/uploads", status_code=201)
async def create_upload(person_id: int = Form(...), note: str = Form(""),
                        submitted_by: str = Form("家属"),
                        files: list[UploadFile] = File(...), db: Session = Depends(get_db), request: Request = None):
    if not files:
        raise HTTPException(422, "至少一张照片")
    if db.get(Person, person_id) is None:
        raise HTTPException(404, "person not found")
    pu = PendingUpload(person_id=person_id, note=note or None, submitted_by=submitted_by or "家属")
    db.add(pu)
    db.flush()
    saved = []
    for f in files:
        raw = await f.read()
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{f.filename} 超过 10MB")
        saved.append(_save_upload(raw, f.filename or "photo", f"u{pu.id}", request))
    db.commit()
    return {"id": pu.id, "status": pu.status, "files": [s[0] for s in saved],
            "queued": len(saved)}


UPLOAD_PAGE = """<!doctype html><html lang="zh"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>上传报告单 · HealthHub</title><style>
:root{color-scheme:light dark}
*{box-sizing:border-box}
body{font-family:system-ui,-apple-system,sans-serif;margin:0;background:#f5f5f4;color:#1c1917;padding:16px;max-width:520px;margin:0 auto}
@media(prefers-color-scheme:dark){body{background:#0c0a09;color:#e7e5e4}}
h1{font-size:19px;margin:8px 0 2px}
.sub{font-size:13px;color:#78716c;margin-bottom:14px}
.card{background:#fff;border:1px solid #e7e5e4;border-radius:12px;padding:14px;margin-bottom:12px}
@media(prefers-color-scheme:dark){.card{background:#1c1917;border-color:#292524}}
label{font-size:13px;color:#78716c;display:block;margin:10px 0 4px}
select,input[type=text]{width:100%;padding:10px;border:1px solid #e7e5e4;border-radius:8px;background:transparent;color:inherit;font-size:15px}
@media(prefers-color-scheme:dark){select,input{border-color:#44403c}}
input[type=file]{width:100%;margin-top:4px;font-size:14px}
#previews{display:flex;flex-wrap:wrap;gap:8px;margin-top:8px}
#previews img{width:72px;height:72px;object-fit:cover;border-radius:8px;border:1px solid #e7e5e4}
button{width:100%;padding:14px;margin-top:14px;border:none;border-radius:10px;background:#2563eb;color:#fff;font-size:16px;font-weight:600}
button:disabled{opacity:.6}
#result{margin-top:12px;font-size:14px;text-align:center;white-space:pre-line}
a.back{display:block;text-align:center;margin-top:16px;color:#2563eb;font-size:14px;text-decoration:none}
</style></head><body>
<h1>📥 上传检验单</h1>
<div class="sub">拍照上传 → 排队 → 核对后入库（面板数据点可回看原件）</div>
<div class="card">
<label>成员</label><select id="person"></select>
<label>备注（可选，如"今晨血气+血常规"）</label><input type="text" id="note" maxlength="100">
<label>照片（可多选，纸质单直接拍）</label><input type="file" id="files" accept="image/*" capture="environment" multiple>
<div id="previews"></div>
<button id="send">上传</button>
<div id="result"></div>
</div>
<a class="back" href="/" onclick="history.back();return false">← 返回</a>
<script>
fetch('/persons').then(r=>r.json()).then(function(ps){
var s=document.getElementById('person');
ps.forEach(function(p){var o=document.createElement('option');o.value=p.id;o.textContent=p.name;s.appendChild(o);});
var m=new URLSearchParams(location.search).get('person_id');
if(m){s.value=m;}
});
document.getElementById('files').addEventListener('change',function(){
var pv=document.getElementById('previews');pv.innerHTML='';
for(var i=0;i<this.files.length;i++){var img=document.createElement('img');img.src=URL.createObjectURL(this.files[i]);pv.appendChild(img);}
});
document.getElementById('send').addEventListener('click',function(){
var f=document.getElementById('files').files;
if(!f.length){document.getElementById('result').textContent='先选照片';return;}
var b=document.getElementById('send');b.disabled=true;b.textContent='上传中…';
var fd=new FormData();
fd.append('person_id',document.getElementById('person').value);
fd.append('note',document.getElementById('note').value);
fd.append('submitted_by','家属');
for(var i=0;i<f.length;i++){fd.append('files',f[i],f[i].name||('photo'+i+'.jpg'));}
fetch('/uploads',{method:'POST',body:fd}).then(function(r){if(!r.ok){throw new Error('HTTP '+r.status);}return r.json();})
.then(function(d){document.getElementById('result').textContent='✓ 已进入队列（#'+d.id+'，'+d.queued+' 张）\\n核对入库后自动出现在面板';document.getElementById('previews').innerHTML='';document.getElementById('files').value='';})
.catch(function(e){document.getElementById('result').textContent='上传失败：'+e.message;})
.finally(function(){b.disabled=false;b.textContent='上传';});
});
</script></body></html>"""


@router.get("/upload", response_class=HTMLResponse)
def upload_page():
    return UPLOAD_PAGE


@router.get("/uploads")
def list_uploads(person_id: int | None = None, db: Session = Depends(get_db), request: Request = None):
    q = db.query(PendingUpload)
    if person_id:
        q = q.filter_by(person_id=person_id)
    rows = q.order_by(PendingUpload.id.desc()).limit(100).all()
    files_dir = _files_dir(request)
    out = []
    for u in rows:
        n_files = len(list(files_dir.glob(f"u{u.id}_*")))
        out.append({"id": u.id, "person_id": u.person_id, "status": u.status,
                    "note": u.note, "submitted_by": u.submitted_by,
                    "files": n_files, "source_report_id": u.source_report_id,
                    "error": u.error, "created_at": u.created_at})
    return out


@router.post("/uploads/{uid}/claim", dependencies=[Depends(verify_token)])
def claim_upload(uid: int, db: Session = Depends(get_db), request: Request = None):
    u = db.get(PendingUpload, uid)
    if u is None:
        raise HTTPException(404, "upload not found")
    if u.status not in ("pending", "error"):
        raise HTTPException(409, f"status={u.status} 不可认领")
    u.status = "processing"
    db.commit()
    return {"id": uid, "status": "processing", "files": [p.name for p in sorted(_files_dir(request).glob(f"u{uid}_*"))]}


@router.post("/uploads/{uid}/ingest", dependencies=[Depends(verify_token)])
def ingest_upload(uid: int, body: dict, db: Session = Depends(get_db), request: Request = None):
    """agent 终审后一次调用：结构化入库（渠道=upload_ocr）+ 队列原件自动挂到该报告 + 队列闭环。

    body = ReportIn 同构（person_ref/person_id, external_id, panel, sampled_at, items[...]）
    """
    from app.ingest import IngestError, ingest_report

    u = db.get(PendingUpload, uid)
    if u is None:
        raise HTTPException(404, "upload not found")
    if u.status == "done":
        raise HTTPException(409, f"already done (report {u.source_report_id})")
    payload = dict(body)
    payload["provenance"] = "upload_ocr"
    try:
        result = ingest_report(db, payload)
    except IngestError as e:
        u.status = "error"
        u.error = str(e)
        db.commit()
        raise HTTPException(e.status, str(e))
    rid = result.get("report_id")
    # 挂原件：u{uid}_* → r{rid}_* + source_files 行
    attached = []
    for p in sorted(_files_dir(request).glob(f"u{uid}_*")):
        ext = p.suffix.lower()
        mime = "application/pdf" if ext == ".pdf" else ("image/png" if ext == ".png" else "image/jpeg")
        new_name = f"r{rid}_{p.name.split('_', 2)[-1]}"
        new_path = p.with_name(new_name)
        p.rename(new_path)
        sf = SourceFile(source_report_id=rid, filename=new_name, mime=mime,
                        size_bytes=new_path.stat().st_size,
                        sha256=hashlib.sha256(new_path.read_bytes()).hexdigest())
        db.add(sf)
        attached.append(new_name)
    u.status = "done"
    u.source_report_id = rid
    u.processed_at = utcnow_iso()
    db.commit()
    return {"ingest": result, "attached_files": attached}


@router.post("/uploads/{uid}/done", dependencies=[Depends(verify_token)])
def done_upload(uid: int, source_report_id: int, db: Session = Depends(get_db)):
    u = db.get(PendingUpload, uid)
    if u is None:
        raise HTTPException(404, "upload not found")
    sr = db.get(SourceReport, source_report_id)
    if sr is None:
        raise HTTPException(404, "source_report not found")
    u.status = "done"
    u.source_report_id = source_report_id
    u.processed_at = utcnow_iso()
    db.commit()
    return {"id": uid, "status": "done"}


@router.post("/reports/{rid}/files", status_code=201, dependencies=[Depends(verify_token)])
async def attach_files(rid: int, files: list[UploadFile] = File(...),
                       db: Session = Depends(get_db), request: Request = None):
    sr = db.get(SourceReport, rid)
    if sr is None:
        raise HTTPException(404, "source_report not found")
    out = []
    for f in files:
        raw = await f.read()
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(413, f"{f.filename} 超过 10MB")
        name, mime, size, sha = _save_upload(raw, f.filename or "photo", f"r{rid}", request)
        sf = SourceFile(source_report_id=rid, filename=name, mime=mime,
                        size_bytes=size, sha256=sha)
        db.add(sf)
        out.append(sf.filename)
    db.commit()
    return {"report_id": rid, "attached": out}


@router.get("/reports/{rid}/files")
def report_files(rid: int, db: Session = Depends(get_db)):
    rows = db.query(SourceFile).filter_by(source_report_id=rid).order_by(SourceFile.id).all()
    return [{"id": f.id, "filename": f.filename, "mime": f.mime, "size_bytes": f.size_bytes}
            for f in rows]


@router.get("/files/{fid}")
def get_file(fid: int, db: Session = Depends(get_db), request: Request = None):
    sf = db.get(SourceFile, fid)
    if sf is None:
        raise HTTPException(404, "file not found")
    path = _files_dir(request) / sf.filename
    if not path.exists():
        raise HTTPException(404, "file missing on disk")
    return FileResponse(path, media_type=sf.mime, filename=sf.filename)


@router.get("/sources/list")
def sources_ledger(person_id: int, db: Session = Depends(get_db)):
    rows = (db.query(SourceReport).filter_by(person_id=person_id)
            .order_by(SourceReport.sampled_at.desc(), SourceReport.id.desc()).all())
    out = []
    for r in rows:
        n_files = db.query(SourceFile).filter_by(source_report_id=r.id).count()
        n_obs = db.query(Observation).filter_by(source_report_id=r.id).count()
        out.append({"id": r.id, "external_id": r.external_id, "panel": r.panel,
                    "hospital": r.hospital, "sampled_at": r.sampled_at,
                    "reported_at": r.reported_at, "provenance": r.provenance or "api",
                    "observations": n_obs, "files": n_files, "created_at": r.created_at})
    return out


@router.patch("/observations/{oid}", dependencies=[Depends(verify_token)])
def fix_observation(oid: int, body: dict, db: Session = Depends(get_db)):
    """body: {raw_value, value_num?, reason?, edited_by?}"""
    o = db.get(Observation, oid)
    if o is None:
        raise HTTPException(404, "observation not found")
    new_raw = str(body.get("raw_value", "")).strip()
    if not new_raw:
        raise HTTPException(422, "raw_value required")
    try:
        new_num = float(new_raw) if body.get("value_num") is None else float(body["value_num"])
    except ValueError:
        new_num = None
    rev = ObservationRevision(observation_id=oid, old_raw_value=o.raw_value,
                              new_raw_value=new_raw, old_value_num=o.value_num,
                              new_value_num=new_num, reason=body.get("reason"),
                              edited_by=body.get("edited_by"))
    o.raw_value = new_raw
    o.value_num = new_num
    if new_num is not None:
        # 重算 flag_computed 会牵连参考范围逻辑 —— 保留原 flag，修正场景多为数值誊错
        pass
    db.add(rev)
    db.commit()
    return {"id": oid, "raw_value": o.raw_value, "value_num": o.value_num,
            "revised": True}


@router.get("/observations/{oid}/revisions")
def obs_revisions(oid: int, db: Session = Depends(get_db)):
    rows = (db.query(ObservationRevision).filter_by(observation_id=oid)
            .order_by(ObservationRevision.id.desc()).all())
    return [{"old": r.old_raw_value, "new": r.new_raw_value, "reason": r.reason,
             "by": r.edited_by, "at": r.created_at} for r in rows]
