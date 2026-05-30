#!/bin/bash
# ╔══════════════════════════════════════════════════════╗
# ║        AUTOPAY STORE BOT — SETUP INSTALLER          ║
# ║        by NEXUSDEV | nexusdev.web.id                ║
# ╚══════════════════════════════════════════════════════╝

set -e

# ── Warna terminal ──
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
BOLD='\033[1m'
NC='\033[0m'

GITHUB_RAW="https://raw.githubusercontent.com/kiryusekei/storev7/main/autoorder.py"
BOT_DIR="/root/autoorder-bot"
SERVICE_NAME="autoorder-bot"

banner() {
    clear
    echo -e "${CYAN}${BOLD}"
    echo "  ╔══════════════════════════════════════════════╗"
    echo "  ║       AUTOPAY STORE BOT — INSTALLER         ║"
    echo "  ║       NEXUSDEV | nexusdev.web.id            ║"
    echo "  ╚══════════════════════════════════════════════╝"
    echo -e "${NC}"
}

info()    { echo -e "${GREEN}[✓]${NC} $1"; }
warn()    { echo -e "${YELLOW}[!]${NC} $1"; }
error()   { echo -e "${RED}[✗]${NC} $1"; exit 1; }
step()    { echo -e "\n${CYAN}${BOLD}══ $1 ══${NC}"; }

# ── Cek root ──
[ "$(id -u)" -ne 0 ] && error "Jalankan sebagai root: sudo bash setup.sh"

banner

# ══════════════════════════════════════════════
#  STEP 1: INPUT KONFIGURASI
# ══════════════════════════════════════════════
step "KONFIGURASI BOT"

echo -e "${BOLD}Masukkan data konfigurasi bot kamu:${NC}\n"

# Bot Token
while true; do
    read -p "  🤖 Bot Token (dari @BotFather): " BOT_TOKEN
    [[ -n "$BOT_TOKEN" ]] && break
    warn "Token tidak boleh kosong!"
done

# Admin ID
while true; do
    read -p "  👤 Admin Telegram ID (angka): " ADMIN_ID
    [[ "$ADMIN_ID" =~ ^[0-9]+$ ]] && break
    warn "Harus berupa angka! Contoh: 123456789"
done

# API Key Pakasir
while true; do
    read -p "  💳 API Key Pakasir (dari dashboard app.pakasir.com): " API_KEY
    [[ -n "$API_KEY" ]] && break
    warn "API Key tidak boleh kosong!"
done

# Pakasir Project Slug
while true; do
    read -p "  🏷️  Pakasir Project Slug (dari dashboard app.pakasir.com): " PAKASIR_PROJECT
    [[ -n "$PAKASIR_PROJECT" ]] && break
    warn "Project Slug tidak boleh kosong!"
done

# Nama Toko
while true; do
    read -p "  🏪 Nama Toko (tampil di bot & nota): " STORE_NAME
    [[ -n "$STORE_NAME" ]] && break
    warn "Nama toko tidak boleh kosong!"
done

# Website
read -p "  🔗 Website / Link toko [nexusdev.web.id]: " WEBSITE
WEBSITE="${WEBSITE:-nexusdev.web.id}"

echo ""
echo -e "${BOLD}══════════════════════════════════════${NC}"
echo -e "  🤖 Token    : ${CYAN}${BOT_TOKEN:0:20}...${NC}"
echo -e "  👤 Admin ID : ${CYAN}${ADMIN_ID}${NC}"
echo -e "  💳 API Key  : ${CYAN}${API_KEY:0:15}...${NC}"
echo -e "  🏷️  Slug     : ${CYAN}${PAKASIR_PROJECT}${NC}"
echo -e "  🏪 Nama     : ${CYAN}${STORE_NAME}${NC}"
echo -e "  🔗 Website  : ${CYAN}${WEBSITE}${NC}"
echo -e "${BOLD}══════════════════════════════════════${NC}"
echo ""
read -p "  Lanjutkan instalasi? [y/N]: " CONFIRM
[[ "$CONFIRM" =~ ^[Yy]$ ]] || { warn "Instalasi dibatalkan."; exit 0; }

# ══════════════════════════════════════════════
#  STEP 2: INSTALL DEPENDENSI SISTEM
# ══════════════════════════════════════════════
step "INSTALL DEPENDENSI"

info "Update package list..."
apt-get update -qq

info "Install Python3, pip, font DejaVu..."
apt-get install -y -qq \
    python3 python3-pip python3-venv \
    fonts-dejavu-core fonts-dejavu-extra \
    curl wget git

info "Install library Python..."
pip3 install -q --break-system-packages \
    python-telegram-bot==21.3 \
    aiohttp \
    pillow \
    qrcode \
    2>/dev/null || \
pip3 install -q \
    python-telegram-bot==21.3 \
    aiohttp \
    pillow \
    qrcode

info "Dependensi berhasil diinstall ✅"

# ══════════════════════════════════════════════
#  STEP 3: DOWNLOAD & SETUP BOT
# ══════════════════════════════════════════════
step "DOWNLOAD BOT"

