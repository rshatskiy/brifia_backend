#!/bin/bash
# ============================================
# Brifia VPS Deployment Script
# Run as root on Ubuntu 24.04
# Usage: bash deploy_vps.sh
# ============================================

set -e

echo "============================================"
echo "  Brifia VPS Deployment"
echo "============================================"

# --- 1. System update ---
echo ""
echo "=== 1/7: Updating system ==="
apt update && apt upgrade -y
apt install -y curl git ufw nginx certbot python3-certbot-nginx \
    python3 python3-pip python3-venv \
    postgresql postgresql-contrib

# --- 2. Node.js 20 ---
echo ""
echo "=== 2/7: Installing Node.js 20 ==="
if ! command -v node &> /dev/null; then
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
    apt install -y nodejs
fi
echo "Node.js: $(node --version)"
echo "npm: $(npm --version)"

# --- 3. PostgreSQL setup ---
echo ""
echo "=== 3/7: Configuring PostgreSQL ==="
DB_PASSWORD=$(openssl rand -hex 16)

sudo -u postgres psql -c "CREATE USER brifia WITH PASSWORD '${DB_PASSWORD}';" 2>/dev/null || echo "User brifia already exists"
sudo -u postgres psql -c "CREATE DATABASE brifia OWNER brifia;" 2>/dev/null || echo "Database brifia already exists"
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE brifia TO brifia;"

echo "DB_PASSWORD=${DB_PASSWORD}" > /root/.brifia_db_credentials
chmod 600 /root/.brifia_db_credentials
echo "Database credentials saved to /root/.brifia_db_credentials"

# --- 4. Backend (FastAPI) ---
echo ""
echo "=== 4/7: Setting up backend ==="
mkdir -p /opt/brifia
cd /opt/brifia

if [ ! -d "backend" ]; then
    git clone https://github.com/rshatskiy/brifia_backend.git backend
else
    cd backend && git pull && cd ..
fi

cd backend
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
pip install asyncpg  # For migration script

# Create .env
JWT_SECRET=$(openssl rand -hex 32)
WHISPER_API_KEY=$(openssl rand -hex 16)

cat > .env << ENVEOF
DATABASE_URL=postgresql+asyncpg://brifia:${DB_PASSWORD}@localhost:5432/brifia
JWT_SECRET=${JWT_SECRET}
JWT_ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=30
REFRESH_TOKEN_EXPIRE_DAYS=30

GOOGLE_CLIENT_ID=287282098766-nul8hncrbkqb17tt79gjpaov3t0hr5if.apps.googleusercontent.com
GOOGLE_IOS_CLIENT_ID=287282098766-8diua7d5dbg644o5grf1kr5e4kc2rl7u.apps.googleusercontent.com

YOOKASSA_SHOP_ID=
YOOKASSA_SECRET_KEY=
YOOKASSA_WEBHOOK_SECRET=

PAYMENT_SUCCESS_URL=https://brifia.ru/payment/success
PAYMENT_CANCEL_URL=https://brifia.ru/payment/cancel

FASTER_WHISPER_API_KEY=${WHISPER_API_KEY}
ENVEOF

echo "FASTER_WHISPER_API_KEY=${WHISPER_API_KEY}" >> /root/.brifia_db_credentials

deactivate
cd /opt/brifia

# --- 5. Web (Next.js) ---
echo ""
echo "=== 5/7: Setting up web app ==="
if [ ! -d "web" ]; then
    git clone https://github.com/rshatskiy/brifia_web.git web
else
    cd web && git pull && cd ..
fi

cd web
cat > .env.local << ENVEOF
NEXT_PUBLIC_API_URL=https://api.brifia.ru
ENVEOF

npm install
npm run build
cd /opt/brifia

# --- 6. Systemd services ---
echo ""
echo "=== 6/7: Creating systemd services ==="

# Backend service
cat > /etc/systemd/system/brifia-api.service << 'SVCEOF'
[Unit]
Description=Brifia API Backend
After=network.target postgresql.service

[Service]
Type=simple
User=root
WorkingDirectory=/opt/brifia/backend
ExecStart=/opt/brifia/backend/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
Environment=PATH=/opt/brifia/backend/venv/bin:/usr/bin

[Install]
WantedBy=multi-user.target
SVCEOF

# Web service
cat > /etc/systemd/system/brifia-web.service << 'SVCEOF'
[Unit]
Description=Brifia Web App
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/brifia/web
ExecStart=/usr/bin/npm start -- -p 3000
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
SVCEOF

systemctl daemon-reload
systemctl enable brifia-api brifia-web
systemctl start brifia-api
systemctl start brifia-web

echo "Services started. Checking status..."
sleep 2
systemctl status brifia-api --no-pager -l | head -5
systemctl status brifia-web --no-pager -l | head -5

# --- 7. Nginx + Firewall ---
echo ""
echo "=== 7/7: Configuring Nginx and Firewall ==="

# Nginx config for API
cat > /etc/nginx/sites-available/brifia-api << 'NGXEOF'
server {
    listen 80;
    server_name api.brifia.ru;

    client_max_body_size 100M;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    location /ws/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_read_timeout 86400;
    }
}
NGXEOF

# Nginx config for Web
cat > /etc/nginx/sites-available/brifia-web << 'NGXEOF'
server {
    listen 80;
    server_name brifia.ru www.brifia.ru;

    location / {
        proxy_pass http://127.0.0.1:3000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGXEOF

ln -sf /etc/nginx/sites-available/brifia-api /etc/nginx/sites-enabled/
ln -sf /etc/nginx/sites-available/brifia-web /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

nginx -t && systemctl reload nginx

# Firewall
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo ""
echo "============================================"
echo "  DEPLOYMENT COMPLETE!"
echo "============================================"
echo ""
echo "Services:"
echo "  API:  http://31.129.105.16:8000"
echo "  Web:  http://31.129.105.16:3000"
echo ""
echo "Next steps:"
echo "  1. Point DNS: api.brifia.ru → 31.129.105.16"
echo "  2. Point DNS: brifia.ru → 31.129.105.16"
echo "  3. Run: certbot --nginx -d api.brifia.ru -d brifia.ru -d www.brifia.ru"
echo "  4. Fill YOOKASSA_* in /opt/brifia/backend/.env"
echo "  5. Update BACKEND_API_KEY in faster-whisper .env"
echo "     Key: $(grep FASTER_WHISPER_API_KEY /root/.brifia_db_credentials | cut -d= -f2)"
echo ""
echo "Credentials saved to /root/.brifia_db_credentials"
cat /root/.brifia_db_credentials
echo ""
