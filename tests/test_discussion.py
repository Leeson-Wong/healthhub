from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import SEED_DIR


def _client(tmp_path):
    settings = Settings(database_path=str(tmp_path / "d.db"), seed_dir=str(SEED_DIR),
                        scheduler_enabled=False, llm_api_key="")
    return TestClient(create_app(settings))


def test_discussion_crud_and_dashboard(tmp_path):
    with _client(tmp_path) as c:
        pid = c.post("/persons", json={"external_ref": "father", "name": "王维首"}).json()["id"]

        r = c.post("/discussion", json={"person_id": pid, "author": "儿子",
                                        "category": "医生反馈", "content": "主任说引流液颜色转清了"})
        assert r.status_code == 201
        r = c.post("/discussion", json={"person_id": pid, "category": "观察", "content": "今天清醒时间变长"})
        assert r.status_code == 201 and r.json()["author"] == "家属"

        assert c.post("/discussion", json={"person_id": pid, "category": "无效类", "content": "x"}).status_code == 422
        assert c.post("/discussion", json={"person_id": pid, "category": "观察", "content": ""}).status_code == 422

        lst = c.get(f"/discussion?person_id={pid}").json()
        assert len(lst) == 2 and lst[0]["content"] == "今天清醒时间变长"  # 倒序

        html = c.get(f"/dashboard/{pid}").text
        assert "病情讨论区" in html and "医生反馈" in html and "清醒时间变长" in html
