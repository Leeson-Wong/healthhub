"""MCP 写入通道 — Alterego 挂载的官方写入口，语义与 REST 完全一致。

挂载在 /mcp（Streamable HTTP），由 INGEST_TOKEN Bearer 守门。
只暴露写操作；读侧（面板/查询）不重复暴露，仍走 REST。
"""
from __future__ import annotations

import logging

from fastapi import HTTPException
from fastmcp import FastMCP
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import Database
from app.ingest import IngestError
from app.ingest import ingest_report as _ingest_report
from app.models import Person
from app.schemas import MappingIn, PersonIn, ReportIn

log = logging.getLogger("healthhub.mcp")


def _run(fn, session: Session, *args) -> dict:
    """统一把 REST/路由层异常转换成工具返回值，不让 MCP 调用方看到堆栈。"""
    try:
        result = fn(*args, db=session)
    except HTTPException as e:
        return {"error": str(e.detail), "status": e.status_code}
    except IngestError as e:
        return {"error": str(e), "status": e.status}
    if isinstance(result, dict):
        return result
    if result is None:
        return {"ok": True}
    return {"ok": True, "detail": str(result)}


def create_mcp(db: Database, settings: Settings) -> FastMCP:
    mcp = FastMCP(
        "healthhub",
        instructions=(
            "HealthHub 写入通道。工具：ingest_report（推送检验报告，幂等）、"
            "create_person（建档）、confirm_mapping（人工确认术语别名）。"
            "语义与 REST API 一致；错误以 {'error','status'} 返回而非抛异常。"
        ),
    )

    @mcp.tool
    def ingest_report(report: ReportIn) -> dict:
        """推送一份检验报告。幂等：同 external_id 同内容返回既有报告(created=false)、
        不同内容 status=409。items 为检验指标；cultures 为培养/药敏。字段同 REST 契约。"""
        with db.SessionLocal() as session:
            try:
                return _ingest_report(session, report.model_dump())
            except IngestError as e:
                return {"error": str(e), "status": e.status}

    @mcp.tool
    def create_person(person: PersonIn) -> dict:
        """创建成员档案。external_ref 重复返回 status=409。"""
        with db.SessionLocal() as session:
            if session.query(Person).filter_by(external_ref=person.external_ref).first():
                return {"error": f"person '{person.external_ref}' already exists", "status": 409}
            p = Person(external_ref=person.external_ref, name=person.name, sex=person.sex,
                       birth_date=person.birth_date, note=person.note)
            session.add(p)
            session.commit()
            return {"person_id": p.id, "external_ref": p.external_ref, "name": p.name, "created": True}

    @mcp.tool
    def confirm_mapping(mapping: MappingIn) -> dict:
        """人工确认术语别名（raw_code/raw_name → canonical_code），并自动解决匹配的
        待处理项。canonical_code 必须已存在于标准术语表。"""
        from app.routers.mappings import add_mapping
        with db.SessionLocal() as session:
            return _run(add_mapping, session, mapping)

    return mcp


def bearer_gate(asgi_app, settings: Settings):
    """ASGI 中间件：/mcp 全部工具均为写操作，统一要求 Bearer INGEST_TOKEN。"""
    async def _guarded(scope, receive, send):
        if scope["type"] == "http" and settings.ingest_token:
            auth = ""
            for k, v in scope.get("headers") or []:
                if k.decode("latin-1").lower() == "authorization":
                    auth = v.decode("latin-1")
                    break
            if auth != f"Bearer {settings.ingest_token}":
                from fastapi.responses import JSONResponse
                await JSONResponse({"detail": "unauthorized"}, status_code=401)(scope, receive, send)
                return
        await asgi_app(scope, receive, send)
    return _guarded
