#!/usr/bin/env bash
# 一键启动：自动创建虚拟环境、安装依赖并运行服务
set -e
cd "$(dirname "$0")"
PORT="${PORT:-8000}"
HOST="${HOST:-0.0.0.0}"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
if [ ! -d .venv ]; then
  python3 -m venv .venv
fi
PY=.venv/bin/python
# 依赖已齐全时跳过安装（离线环境也能启动）
if ! "$PY" -c "import fastapi, uvicorn, httpx, multipart" 2>/dev/null; then
  "$PY" -m ensurepip --upgrade >/dev/null 2>&1 || true
  "$PY" -m pip install -q -r requirements.txt
fi
echo "AI 字幕工作台已启动：浏览器访问 http://localhost:${PORT}  （监听 ${HOST}:${PORT}）"
exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT" ${UVICORN_ARGS:-}
