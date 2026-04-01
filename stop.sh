#!/usr/bin/env bash
# TakeoffAI — Stop
# Usage: bash ~/TakeoffAI/stop.sh
#
# Uses 'docker compose stop' (not down) to preserve the container filesystem
# so any AI-evolved prompt improvements survive between sessions.
# To fully reset: docker compose down  (wipes evolved prompts, keeps data volume)

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
NC='\033[0m'

INSTALL_DIR="$HOME/TakeoffAI"

echo ""
echo -e "${YELLOW}  TakeoffAI — Stopping...${NC}"
echo ""

if ! docker info &>/dev/null; then
  echo "Docker is not running — nothing to stop."
  exit 0
fi

cd "$INSTALL_DIR"
docker compose stop

echo ""
echo -e "${GREEN}✅ TakeoffAI stopped.${NC}"
echo -e "   To start again: ${YELLOW}bash ~/TakeoffAI/start.sh${NC}"
echo ""
