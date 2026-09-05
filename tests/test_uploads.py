import io

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import SEED_DIR

JPEG_1PX = (b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\x09\x09\x08\x0a\x0c\x14"
            b"\x0d\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c\x1c $.\x27 "
            b",#\x1c\x1c(7),01444\x1f\x27=9=82<.342\xff\xc0\x00\x0b\x08\x00\x01\x00\x01\x01\x01"
            b"\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\x09\x0a\x0b\xff\xc4\x00\xb5\x10\x00\x02"
            b"\x01\x03\x03\x02\x04\x03\x05\x05\x04\x04\x00\x00\x01}\x01\x02\x03\x00\x04\x11\x05\x12"
            b"\xff\xda\x00\x08\x01\x01\x00\x00?\x00T\xdf\xdb\xa2\x8a\xff\xd9")


def _client(tmp_path):
    settings = Settings(database_path=str(tmp_path / "u.db"), seed_dir=str(SEED_DIR),
                        scheduler_enabled=False, llm_api_key="")
    return TestClient(create_app(settings))


def _person(c):
    return c.post("/persons", json={"external_ref": "father", "name": "王维首"}).json()["id"]


def test_upload_to_ingest_to_provenance_chain(tmp_path):
    with _client(tmp_path) as c:
        pid = _person(c)

        # 1. 家属上传两张"照片"
        r = c.post("/uploads",
                   data={"person_id": str(pid), "note": "今晨血气", "submitted_by": "姐姐"},
                   files=[("files", ("a.jpg", io.BytesIO(JPEG_1PX), "image/jpeg")),
                          ("files", ("b.jpg", io.BytesIO(JPEG_1PX), "image/jpeg"))])
        assert r.status_code == 201, r.text
        uid = r.json()["id"]
        assert r.json()["queued"] == 2

        # 2. 队列可见（pending）
        lst = c.get(f"/uploads?person_id={pid}").json()
        assert lst[0]["status"] == "pending" and lst[0]["files"] == 2

        # 3. agent 认领
        claim = c.post(f"/uploads/{uid}/claim")
        assert claim.status_code == 200 and len(claim.json()["files"]) == 2

        # 4. 一次调用：结构化入库 + 原件挂载 + 闭环
        r = c.post(f"/uploads/{uid}/ingest", json={
            "person_id": pid, "external_id": "up-test-001", "panel": "血气",
            "sampled_at": "2026-08-25T08:00:00+08:00",
            "items": [{"code": "LAC", "name": "乳酸", "value": "1.2", "unit": "mmol/L"}]})
        assert r.status_code == 200, r.text
        rid = r.json()["ingest"]["report_id"]
        assert r.json()["ingest"]["created"] is True
        assert len(r.json()["attached_files"]) == 2

        # 5. 队列 done + ledger 显示渠道/原件
        assert c.get(f"/uploads?person_id={pid}").json()[0]["status"] == "done"
        ledger = c.get(f"/sources/list?person_id={pid}").json()
        assert ledger[0]["provenance"] == "upload_ocr" and ledger[0]["files"] == 2

        # 6. 原件可取回
        files = c.get(f"/reports/{rid}/files").json()
        resp = c.get(f"/files/{files[0]['id']}")
        assert resp.status_code == 200 and resp.headers["content-type"].startswith("image/")

        # 7. dashboard 入口卡 + 独立数据管理台
        html = c.get(f"/dashboard/{pid}").text
        assert "数据管理台" in html and "srccard" in html and "/sources?person_id" in html
        assert "srcchip" in html  # 详情弹窗的来源列
        src_page = c.get(f"/sources?person_id={pid}").text
        assert "来源账本" in src_page and "📷拍照" in src_page and "修正留痕" in src_page

        # 8. 重复 ingest → 409（幂等保护）
        assert c.post(f"/uploads/{uid}/ingest", json={
            "person_id": pid, "external_id": "up-test-001", "panel": "x",
            "items": []}).status_code == 409


def test_observation_fix_leaves_revision(tmp_path):
    with _client(tmp_path) as c:
        pid = _person(c)
        c.post("/ingest/reports", json={
            "person_ref": "father", "external_id": "fix-001",
            "sampled_at": "2026-08-25T08:00:00+08:00",
            "items": [{"code": "PLT", "name": "血小板", "value": "105"}]})
        obs = c.get(f"/observations?person_id={pid}").json()
        oid = obs[0]["id"]
        r = c.patch(f"/observations/{oid}", json={"raw_value": "95", "reason": "家属核对原单", "edited_by": "儿子"})
        assert r.status_code == 200 and r.json()["value_num"] == 95.0
        revs = c.get(f"/observations/{oid}/revisions").json()
        assert revs[0]["old"] == "105" and revs[0]["new"] == "95" and revs[0]["by"] == "儿子"
