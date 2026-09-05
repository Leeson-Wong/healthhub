"""meds_final JSON → HealthHub /medications + /events。幂等：409/已有则跳过。"""
import argparse
import json
import sys
import urllib.error
import urllib.request


def post(base, path, payload):
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def get_json(base, path):
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8888")
    ap.add_argument("--json", required=True)
    ap.add_argument("--person-ref", default="father")
    args = ap.parse_args()

    data = json.load(open(args.json, encoding="utf-8"))
    persons = get_json(args.base, "/persons")
    pid = next(p["id"] for p in persons if p["external_ref"] == args.person_ref)
    ok = dup = fail = 0

    existing_meds = get_json(args.base, f"/medications?person_id={pid}")
    existing_names = {m["drug_name"] for m in existing_meds}
    for m in data.get("medications", []):
        if m["drug_name"] in existing_names:
            dup += 1
            continue
        days = m["days"]
        first, last = min(days), max(days)
        ongoing = last >= "2026-08-23" and "仍在用" not in m.get("note", "")
        payload = {
            "person_id": pid, "drug_name": m["drug_name"], "route": m.get("route"),
            "started_at": f"{first}T00:00:00+08:00",
            "ended_at": None if last >= "2026-08-23" else f"{last}T23:59:59+08:00",
            "note": m.get("note", "") + " ｜ 每日用量: " +
                    "，".join(f"{d[5:]}×{q}" for d, q in sorted(days.items())),
        }
        code, body = post(args.base, "/medications", payload)
        if code in (200, 201):
            ok += 1
        else:
            fail += 1
            print(f"FAIL med {m['drug_name']}: {code} {body[:120]}", file=sys.stderr)

    existing_events = get_json(args.base, f"/events?person_id={pid}")
    ev_keys = {(e["occurred_at"], e["kind"], e["title"]) for e in existing_events}
    for ev in data.get("events", []):
        key = ("", ev["kind"], ev["title"])
        titles = {t for _, _, t in ev_keys}
        if ev["title"] in titles:
            dup += 1
            continue
        code, body = post(args.base, "/events", {"person_id": pid, **ev})
        if code in (200, 201):
            ok += 1
        else:
            fail += 1
            print(f"FAIL event {ev['title']}: {code} {body[:120]}", file=sys.stderr)

    print(f"done: ok={ok} dup={dup} fail={fail}")


if __name__ == "__main__":
    raise SystemExit(main())
