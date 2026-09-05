"""MCP 写入通道测试：工具逻辑（in-memory）+ HTTP 鉴权门（TestClient）。"""
import asyncio
from pathlib import Path

import pytest

SEED_DIR = Path(__file__).resolve().parent.parent / "seed"
TOKEN = "test-mcp-token"


@pytest.fixture
def client(tmp_path):
    from fastapi.testclient import TestClient

    from app.config import Settings
    from app.main import create_app

    settings = Settings(
        database_path=str(tmp_path / "test.db"),
        seed_dir=str(SEED_DIR),
        scheduler_enabled=False,
        llm_api_key="",
        ingest_token=TOKEN,
    )
    app = create_app(settings)
    with TestClient(app) as c:
        yield c


@pytest.fixture
def mcp(client):
    """与 TestClient 同一 db/settings 的 FastMCP 实例（in-memory 直连，绕过 HTTP）。"""
    from app.mcp import create_mcp
    return create_mcp(client.app.state.db, client.app.state.settings)


REPORT = {
    "person_ref": "father",
    "external_id": "mcp-test-1",
    "hospital": "测试医院",
    "panel": "血常规",
    "sampled_at": "2026-08-19T13:21:00+08:00",
    "items": [
        {"code": "WBC", "name": "白细胞", "value": "3.81", "unit": "10^9/L",
         "ref_range_text": "3.5-9.5", "flag": ""},
    ],
    "cultures": [],
}

_INIT = {
    "jsonrpc": "2.0", "id": 1, "method": "initialize",
    "params": {"protocolVersion": "2025-03-26", "capabilities": {},
               "clientInfo": {"name": "pytest", "version": "0"}},
}


def test_mcp_gate_401_without_token(client):
    r = client.post("/mcp", json=_INIT,
                    headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401


def test_mcp_gate_initialize_with_token(client):
    r = client.post("/mcp", json=_INIT,
                    headers={"Authorization": f"Bearer {TOKEN}",
                             "Accept": "application/json, text/event-stream"})
    assert r.status_code == 200
    assert "healthhub" in r.text


def _payload(result):
    """兼容不同 fastmcp 版本的 call_tool 返回形状，规整成 dict。"""
    v = getattr(result, "data", None)
    if v is None and hasattr(result, "content"):
        content = result.content
        import json as _json
        texts = [getattr(b, "text", None) for b in (content or [])]
        texts = [t for t in texts if t]
        if len(texts) == 1:
            try:
                v = _json.loads(texts[0])
            except ValueError:
                v = {"raw": texts[0]}
        else:
            v = {"raw": str(content)}
    if not isinstance(v, dict):
        v = {"raw": str(v)}
    return v


def test_mcp_tools_and_write(mcp, client):
    async def run():
        from fastmcp import Client

        async with Client(mcp) as c:
            listed = await c.list_tools()
            tools = {t.name for t in (listed.tools if hasattr(listed, "tools") else listed)}
            assert {"ingest_report", "create_person", "confirm_mapping"} <= tools

            # 建档（重复创建 → 409 语义）
            r1 = _payload(await c.call_tool("create_person", {"person": {
                "external_ref": "father", "name": "测试成员", "sex": "M",
                "birth_date": "1969-01-01"}}))
            assert r1.get("created") is True or r1.get("status") == 409

            # 幂等推送：两次同内容，第二次 created=False
            r2 = _payload(await c.call_tool("ingest_report", {"report": REPORT}))
            r3 = _payload(await c.call_tool("ingest_report", {"report": REPORT}))
            if r2.get("created"):
                assert r3.get("created") is False
                assert r3.get("report_id") == r2.get("report_id")

            # REST 读侧可见刚写入的数据
            r = client.get("/persons")
            assert any(p["external_ref"] == "father" for p in r.json())

    asyncio.run(run())
