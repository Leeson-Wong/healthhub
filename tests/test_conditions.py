from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import SEED_DIR


def _client(tmp_path):
    settings = Settings(database_path=str(tmp_path / "c.db"), seed_dir=str(SEED_DIR),
                        scheduler_enabled=False, llm_api_key="")
    return TestClient(create_app(settings))


def test_condition_lifecycle(tmp_path):
    with _client(tmp_path) as c:
        pid = c.post("/persons", json={"external_ref": "father", "name": "王维首"}).json()["id"]
        r = c.post("/conditions", json={
            "person_id": pid, "name": "急性胆管炎", "status": "active",
            "category": "主诊断", "onset_at": "2026-08-12",
            "note": "胆源性", "source": "病程"})
        assert r.status_code == 201
        cid = r.json()["id"]
        assert c.post("/conditions", json={"person_id": pid, "name": "急性胆管炎"}).status_code == 409
        assert c.post("/conditions", json={"person_id": pid, "name": "x", "status": "bad"}).status_code == 422

        # 生命周期：active → improving → resolved
        assert c.patch(f"/conditions/{cid}", json={"status": "improving"}).json()["status"] == "improving"
        r = c.patch(f"/conditions/{cid}", json={"status": "resolved", "resolved_at": "2026-09-01"})
        assert r.json()["resolved_at"] == "2026-09-01"

        lst = c.get(f"/conditions?person_id={pid}").json()
        assert len(lst) == 1 and lst[0]["status"] == "resolved"

        html = c.get(f"/dashboard/{pid}").text
        assert "诊断档案" in html and "急性胆管炎" in html and "已缓解" in html and "第" in html


def test_condition_chronic_and_suspected_render(tmp_path):
    with _client(tmp_path) as c:
        pid = c.post("/persons", json={"external_ref": "father", "name": "王维首"}).json()["id"]
        for name, st in (("胆结石病", "chronic"), ("应激性心肌损伤", "suspected")):
            assert c.post("/conditions", json={"person_id": pid, "name": name, "status": st}).status_code == 201
        html = c.get(f"/dashboard/{pid}").text
        assert "慢病/长期" in html and "疑似" in html
