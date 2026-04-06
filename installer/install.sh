#!/usr/bin/env bash
# TakeoffAI Installer — by answerd.it
# Run this from the USB drive. Loads TakeoffAI into Docker Desktop.
# After install the USB is no longer needed.

set -e

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_SOURCE="$(cd "$SCRIPT_DIR/.." && pwd)"
INSTALL_DIR="$HOME/TakeoffAI"

echo ""
echo -e "${YELLOW}  ████████╗ █████╗ ██╗  ██╗███████╗ ██████╗ ███████╗███████╗ █████╗ ██╗${NC}"
echo -e "${YELLOW}     ██╔══╝██╔══██╗██║ ██╔╝██╔════╝██╔═══██╗██╔════╝██╔════╝██╔══██╗██║${NC}"
echo -e "${YELLOW}     ██║   ███████║█████╔╝ █████╗  ██║   ██║█████╗  █████╗  ███████║██║${NC}"
echo -e "${YELLOW}     ██║   ██╔══██║██╔═██╗ ██╔══╝  ██║   ██║██╔══╝  ██╔══╝  ██╔══██║██║${NC}"
echo -e "${YELLOW}     ██║   ██║  ██║██║  ██╗███████╗╚██████╔╝██║     ██║     ██║  ██║██║${NC}"
echo -e "${YELLOW}     ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═╝     ╚═╝     ╚═╝  ╚═╝╚═╝${NC}"
echo -e "                         a product by ${YELLOW}answerd.it${NC}"
echo ""

# ── 1. macOS check ────────────────────────────────────────────────────────────
if [ "$(uname)" != "Darwin" ]; then
  echo -e "${RED}ERROR: TakeoffAI requires macOS.${NC}"
  exit 1
fi

# ── 2. Docker Desktop check / install ────────────────────────────────────────
if ! command -v docker &>/dev/null; then
  echo -e "${RED}Docker Desktop is not installed.${NC}"
  INSTALLERS_DIR="$(dirname "$APP_SOURCE")/installers"
  DMG="$(find "$INSTALLERS_DIR" -maxdepth 1 -name "Docker*.dmg" 2>/dev/null | head -1)"
  if [ -n "$DMG" ]; then
    echo -e "${CYAN}Found Docker installer: $DMG${NC}"
    echo "Opening installer — drag Docker to Applications, then launch it."
    open "$DMG"
    echo ""
    echo -e "${YELLOW}After Docker Desktop is running (whale in menu bar), re-run:${NC}"
    echo "  bash $0"
  else
    echo "Download Docker Desktop for Mac from: https://www.docker.com/products/docker-desktop/"
    echo "Then re-run this script."
  fi
  exit 1
fi

if ! docker info &>/dev/null; then
  echo -e "${RED}Docker Desktop is not running.${NC}"
  echo "Opening Docker Desktop — wait for the whale icon, then re-run this script."
  open -a Docker
  exit 1
fi

echo -e "${GREEN}✓ Docker Desktop is running${NC}"

# ── 3. Load Docker images (once — from USB, never touches local disk as source)
if docker image inspect takeoffai-backend:latest &>/dev/null && \
   docker image inspect takeoffai-frontend:latest &>/dev/null; then
  echo -e "${GREEN}✓ TakeoffAI images already loaded${NC}"
else
  echo ""
  echo "Loading TakeoffAI into Docker (one-time, ~1 minute)..."
  docker load -i "$APP_SOURCE/takeoffai.tar.gz"
  echo -e "${GREEN}✓ Images loaded into Docker${NC}"
fi

# ── 4. Drop only the three runtime files onto the Mac ─────────────────────────
mkdir -p "$INSTALL_DIR"

cp "$APP_SOURCE/docker-compose.run.yml" "$INSTALL_DIR/docker-compose.yml"
cp "$APP_SOURCE/start.sh"               "$INSTALL_DIR/start.sh"
cp "$APP_SOURCE/stop.sh"                "$INSTALL_DIR/stop.sh"
chmod +x "$INSTALL_DIR/start.sh" "$INSTALL_DIR/stop.sh"

echo -e "${GREEN}✓ Runtime files installed to $INSTALL_DIR${NC}"

# ── 5. Set up .env ────────────────────────────────────────────────────────────
if [ ! -f "$INSTALL_DIR/.env" ]; then
  cp "$APP_SOURCE/.env.template" "$INSTALL_DIR/.env"
  echo ""
  echo -e "${YELLOW}You need an Anthropic API key to run TakeoffAI.${NC}"
  echo -e "  Get one at: ${CYAN}https://console.anthropic.com${NC}"
  echo ""
  read -r -p "Paste your API key here: " api_key
  sed -i '' "s/sk-ant-your-key-here/$api_key/" "$INSTALL_DIR/.env"
  echo -e "${GREEN}✓ API key saved${NC}"
else
  echo -e "${GREEN}✓ .env already configured${NC}"
fi

# ── 6. Start ──────────────────────────────────────────────────────────────────
echo ""
echo "Starting TakeoffAI..."
cd "$INSTALL_DIR"
docker compose up -d

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
    echo -e "${RED}Backend did not respond. Check logs: docker compose -f ~/TakeoffAI/docker-compose.yml logs backend${NC}"
    exit 1
  fi
done

# ── 7. Done ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}✅ TakeoffAI is running!${NC}"
echo ""
echo -e "   App:      ${CYAN}http://localhost:3000${NC}"
echo -e "   API docs: ${CYAN}http://localhost:8000/docs${NC}"
echo ""
echo -e "   ${YELLOW}You can now eject the USB drive.${NC}"
echo ""
echo -e "   Start:  ${YELLOW}bash ~/TakeoffAI/start.sh${NC}"
echo -e "   Stop:   ${YELLOW}bash ~/TakeoffAI/stop.sh${NC}"
echo -e "   API key: edit ${YELLOW}~/TakeoffAI/.env${NC}"
echo ""
echo -e "   Powered by ${YELLOW}answerd.it${NC}  |  support@answerd.it"
echo ""
open http://localhost:3000
