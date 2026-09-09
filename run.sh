#!/usr/bin/env bash
# Chạy dev: backend (uvicorn, port 8001) + frontend (vite, port 5175).
# Bỏ qua phần nào đã đang chạy sẵn (không port-conflict).
# Dùng: ./run.sh  (Ctrl+C để dừng)

set -euo pipefail
cd "$(dirname "$0")"

BACKEND_PORT=8001
FRONTEND_PORT=5175
BACKEND_PID=""

is_listening() {
  netstat -ano 2>/dev/null | grep -q ":$1 .*LISTENING"
}

cleanup() {
  if [ -n "$BACKEND_PID" ]; then
    echo "Dừng backend (pid $BACKEND_PID)..."
    kill "$BACKEND_PID" 2>/dev/null || true
  fi
}
trap cleanup EXIT INT TERM

if is_listening "$BACKEND_PORT"; then
  echo "[skip] Backend đã chạy ở port $BACKEND_PORT."
else
  echo "[start] Backend (uvicorn --reload) ở port $BACKEND_PORT..."
  .venv/Scripts/python.exe -m uvicorn app.main:app --reload --port "$BACKEND_PORT" &
  BACKEND_PID=$!
fi

if is_listening "$FRONTEND_PORT"; then
  echo "[skip] Frontend đã chạy ở port $FRONTEND_PORT — mở http://localhost:$FRONTEND_PORT"
  if [ -n "$BACKEND_PID" ]; then
    echo "Backend vừa khởi động — nhấn Ctrl+C để dừng."
    wait "$BACKEND_PID"
  fi
else
  echo "[start] Frontend (vite) ở port $FRONTEND_PORT — mở http://localhost:$FRONTEND_PORT"
  cd frontend
  npm run dev
fi