mkdir -p "$BOT_DIR"
cd "$BOT_DIR"

info "Download autoorder.py dari GitHub..."

if ! curl -fsSL "$GITHUB_RAW" -o autoorder.py 2>/dev/null; then
    warn "Gagal download dari GitHub. Coba metode lain..."
    # Fallback: coba wget
    if ! wget -q "$GITHUB_RAW" -O autoorder.py 2>/dev/null; then
        error "Gagal download bot! Cek URL GitHub di variabel GITHUB_RAW dalam script ini."
    fi
fi

info "File bot berhasil didownload ✅"

# ══════════════════════════════════════════════
#  STEP 4: INJECT KONFIGURASI
# ══════════════════════════════════════════════
step "KONFIGURASI"

info "Menulis konfigurasi ke bot..."

# Escape karakter khusus untuk sed
ESC_TOKEN=$(printf '%s\n' "$BOT_TOKEN" | sed 's/[[\.*^$()+?{|]/\\&/g')
ESC_APIKEY=$(printf '%s\n' "$API_KEY"  | sed 's/[[\.*^$()+?{|]/\\&/g')
ESC_SLUG=$(printf '%s\n' "$PAKASIR_PROJECT" | sed 's/[[\.*^$()+?{|]/\\&/g')
ESC_STORE=$(printf '%s\n' "$STORE_NAME" | sed 's/[[\.*^$()+?{|]/\\&/g')
ESC_WEB=$(printf '%s\n' "$WEBSITE" | sed 's/[[\.*^$()+?{|]/\\&/g')

sed -i \
    -e "s|BOT_TOKEN  = \"ISI_TOKEN_BOT\"|BOT_TOKEN  = \"${ESC_TOKEN}\"|" \
    -e "s|ADMIN_IDS  = \[123456789\]|ADMIN_IDS  = [${ADMIN_ID}]|" \
    -e "s|API_KEY    = \"ISI_API_KEY_PAKASIR\"|API_KEY    = \"${ESC_APIKEY}\"|" \
    -e "s|PAKASIR_PROJECT = \"ISI_SLUG_PROYEK\"|PAKASIR_PROJECT = \"${ESC_SLUG}\"|" \
    -e "s|STORE_NAME    = \"NEXUS MARKETING\"|STORE_NAME    = \"${ESC_STORE}\"|" \
    -e "s|WEBSITE       = \"nexusdev.web.id\"|WEBSITE       = \"${ESC_WEB}\"|" \
    autoorder.py

info "Konfigurasi berhasil ditulis ✅"

# Verifikasi
python3 -m py_compile autoorder.py && info "Syntax Python OK ✅" || error "Syntax error! Cek file autoorder.py"

# ══════════════════════════════════════════════
#  STEP 5: SETUP SYSTEMD SERVICE
# ══════════════════════════════════════════════
step "SETUP SYSTEMD SERVICE"

cat > /etc/systemd/system/${SERVICE_NAME}.service << EOF
[Unit]
Description=Autopay Store Bot — ${STORE_NAME}
After=network.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${BOT_DIR}
ExecStart=/usr/bin/python3 ${BOT_DIR}/autoorder.py
Restart=always
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=${SERVICE_NAME}
User=root

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable ${SERVICE_NAME} --quiet
systemctl restart ${SERVICE_NAME}

sleep 2

# ══════════════════════════════════════════════
#  DONE
# ══════════════════════════════════════════════
banner

STATUS=$(systemctl is-active ${SERVICE_NAME} 2>/dev/null)
if [ "$STATUS" = "active" ]; then
    echo -e "${GREEN}${BOLD}  ✅ BOT BERHASIL DIINSTALL & BERJALAN!${NC}\n"
else
    echo -e "${YELLOW}${BOLD}  ⚠️  Bot diinstall tapi mungkin belum aktif.${NC}\n"
fi

echo -e "  🏪 Toko     : ${BOLD}${STORE_NAME}${NC}"
echo -e "  📁 Lokasi   : ${BOLD}${BOT_DIR}${NC}"
echo -e "  🔧 Service  : ${BOLD}${SERVICE_NAME}${NC}"
echo -e "  📊 Status   : ${BOLD}${STATUS}${NC}"
echo ""
echo -e "${CYAN}${BOLD}  PERINTAH BERGUNA:${NC}"
echo -e "  ├ Lihat log  : ${BOLD}journalctl -u ${SERVICE_NAME} -f${NC}"
echo -e "  ├ Stop bot   : ${BOLD}systemctl stop ${SERVICE_NAME}${NC}"
echo -e "  ├ Start bot  : ${BOLD}systemctl start ${SERVICE_NAME}${NC}"
echo -e "  └ Restart    : ${BOLD}systemctl restart ${SERVICE_NAME}${NC}"
echo ""
echo -e "  ${YELLOW}⚠️  Jangan lupa set ADMIN di bot dengan /start${NC}"
echo -e "${CYAN}══════════════════════════════════════════════${NC}\n"
