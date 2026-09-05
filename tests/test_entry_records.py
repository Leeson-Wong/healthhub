from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from conftest import SEED_DIR


def _client(tmp_path):
    settings = Settings(database_path=str(tmp_path / "e.db"), seed_dir=str(SEED_DIR),
                        scheduler_enabled=False, llm_api_key="")
    app = create_app(settings)
    return TestClient(app)


def test_entry_page_and_submit(tmp_path):
    with _client(tmp_path) as c:
        _run_entry(c)


def _run_entry(c):
    r = c.post("/persons", json={"external_ref": "father", "name": "王维首", "sex": "M",
                                 "birth_date": "1969-01-01"})
    pid = r.json()["id"]
    r = c.get("/entry")
    assert r.status_code == 200 and "健康数据录入" in r.text

    # form submit: BP dual values + symptom
    r = c.post("/entry/submit", data={
        "person_id": str(pid), "sampled_at": "2026-08-23T08:30",
        "bp_sys": "122", "bp_dia": "78", "hr": "76", "spo2": "97",
        "symptom_text": "晨起头晕片刻", "symptom_sev": "2", "client_id": "abc123",
    })
    assert r.status_code == 200 and "已保存" in r.text

    obs = c.get(f"/observations?person_id={pid}").json()
    codes = {o["canonical_code"] for o in obs}
    assert {"BP_SYS", "BP_DIA", "HR", "SPO2"} <= codes
    bp = next(o for o in obs if o["canonical_code"] == "BP_SYS")
    assert bp["value_num"] == 122 and bp["flag_computed"] == "NORMAL"
    # same timestamp & report
    bd = next(o for o in obs if o["canonical_code"] == "BP_DIA")
    assert bd["source_report_id"] == bp["source_report_id"]
    assert c.get("/symptoms?person_id=" + str(pid)).json()[0]["text"] == "晨起头晕片刻"

    # idempotent re-submit with same client_id
    r = c.post("/entry/submit", data={
        "person_id": str(pid), "sampled_at": "2026-08-23T08:30",
        "bp_sys": "122", "client_id": "abc123"})
    assert len(c.get(f"/observations?person_id={pid}&code=BP_SYS").json()) == 1

    # JSON API submit (shortcuts etc.)
    r = c.post("/entry/submit", json={
        "person_id": pid, "sampled_at": "2026-08-23T09:00", "client_id": "j1",
        "items": {"weight": "64.5", "glu": "5.8"}})
    assert r.status_code == 200 and r.json()["items"] == 2
    codes = {o["canonical_code"] for o in c.get(f"/observations?person_id={pid}").json()}
    assert "WEIGHT" in codes and "GLU_HOME" in codes

    # empty submit rejected
    r = c.post("/entry/submit", json={"person_id": pid, "client_id": "j2"})
    assert r.status_code == 400


def test_dashboard_long_term_sections(tmp_path):
    with _client(tmp_path) as c:
        pid = c.post("/persons", json={"external_ref": "f", "name": "测试", "sex": "M",
                                       "birth_date": "1969-01-01"}).json()["id"]
        for day in range(1, 6):
            c.post("/entry/submit", json={
                "person_id": pid, "sampled_at": f"2026-08-1{day}T08:00" if day < 10 else f"2026-08-{day}T08:00",
                "client_id": f"d{day}",
                "items": {"bp_sys": 118 + day * 2, "bp_dia": 76 + day, "hr": 70 + day}})
        c.post("/medications", json={"person_id": pid, "drug_name": "测试药", "frequency": "qd"})
        c.post("/symptoms", json={"person_id": pid, "severity": 2, "text": "偶尔头晕"})
        c.post("/encounters", json={"person_id": pid, "hospital": "市一院", "doctor_name": "张医生",
                                    "doctor_phone": "13800000000"})

        page = c.get(f"/dashboard/{pid}").text
        # 新结构：顶部体征快照（无大标题）+ 折叠式"生命体征趋势"
        assert "monrow" in page and "生命体征趋势" in page
        assert "window=30d" in page and "window=all" in page  # chips present
        assert "128/81" in page  # combined BP display (latest day: 118+2*5 / 76+5)
        assert "vital-card" in page and "class='c-band'" in page  # ref band via theme class
        assert "用药记录" in page and "测试药" in page and "在用" in page
        assert "症状记录" in page and "偶尔头晕" in page
        assert "就诊 · 住院档案" in page and "tel:13800000000" in page
        # UI 专项: sticky nav + collapsed overview + FAB
        assert "stickynav" in page and "#sec-vitals" in page and "#sec-labs" in page
        assert "<details class='ovbox'>" in page and "<details class='ovbox' open>" not in page
        assert "class='fab'" in page and "prefers-color-scheme: dark" in page
        assert "@media print" in page

        page7 = c.get(f"/dashboard/{pid}?window=all").text
        assert page7.count("vital-card") >= 3
        # 7d window: data spans 5 days, still shown; abnormal labs absent for this person
        assert "暂无生命体征记录" not in page7

        # insight prompt assembly includes long-term context
        from app.insights import build_prompt
        from app.models import Person as P
        sess = c.app.state.db.SessionLocal()
        person = sess.query(P).filter_by(id=pid).first()
        prompt = build_prompt(sess, person, None, None)
        sess.close()
        for marker in ("生命体征摘要", "当前用药", "近期症状", "就诊档案", "偶尔头晕", "张医生"):
            assert marker in prompt, marker


def test_medications_and_encounters(tmp_path):
    with _client(tmp_path) as c:
        pid = c.post("/persons", json={"external_ref": "f", "name": "测试", "sex": "M"}).json()["id"]

        r = c.post("/medications", json={"person_id": pid, "drug_name": "头孢曲松",
                                         "dose": "2g", "unit": "", "frequency": "qd",
                                         "started_at": "2026-08-19T00:00:00+00:00"})
        assert r.status_code == 201 and r.json()["active"] is True
        med_id = r.json()["id"]
        c.post("/medications", json={"person_id": pid, "drug_name": "氨溴索", "frequency": "tid"})

        meds = c.get(f"/medications?person_id={pid}").json()
        assert len(meds) == 2
        stopped = c.post(f"/medications/{med_id}/stop").json()
        assert stopped["active"] is False
        actives = c.get(f"/medications?person_id={pid}&active=true").json()
        assert [m["drug_name"] for m in actives] == ["氨溴索"]

        r = c.post("/encounters", json={
            "person_id": pid, "kind": "住院", "hospital": "淮南市第一人民医院",
            "department": "重症医学科", "doctor_name": "李艳平", "doctor_phone": "0554-6616390",
            "diagnosis": "急性胆管炎", "admitted_at": "2026-08-19T00:00:00+00:00"})
        assert r.status_code == 201
        encs = c.get(f"/encounters?person_id={pid}").json()
        assert encs[0]["doctor_name"] == "李艳平"
