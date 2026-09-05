from app.csv_import import build_payloads

REPORT = {
    "person_ref": "father",
    "external_id": "test-1",
    "hospital": "淮南市第一人民医院",
    "panel": "血常规",
    "sampled_at": "2026-08-19T13:21:00+08:00",
    "items": [
        {"code": "WBC", "name": "白细胞", "value": "3.81", "unit": "10^9/L", "ref_range_text": "3.5-9.5", "flag": ""},
        {"code": "PCT", "name": "血小板压积", "value": "0.13", "unit": "%", "ref_range_text": "0.17-0.39", "flag": "↓"},
        {"code": "MYSTERY", "name": "神秘项目", "value": "42", "unit": None, "ref_range_text": None, "flag": None},
    ],
    "cultures": [{
        "specimen": "厌氧血培养",
        "sampled_at": "2026-08-19T17:01:00+08:00",
        "reported_at": "2026-08-20T10:00:00+08:00",
        "organism": "阪崎肠杆菌",
        "mdr_text": "CRO·多重耐药菌",
        "susceptibilities": [
            {"drug_code": "GEN", "drug_name": "庆大霉素", "result": "S", "mic_text": "MIC≤1"},
            {"drug_code": "TZP", "drug_name": "哌拉西林/他唑巴坦", "result": "R", "mic_text": "MIC≥128/4"},
        ],
    }],
}


def _setup_person(client):
    r = client.post("/persons", json={"external_ref": "father", "name": "王维首", "sex": "M", "birth_date": "1969-01-01"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json()["db"] is True


def test_ingest_idempotent_and_flags(client):
    person_id = _setup_person(client)
    r = client.post("/ingest/reports", json=REPORT)
    assert r.status_code == 200, r.text
    s = r.json()
    assert s["created"] is True and s["items_total"] == 3
    assert s["items_normalized"] == 2 and s["items_unresolved"] == 1
    assert s["cultures"] == 1

    # idempotent re-post: same external_id + same content
    r2 = client.post("/ingest/reports", json=REPORT)
    assert r2.status_code == 200 and r2.json()["created"] is False
    assert r2.json()["report_id"] == s["report_id"]

    # same external_id + different content -> 409
    bad = {**REPORT, "items": REPORT["items"][:1]}
    r3 = client.post("/ingest/reports", json=bad)
    assert r3.status_code == 409

    # observations landed with correct flags
    obs = client.get(f"/observations?person_id={person_id}&code=PCT_PLT").json()
    assert len(obs) == 1
    assert obs[0]["flag_computed"] == "LOW" and obs[0]["flag_source"] == "LOW"
    obs = client.get(f"/observations?person_id={person_id}&code=WBC").json()
    assert obs[0]["flag_computed"] == "NORMAL" and obs[0]["value_num"] == 3.81

    # unresolved queued
    unres = client.get("/mappings/unresolved").json()
    assert len(unres) == 1 and unres[0]["raw_code"] == "MYSTERY"


def test_mapping_confirm_and_reprocess(client):
    _setup_person(client)
    client.post("/ingest/reports", json=REPORT)
    unres = client.get("/mappings/unresolved").json()
    assert len(unres) == 1

    r = client.post("/mappings", json={"raw_code": "MYSTERY", "raw_name": "神秘项目", "canonical_code": "RDW_CV"})
    assert r.status_code == 200 and r.json()["unresolved_marked_resolved"] == 1

    r = client.post("/admin/reprocess")
    assert r.json()["resolved"] == 1

    obs = client.get("/observations?code=RDW_CV").json()
    assert len(obs) == 1 and obs[0]["raw_code"] == "MYSTERY"


def test_trends(client):
    _setup_person(client)
    client.post("/ingest/reports", json=REPORT)
    later = {**REPORT, "external_id": "test-2", "sampled_at": "2026-08-20T07:51:00+08:00",
             "items": [{"code": "WBC", "name": "白细胞", "value": "17.84", "unit": "10^9/L",
                        "ref_range_text": "3.5-9.5", "flag": "↑"}],
             "cultures": []}
    client.post("/ingest/reports", json=later)
    r = client.get("/trends/WBC")
    assert r.status_code == 200
    t = r.json()
    assert t["first"] == 3.81 and t["last"] == 17.84
    assert t["points"][-1]["flag"] == "HIGH"
    assert t["delta_pct"] == 368.24


def test_csv_import_end_to_end(client, tmp_path):
    """CSV import through the API service must yield zero unresolved lab rows."""
    payloads = build_payloads(r"F:\health\data\health_data_20260819-23.csv")
    lab_rows = total = 0
    person_id = _setup_person(client)
    for p in payloads:
        p["person_id"] = person_id
        r = client.post("/ingest/reports", json=p)
        assert r.status_code == 200, (r.status_code, r.text[:300], p["external_id"])
        s = r.json()
        total += s["items_total"]
        lab_rows += s["items_unresolved"]
    assert total == 292  # 307 CSV rows minus 15 micro rows that go to cultures
    assert lab_rows == 0
    # spot check: PCT split + culture + trend
    obs = client.get(f"/observations?person_id={person_id}&code=PCT_PROCAL").json()
    assert {o["value_num"] for o in obs} == {25.049, 63.921, 14.925, 5.775}
    obs = client.get(f"/observations?person_id={person_id}&code=PCT_PLT").json()
    assert len(obs) == 5  # 8/19-8/23 every day had a plateletcrit row
    t = client.get(f"/trends/CREA?person_id={person_id}").json()
    assert t["first"] == 117.9 and t["last"] == 91.0
    cultures = client.get(f"/cultures?person_id={person_id}").json()
    assert len(cultures) == 2
    blood = next(c for c in cultures if "厌氧" in c["specimen"])
    assert blood["organism"] == "阪崎肠杆菌" and "CRO" in blood["mdr_text"]
    assert len(blood["susceptibilities"]) == 8
    bile = next(c for c in cultures if "胆汁" in c["specimen"])
    assert bile["organism"] == "屎肠球菌" and len(bile["susceptibilities"]) == 4


def test_dashboard_page(client):
    person_id = _setup_person(client)
    for p in build_payloads(r"F:\health\data\health_data_20260819-23.csv"):
        p["person_id"] = person_id
        client.post("/ingest/reports", json=p)
    r = client.get(f"/dashboard/{person_id}")
    assert r.status_code == 200
    page = r.text
    assert "王维首" in page and "健康面板" in page
    assert "阪崎肠杆菌" in page and "屎肠球菌" in page
    assert "不构成医疗建议" in page
    assert "采样" in page
    # interactive curves: overview chart + sparklines carry per-point sample times
    assert "病情总览曲线" in page
    assert "main-pt" in page and "spark-pt" in page and "data-series" in page
    assert "getElementById('modal')" in page and "data-fu" in page
    # per-indicator background intro + rule-based analysis embedded in cards
    assert "背景介绍" in page and "单项分析" in page and "idetail" in page
    assert "降钙素原" in page  # background text of PCT_PROCAL present
    r = client.get("/dashboard/9999")
    assert r.status_code == 404
    r = client.get("/")
    assert r.status_code == 200 and f"/dashboard/{person_id}" in r.text
