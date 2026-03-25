#!/usr/bin/env bash
# TakeoffAI Installer — by answerd.it
# Usage: ./install.sh

set -e

YELLOW='\033[1;33m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m'

echo ""
echo -e "${YELLOW}  ████████╗ █████╗ ██╗  ██╗███████╗ ██████╗ ███████╗███████╗ █████╗ ██╗${NC}"
echo -e "${YELLOW}     ██╔══╝██╔══██╗██║ ██╔╝██╔════╝██╔═══██╗██╔════╝██╔════╝██╔══██╗██║${NC}"
echo -e "${YELLOW}     ██║   ███████║█████╔╝ █████╗  ██║   ██║█████╗  █████╗  ███████║██║${NC}"
echo -e "${YELLOW}     ██║   ██╔══██║██╔═██╗ ██╔══╝  ██║   ██║██╔══╝  ██╔══╝  ██╔══██║██║${NC}"
echo -e "${YELLOW}     ██║   ██║  ██║██║  ██╗███████╗╚██████╔╝██║     ██║     ██║  ██║██║${NC}"
echo -e "${YELLOW}     ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝ ╚═════╝ ╚═╝     ╚═╝     ╚═╝  ╚═╝╚═╝${NC}"
echo -e "                         a product by ${YELLOW}answerd.it${NC}"
echo ""

# 1. Check macOS
OS=$(uname)
if [ "$OS" != "Darwin" ]; then
  echo -e "${RED}ERROR: TakeoffAI requires macOS.${NC}"
  exit 1
fi

# 2. Check Docker
if ! command -v docker &> /dev/null; then
  echo -e "${RED}Docker Desktop is not installed.${NC}"
  echo "Please install it from: https://www.docker.com/products/docker-desktop/"
  echo "Then re-run this script."
  exit 1
fi

if ! docker info &> /dev/null; then
  echo -e "${RED}Docker Desktop is not running.${NC}"
  echo "Please open Docker Desktop and wait for it to start, then re-run this script."
  exit 1
fi

echo -e "${GREEN}✓ Docker Desktop is running${NC}"

# 3. Set up .env
if [ ! -f ".env" ]; then
  cp .env.template .env
  echo ""
  echo -e "${YELLOW}Enter your Anthropic API key (get one at console.anthropic.com):${NC}"
  read -r -p "API Key: " api_key
  sed -i '' "s/sk-ant-your-key-here/$api_key/" .env
  echo -e "${GREEN}✓ API key saved${NC}"
else
  echo -e "${GREEN}✓ .env already configured${NC}"
fi

# 4. Load Docker image if tar exists
if [ -f "takeoffai.tar.gz" ]; then
  echo "Loading TakeoffAI Docker image (this takes ~2 minutes)..."
  docker load -i takeoffai.tar.gz
  echo -e "${GREEN}✓ Docker image loaded${NC}"
fi

# 5. Start containers
echo "Starting TakeoffAI..."
docker-compose up -d

# 6. Wait for health check
echo "Waiting for TakeoffAI to start..."
for i in {1..30}; do
  if curl -sf http://localhost:8000/api/health > /dev/null 2>&1; then
    break
  fi
  sleep 2
done

# 7. Open browser
echo ""
echo -e "${GREEN}✅ TakeoffAI is running!${NC}"
echo -e "   Opening ${YELLOW}http://localhost:3000${NC}..."
echo ""
echo -e "   Powered by ${YELLOW}answerd.it${NC}"
echo -e "   Support: support@answerd.it"
echo ""
open http://localhost:3000
