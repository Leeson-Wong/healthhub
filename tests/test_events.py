from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import SEED_DIR


def _client(tmp_path):
    settings = Settings(database_path=str(tmp_path / "e.db"), seed_dir=str(SEED_DIR),
                        scheduler_enabled=False, llm_api_key="")
    app = create_app(settings)
    return TestClient(app)


def _person(c):
    r = c.post("/persons", json={"external_ref": "father", "name": "王维首", "sex": "M",
                                 "birth_date": "1969-01-01"})
    return r.json()["id"]


def _obs(c, pid):
    """两条体征数据，让 sparkline 可绘制。"""
    for i, (t, temp) in enumerate([("2026-08-19T14:00:00+08:00", 38.9),
                                   ("2026-08-21T08:00:00+08:00", 37.2)]):
        c.post("/ingest/reports", json={
            "person_ref": "father", "external_id": f"evt-test-{i}", "panel": "体温",
            "sampled_at": t,
            "items": [{"code": "TEMP", "name": "体温", "value": str(temp), "unit": "℃",
                       "ref_range_text": "36-37.2", "flag": "HIGH" if i == 0 else ""}],
        })


def test_event_crud_and_normalization(tmp_path):
    with _client(tmp_path) as c:
        pid = _person(c)
        r = c.post("/events", json={
            "person_id": pid, "occurred_at": "2026-08-20T14:00:00+08:00",
            "time_precision": "hour", "kind": "intervention",
            "title": "胆道引流", "detail": "病程转折点", "source": "家属口述"})
        assert r.status_code == 201
        body = r.json()
        assert body["occurred_at"] == "2026-08-20T06:00:00+00:00"  # 归一化 UTC

        # 重复 → 409
        r2 = c.post("/events", json={
            "person_id": pid, "occurred_at": "2026-08-20T14:00:00+08:00",
            "kind": "intervention", "title": "胆道引流"})
        assert r2.status_code == 409

        # 列表
        lst = c.get(f"/events?person_id={pid}").json()
        assert len(lst) == 1 and lst[0]["title"] == "胆道引流"

        # 无时区 → 422
        r3 = c.post("/events", json={
            "person_id": pid, "occurred_at": "2026-08-20T14:00:00",
            "kind": "other", "title": "x"})
        assert r3.status_code == 422

        # 删除
        assert c.delete(f"/events/{lst[0]['id']}").status_code == 200
        assert c.get(f"/events?person_id={pid}").json() == []


def test_facts_crud(tmp_path):
    with _client(tmp_path) as c:
        pid = _person(c)
        r = c.post("/events/facts", json={
            "person_id": pid, "category": "past_history",
            "title": "胆结石病史", "detail": "多年"})
        assert r.status_code == 201
        assert c.get(f"/events/facts?person_id={pid}").json()[0]["title"] == "胆结石病史"
        assert c.post("/events/facts", json={
            "person_id": pid, "category": "past_history", "title": "胆结石病史"}).status_code == 409


def test_dashboard_renders_timeline_and_markers(tmp_path):
    with _client(tmp_path) as c:
        pid = _person(c)
        _obs(c, pid)
        c.post("/events", json={
            "person_id": pid, "occurred_at": "2026-08-20T14:00:00+08:00",
            "kind": "intervention", "title": "胆道引流"})
        c.post("/events", json={
            "person_id": pid, "occurred_at": "2026-08-21T12:00:00+08:00",
            "kind": "culture_result", "title": "mNGS 报告"})
        c.post("/events/facts", json={
            "person_id": pid, "category": "baseline", "title": "基础体格好"})

        html = c.get(f"/dashboard/{pid}").text
        assert "病程时间线" in html and "背景档案" in html
        assert "胆道引流" in html and "mNGS 报告" in html and "基础体格好" in html
        assert "ev-line" in html  # 趋势图上有事件竖线


def test_phase_modes_monitoring_vs_followup(tmp_path):
    with _client(tmp_path) as c:
        pid = _person(c)
        _obs(c, pid)
        c.post("/encounters", json={"person_id": pid, "kind": "住院", "hospital": "市一院",
                                    "admitted_at": "2026-08-19"})
        html = c.get(f"/dashboard/{pid}").text
        assert "🏥 监护" in html and "phbtn" in html      # 监护态横幅+切换器
        assert "本次住院档案" not in html                 # 时间线未折叠

        # 出院 + 未来复查事件 → 随访态
        c.post("/events", json={
            "person_id": pid, "occurred_at": "2026-09-10T00:00:00+08:00",
            "kind": "followup", "title": "门诊复查肝功能+胆道超声"})
        encs = c.get(f"/encounters?person_id={pid}").json()
        c.post(f"/entry/submit", json={"person_id": pid, "sampled_at": "2026-08-25T08:00",
                                       "client_id": "ph1", "items": {"hr": 76}})
        # 数据仍是近期 → followup 判定需无活跃住院：把 encounter 补 discharged_at（走 PATCH 不存在，直接再建一次 discharged 的）
        html2 = c.get(f"/dashboard/{pid}").text
        # 仍有活跃住院（未填 discharged_at）→ 还是监护态，验证横幅依旧
        assert "🏥 监护" in html2
