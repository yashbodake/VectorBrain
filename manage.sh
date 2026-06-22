#!/usr/bin/env bash
# VectorBrain — single-script service manager.
#
# Usage:
#   ./manage.sh up      — start DB + backend (GPU) + frontend
#   ./manage.sh down    — stop backend + frontend (DB stays running, data safe)
#   ./manage.sh down-all — stop everything INCLUDING the database
#   ./manage.sh status   — show which services are up/down
#   ./manage.sh restart  — restart backend + frontend (DB untouched)
#   ./manage.sh logs     — tail backend + frontend logs (Ctrl-C to exit)
#
# The DB uses a Docker named volume — `down` does NOT delete data. Only
# `down-all` stops the container (data still persists in the named volume).
# Your data is never lost unless you run `docker compose down -v` manually.

set -euo pipefail

# Resolve the project root (this script lives at the repo root).
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$PROJECT_ROOT/backend"
FRONTEND_DIR="$PROJECT_ROOT/frontend"
BACKEND_LOG="/tmp/vectorbrain-backend.log"
FRONTEND_LOG="/tmp/vectorbrain-frontend.log"
PYTHON="python3"  # the global pyenv python3.13 with CUDA torch
HOST="127.0.0.1"
BACKEND_PORT=8000
FRONTEND_PORT=5173
DB_PORT=5433

# Colors for readability.
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; }
info() { echo -e "${YELLOW}→${NC} $1"; }

# --- Health checks ----------------------------------------------------------
db_healthy() {
  [ "$(docker inspect -f '{{.State.Health.Status}}' vectorbrain-db 2>/dev/null)" = "healthy" ]
}

backend_up() {
  curl -sS --max-time 2 "http://$HOST:$BACKEND_PORT/health" >/dev/null 2>&1
}

frontend_up() {
  curl -sS --max-time 2 "http://$HOST:$FRONTEND_PORT/" >/dev/null 2>&1
}

wait_for() {
  local name="$1" check_fn="$2" max="$3" i=0
  while [ $i -lt $max ]; do
    if $check_fn; then return 0; fi
    i=$((i + 1))
    sleep 2
  done
  return 1
}

# --- Start services ---------------------------------------------------------
start_db() {
  if db_healthy; then ok "DB already running"; return; fi
  info "Starting database (Docker)..."
  cd "$PROJECT_ROOT"
  docker compose up -d
  if ! wait_for "DB" db_healthy 30; then
    fail "DB failed to become healthy"
    return 1
  fi
  ok "DB healthy on port $DB_PORT"
}

start_backend() {
  if backend_up; then ok "Backend already running"; return; fi
  info "Starting backend (GPU Python)..."
  # Kill any stale uvicorn on the port.
  pkill -f "uvicorn app.main" 2>/dev/null || true
  sleep 1
  cd "$BACKEND_DIR"
  HF_HUB_OFFLINE=1 DEVICE=auto nohup "$PYTHON" -m uvicorn app.main:app \
    --host "$HOST" --port "$BACKEND_PORT" > "$BACKEND_LOG" 2>&1 &
  if ! wait_for "backend" backend_up 40; then
    fail "Backend failed to start — check $BACKEND_LOG"
    return 1
  fi
  ok "Backend on http://$HOST:$BACKEND_PORT (GPU: $(get_device))"
}

start_frontend() {
  if frontend_up; then ok "Frontend already running"; return; fi
  info "Starting frontend (Vite)..."
  pkill -f "vite.*$FRONTEND_PORT" 2>/dev/null || true
  sleep 1
  cd "$FRONTEND_DIR"
  nohup npm run dev -- --host "$HOST" --port "$FRONTEND_PORT" > "$FRONTEND_LOG" 2>&1 &
  if ! wait_for "frontend" frontend_up 25; then
    fail "Frontend failed to start — check $FRONTEND_LOG"
    return 1
  fi
  ok "Frontend on http://$HOST:$FRONTEND_PORT"
}

get_device() {
  "$PYTHON" -c "
from app.core.config import settings
print(settings.torch_device)
" 2>/dev/null || echo "unknown"
}

