"""FastAPI service exposing configured robot tools over a small authenticated API."""
import argparse
import os
from pathlib import Path
import secrets

from .gateway import DEFAULT_CONFIG, ToolGateway


def create_app(config_path=DEFAULT_CONFIG, gateway=None):
    try:
        from fastapi import Depends, FastAPI, Header, HTTPException
        from pydantic import BaseModel, Field
    except ImportError as exc:
        raise RuntimeError("请安装机器人 API 依赖：pip install -e '.[robot-api]'") from exc

    service = gateway or ToolGateway(config_path)
    api_key = os.getenv("ROBOT_TOOL_API_KEY", "")

    class ExecuteRequest(BaseModel):
        arguments: dict = Field(default_factory=dict)

    def authorize(x_api_key: str | None = Header(default=None)):
        if api_key and (x_api_key is None or not secrets.compare_digest(x_api_key, api_key)):
            raise HTTPException(status_code=401, detail="invalid API key")

    app = FastAPI(title="Harness² Robot Tool Gateway", version="1.0.0")
    app.state.gateway = service

    @app.get("/health", dependencies=[Depends(authorize)])
    def health():
        return {"ok": True, "tools": len(service.tools), "busy": service.lock.locked()}

    @app.get("/tools", dependencies=[Depends(authorize)])
    def tools():
        return {"tools": service.describe()}

    @app.get("/tools/{name}/health", dependencies=[Depends(authorize)])
    def tool_health(name: str):
        try:
            result = service.health(name)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from None
        if result.get("ok") is not True:
            raise HTTPException(status_code=503, detail=result)
        return result

    @app.post("/tools/{name}/execute", dependencies=[Depends(authorize)])
    def execute(name: str, request: ExecuteRequest):
        try:
            result = service.execute(name, request.arguments)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if result.get("http_status") == 409:
            raise HTTPException(status_code=409, detail=result)
        return result

    @app.post("/cancel", dependencies=[Depends(authorize)])
    def cancel():
        return service.cancel()

    return app


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--host", default="127.0.0.1")
    result.add_argument("--port", type=int, default=8090)
    result.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    if not 1 <= args.port <= 65535:
        raise SystemExit("端口必须在1至65535之间")
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not os.getenv("ROBOT_TOOL_API_KEY"):
        raise SystemExit("监听非本机地址时必须设置 ROBOT_TOOL_API_KEY")
    try:
        import uvicorn
    except ImportError as exc:
        raise SystemExit("请安装机器人 API 依赖：pip install -e '.[robot-api]'") from exc
    uvicorn.run(create_app(args.config), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
