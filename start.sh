#!/usr/bin/env bash
# TakeoffAI — Start
# Usage: bash ~/TakeoffAI/start.sh

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

INSTALL_DIR="$HOME/TakeoffAI"

echo ""
echo -e "${YELLOW}  TakeoffAI — Starting up...${NC}"
echo ""

# ── Start Docker Desktop if not running ───────────────────────────────────────
if ! docker info &>/dev/null; then
  echo -e "${CYAN}Starting Docker Desktop...${NC}"
  open -a Docker
  echo -n "Waiting for Docker"
  for i in {1..30}; do
    sleep 2
    if docker info &>/dev/null; then
      echo ""
      break
    fi
    echo -n "."
    if [ "$i" -eq 30 ]; then
      echo ""
      echo -e "${RED}Docker Desktop did not start in time.${NC}"
      echo "Open Docker Desktop manually, wait for the menu bar icon, then re-run."
      exit 1
    fi
  done
fi

echo -e "${GREEN}✓ Docker Desktop is running${NC}"

# ── Start containers ──────────────────────────────────────────────────────────
cd "$INSTALL_DIR"
docker compose up -d

# ── Wait for health ───────────────────────────────────────────────────────────
echo -n "Waiting for backend"
for i in {1..40}; do
  if curl -sf http://localhost:8000/api/health >/dev/null 2>&1; then
    echo ""
    break
  fi
  echo -n "."
  sleep 2
  if [ "$i" -eq 40 ]; then
    echo ""
    echo -e "${RED}Backend did not respond. Check logs: docker compose logs backend${NC}"
    exit 1
  fi
done

echo ""
echo -e "${GREEN}✅ TakeoffAI is running!${NC}"
echo -e "   ${CYAN}http://localhost:3000${NC}"
echo ""
open http://localhost:3000
