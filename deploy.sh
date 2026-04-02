#!/bin/bash
# ============================================================
# Deploy Script - Bot PPOB H2H.id
# Usage: bash <(curl -s -H "Authorization: token TOKEN" \
#   https://raw.githubusercontent.com/dwiexe/h2hbot/main/deploy.sh)
# ============================================================

APP_DIR=/opt/h2hbot
REPO_URL=https://github.com/dwiexe/h2hbot.git
WEBHOOK_PATH=/webhook/h2h
BOT_PORT=8080
SERVICE_NAME=h2hbot

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
warning() { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
step()    { echo -e "\n${BLUE}========== $1 ==========${NC}"; }

clear
echo -e "${GREEN}"
echo "  ██╗  ██╗██████╗ ██╗  ██╗    ██████╗  ██████╗ ████████╗"
echo "  ██║  ██║╚════██╗██║  ██║    ██╔══██╗██╔═══██╗╚══██╔══╝"
echo "  ███████║ █████╔╝███████║    ██████╔╝██║   ██║   ██║   "
echo "  ██╔══██║██╔═══╝ ╚════██║    ██╔══██╗██║   ██║   ██║   "
echo "  ██║  ██║███████╗     ██║    ██████╔╝╚██████╔╝   ██║   "
echo "  ╚═╝  ╚═╝╚══════╝     ╚═╝    ╚═════╝  ╚═════╝    ╚═╝   "
echo -e "${NC}"
echo "  Bot PPOB H2H.id - Auto Installer"
echo "  ======================================"
echo ""

[ "$EUID" -ne 0 ] && error "Jalankan sebagai root!"

step "KONFIGURASI"

IS_REINSTALL=false
ENV_BACKUP=""
if [ -f "$APP_DIR/.env" ]; then
    IS_REINSTALL=true
    ENV_BACKUP=$(cat "$APP_DIR/.env")
    warning "Instalasi sebelumnya ditemukan — .env akan dibackup otomatis"
fi

read -p "Masukkan domain bot (contoh: bot2.domain.com): " DOMAIN
[ -z "$DOMAIN" ] && error "Domain tidak boleh kosong!"

read -p "Masukkan email untuk SSL: " EMAIL
[ -z "$EMAIL" ] && error "Email tidak boleh kosong!"

if [ "$IS_REINSTALL" = true ]; then
    read -p "Gunakan .env yang lama? (Y/n): " USE_OLD_ENV
    USE_OLD_ENV=${USE_OLD_ENV:-Y}
fi

echo ""
echo "  Domain  : $DOMAIN"
echo "  Email   : $EMAIL"
echo ""
read -p "Lanjutkan instalasi? (Y/n): " CONFIRM
CONFIRM=${CONFIRM:-Y}
[[ ! "$CONFIRM" =~ ^[Yy]$ ]] && echo "Dibatalkan." && exit 0

step "1. UPDATE SYSTEM"
apt update -y
apt install -y git python3 python3-venv python3-pip nginx certbot python3-certbot-nginx curl openssl

step "2. CLONE REPO"
rm -rf $APP_DIR
git clone $REPO_URL $APP_DIR || error "Gagal clone repo!"
info "Clone berhasil"

step "3. INSTALL PYTHON REQUIREMENTS"
cd $APP_DIR
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
info "Requirements terinstall"

step "4. KONFIGURASI .ENV"
if [ "$IS_REINSTALL" = true ] && [[ "$USE_OLD_ENV" =~ ^[Yy]$ ]]; then
    echo "$ENV_BACKUP" > "$APP_DIR/.env"
    info ".env lama berhasil direstore"
else
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    warning "Isi konfigurasi .env sekarang (Ctrl+X lalu Y untuk simpan):"
    sleep 2
    nano "$APP_DIR/.env"
fi

if ! grep -q "WEBHOOK_SECRET" "$APP_DIR/.env"; then
    SECRET=$(openssl rand -hex 32)
    echo "WEBHOOK_SECRET=$SECRET" >> "$APP_DIR/.env"
    info "WEBHOOK_SECRET otomatis dibuat"
fi

step "5. SETUP NGINX"
cat > /etc/nginx/sites-available/$SERVICE_NAME << EOF
server {
    listen 80;
    server_name $DOMAIN;

    location $WEBHOOK_PATH {
        proxy_pass http://127.0.0.1:$BOT_PORT;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 30;
    }

    location / {
        return 200 'OK';
        add_header Content-Type text/plain;
    }
}
EOF

ln -sf /etc/nginx/sites-available/$SERVICE_NAME /etc/nginx/sites-enabled/$SERVICE_NAME
nginx -t && systemctl reload nginx
info "Nginx OK"

step "6. SETUP SSL"
if [ -f "/etc/letsencrypt/live/$DOMAIN/fullchain.pem" ]; then
    info "SSL sudah ada, skip certbot"
else
    certbot --nginx -d $DOMAIN --non-interactive --agree-tos --email $EMAIL --redirect || \
        warning "SSL gagal! Pastikan DNS $DOMAIN sudah pointing ke IP server ini."
fi

step "7. INSTALL SERVICE"
cp $APP_DIR/h2hbot.service /etc/systemd/system/$SERVICE_NAME.service
systemctl daemon-reload
systemctl enable $SERVICE_NAME
systemctl restart $SERVICE_NAME
info "Service $SERVICE_NAME aktif"

sleep 4
systemctl is-active --quiet $SERVICE_NAME && info "✅ Bot berjalan normal" || error "❌ Bot gagal start! Cek: journalctl -u $SERVICE_NAME -n 50"

step "8. TEST WEBHOOK"
RESP=$(curl -s -o /dev/null -w "%{http_code}" -X POST "http://127.0.0.1:$BOT_PORT$WEBHOOK_PATH" -H "Content-Type: application/json" -d '{"ref_id":"test"}')
[ "$RESP" = "200" ] && info "✅ Webhook aktif" || warning "Webhook belum merespons ($RESP)"

SECRET_VAL=$(grep "WEBHOOK_SECRET" "$APP_DIR/.env" 2>/dev/null | cut -d'=' -f2)

echo ""
echo -e "${GREEN}=================================================="
echo "  ✅  INSTALASI SELESAI!"
echo -e "==================================================${NC}"
echo ""
echo "  🌐 Domain   : https://$DOMAIN"
echo "  🔗 Webhook  : https://$DOMAIN$WEBHOOK_PATH"
echo "  🔑 Secret   : $SECRET_VAL"
echo ""
echo -e "${YELLOW}  ⚠️  DAFTARKAN WEBHOOK KE H2H.ID:${NC}"
echo "  Dashboard H2H.id → Pengaturan → API H2H"
echo "  Callback URL: https://$DOMAIN$WEBHOOK_PATH"
echo ""
echo "  📋 Perintah berguna:"
echo "  • Log   : journalctl -u $SERVICE_NAME -f"
echo "  • Restart: systemctl restart $SERVICE_NAME"
echo -e "${GREEN}==================================================${NC}"