# --- Stop services ----------------------------------------------------------
stop_backend() {
  if backend_up; then info "Stopping backend..."; fi
  pkill -f "uvicorn app.main" 2>/dev/null || true
  ok "Backend stopped"
}

stop_frontend() {
  if frontend_up; then info "Stopping frontend..."; fi
  pkill -f "vite.*$FRONTEND_PORT" 2>/dev/null || true
  ok "Frontend stopped"
}

stop_db() {
  if ! docker ps --format '{{.Names}}' | grep -q vectorbrain-db; then
    ok "DB already stopped"; return
  fi
  info "Stopping database (data persists in Docker volume)..."
  cd "$PROJECT_ROOT"
  docker compose stop
  ok "DB stopped (data safe in volume)"
}

# --- Commands ---------------------------------------------------------------
cmd_up() {
  echo -e "\n${GREEN}=== Starting VectorBrain ===${NC}\n"
  start_db
  start_backend
  start_frontend
  echo -e "\n${GREEN}=== All services up ===${NC}"
  echo -e "  App:      http://$HOST:$FRONTEND_PORT"
  echo -e "  API docs: http://$HOST:$BACKEND_PORT/docs"
  echo -e "  DB:       localhost:$DB_PORT"
  echo ""
}

cmd_down() {
  echo -e "\n${RED}=== Stopping backend + frontend (DB stays running) ===${NC}\n"
  stop_backend
  stop_frontend
  echo -e "\n${GREEN}=== Done. DB still running (data safe). ===${NC}\n"
}

cmd_down_all() {
  echo -e "\n${RED}=== Stopping ALL services ===${NC}\n"
  stop_backend
  stop_frontend
  stop_db
  echo -e "\n${GREEN}=== All stopped. Data persists in Docker volume. ===${NC}\n"
}

cmd_restart() {
  echo -e "\n${YELLOW}=== Restarting backend + frontend ===${NC}\n"
  stop_backend
  stop_frontend
  sleep 2
  start_backend
  start_frontend
  echo -e "\n${GREEN}=== Restarted ===${NC}\n"
}

cmd_status() {
  echo -e "\n${GREEN}=== VectorBrain Status ===${NC}\n"
  if db_healthy; then ok "DB: healthy (port $DB_PORT)"; else fail "DB: down"; fi
  if backend_up; then ok "Backend: up (port $BACKEND_PORT)"; else fail "Backend: down"; fi
  if frontend_up; then ok "Frontend: up (port $FRONTEND_PORT)"; else fail "Frontend: down"; fi
  # Ready docs count
  if db_healthy; then
    DOCS=$(docker exec vectorbrain-db psql -U vectorbrain -d vectorbrain -t -c \
      "SELECT count(*) FROM documents WHERE status='ready';" 2>/dev/null | tr -d ' ')
    echo -e "  ${YELLOW}→${NC} $DOCS document(s) ready"
  fi
  echo ""
}

cmd_logs() {
  echo -e "${YELLOW}Tailing backend + frontend logs (Ctrl-C to exit)...${NC}\n"
  tail -f "$BACKEND_LOG" "$FRONTEND_LOG" 2>/dev/null || \
    echo "No logs found yet. Start services first with: ./manage.sh up"
}

# --- Main -------------------------------------------------------------------
case "${1:-}" in
  up)        cmd_up ;;
  down)      cmd_down ;;
  down-all)  cmd_down_all ;;
  restart)   cmd_restart ;;
  status)    cmd_status ;;
  logs)      cmd_logs ;;
  *)
    echo "VectorBrain service manager"
    echo ""
    echo "Usage: ./manage.sh <command>"
    echo ""
    echo "Commands:"
    echo "  up         Start DB + backend + frontend"
    echo "  down       Stop backend + frontend (DB stays, data safe)"
    echo "  down-all   Stop everything including DB (data still persists in volume)"
    echo "  restart    Restart backend + frontend (DB untouched)"
    echo "  status     Show which services are up/down"
    echo "  logs       Tail backend + frontend logs"
    echo ""
    exit 1
    ;;
esac
