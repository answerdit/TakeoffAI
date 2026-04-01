#!/usr/bin/env bash
# TakeoffAI — Stop Script
# Usage: bash ~/TakeoffAI/stop.sh

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo ""
echo -e "${YELLOW}  TakeoffAI — Stopping...${NC}"
echo ""

cd "$INSTALL_DIR"

if ! docker info &>/dev/null; then
  echo "Docker is not running — nothing to stop."
  exit 0
fi

docker compose down

echo ""
echo -e "${GREEN}✅ TakeoffAI stopped.${NC}"
echo -e "   To start again: ${YELLOW}bash ~/TakeoffAI/start.sh${NC}"
echo ""
