"""Argus 后端入口：REST /api + MCP /mcp 双入口，共用领域服务层。"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from .config import settings
from .jenkins_worker import serve

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .db import SessionLocal
from .errors import BusinessError, PermissionError as ActorPermissionError
from .api import router as api_router
from .mcp_server import mcp, mcp_app
from .seed import seed_if_empty


@asynccontextmanager
async def lifespan(_: FastAPI):
    session: Session = SessionLocal()
    try:
        seed_if_empty(session)
    finally:
        session.close()
    # MCP streamable HTTP 的 session manager 必须随宿主 lifespan 启停（挂载的子应用不会自启）。
    async with mcp.session_manager.run():
        stop = asyncio.Event()
        worker = asyncio.create_task(serve(stop)) if (
            settings().jenkins_worker_enabled and settings().jenkins_kind == "jenkins") else None
        try:
            yield
        finally:
            stop.set()
            if worker:
                await worker


app = FastAPI(title="Argus 操作系统测试平台", version="0.2.0",
              description="REST 与 MCP 双入口；业务能力由同一领域服务层提供。",
              lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(BusinessError)
def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    status = 403 if isinstance(exc, ActorPermissionError) else (
        401 if exc.code == "unknown_actor" else 400)
    return JSONResponse(status_code=status, content={"detail": exc.message, "code": exc.code})


app.include_router(api_router)
app.mount("/mcp", mcp_app())


@app.get("/healthz")
def healthz() -> dict:
    return {"status": "ok"}
