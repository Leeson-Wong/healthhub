"""导入病程上下文（事件/背景档案/就诊）到 HealthHub。独立脚本，只依赖标准库。

用法（47 上直接跑，或在任何能访问 HealthHub 的机器）:
  python3 import_context.py --base http://127.0.0.1:8888 --json clinical_context_father.json

幂等：409（已存在）视为成功。
"""
import argparse
import json
import sys
import urllib.error
import urllib.request


def post(base: str, path: str, payload: dict) -> tuple[int, str]:
    req = urllib.request.Request(
        base.rstrip("/") + path,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def get_json(base: str, path: str):
    with urllib.request.urlopen(base.rstrip("/") + path, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8888")
    ap.add_argument("--json", required=True)
    args = ap.parse_args()

    data = json.load(open(args.json, encoding="utf-8"))
    ref = data["person_ref"]
    persons = get_json(args.base, "/persons")
    pid = next((p["id"] for p in persons if p["external_ref"] == ref), None)
    if pid is None:
        print(f"person_ref '{ref}' not found", file=sys.stderr)
        return 1

    ok = dup = fail = 0

    for ev in data.get("events", []):
        code, body = post(args.base, "/events", {"person_id": pid, **ev})
        if code in (200, 201):
            ok += 1
        elif code == 409:
            dup += 1
        else:
            fail += 1
            print(f"FAIL event {ev['title']}: {code} {body[:120]}", file=sys.stderr)
    for f in data.get("facts", []):
        code, body = post(args.base, "/events/facts", {"person_id": pid, **f})
        if code in (200, 201):
            ok += 1
        elif code == 409:
            dup += 1
        else:
            fail += 1
            print(f"FAIL fact {f['title']}: {code} {body[:120]}", file=sys.stderr)
    enc = data.get("encounter")
    if enc:
        existing = get_json(args.base, f"/encounters?person_id={pid}")
        already = any(e.get("admitted_at") == enc.get("admitted_at")
                      and e.get("hospital") == enc.get("hospital") for e in existing)
        if already:
            dup += 1
        else:
            code, body = post(args.base, "/encounters", {"person_id": pid, **enc})
            if code in (200, 201):
                ok += 1
            else:
                fail += 1
                print(f"FAIL encounter: {code} {body[:160]}", file=sys.stderr)

    print(f"done: ok={ok} dup={dup} fail={fail} (person_id={pid})")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
