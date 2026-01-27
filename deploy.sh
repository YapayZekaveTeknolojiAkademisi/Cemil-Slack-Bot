#!/bin/bash

# Renkler
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Dizin ayarları
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo -e "${GREEN}🚀 Cemil Bot Deployment Script${NC}"

# Python komutunu belirle
if command -v python3 &>/dev/null; then
    PYTHON_BIN=python3
else
    PYTHON_BIN=python
fi

# Sanal ortam
if [ -d ".venv" ]; then
    echo -e "${YELLOW}📦 Sanal ortam aktif ediliyor...${NC}"
    source .venv/bin/activate
fi

# 1. DURDURMA (STOP)
echo -e "${YELLOW}🛑 Eski süreçler kontrol ediliyor...${NC}"
# PID dosyasından oku
if [ -f "bot.pid" ]; then
    OLD_PID=$(cat bot.pid)
    if ps -p $OLD_PID > /dev/null; then
        echo -e "   Process $OLD_PID durduruluyor..."
        kill $OLD_PID
        sleep 2
    fi
    rm bot.pid
fi

# Garanti olsun diye pattern ile de öldür
pkill -f "python.* -m src" 2>/dev/null
echo -e "${GREEN}✅ Eski süreçler temizlendi.${NC}"

# 2. GÜNCELLEME (UPDATE)
if [[ "$1" == "--update" ]]; then
    echo -e "${YELLOW}⬇️  Git pull yapılıyor...${NC}"
    git pull
    
    echo -e "${YELLOW}📚 Bağımlılıklar güncelleniyor...${NC}"
    pip install -r requirements.txt
    echo -e "${GREEN}✅ Güncelleme tamamlandı.${NC}"
fi

# 3. BAŞLATMA (START)
echo -e "${YELLOW}🤖 Bot arka planda (daemon) başlatılıyor...${NC}"

# Non-interactive mod
export CEMIL_NON_INTERACTIVE=1

nohup $PYTHON_BIN -m src > bot.log 2>&1 &
NEW_PID=$!

echo $NEW_PID > bot.pid

echo -e "${GREEN}✅ Bot başlatıldı! PID: $NEW_PID${NC}"
echo -e "📄 Logları izlemek için: ${YELLOW}tail -f bot.log${NC}"
echo -e "🛑 Durdurmak için: ${YELLOW}./deploy.sh${NC} (tekrar çalıştırınca öldürür ve yeniden başlatır)"
