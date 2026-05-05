#!/bin/bash
#
# GarageSaleMap.app Production Setup Script
# Automates deployment on Ubuntu 22.04/24.04
#

set -e

echo "================================================"
echo "GarageSaleMap.app Production Setup"
echo "================================================"

# Check if running as root
if [ "$EUID" -ne 0 ]; then
   echo "Please run as root (sudo ./setup_production.sh)"
   exit 1
fi

# Update system
echo "[1/10] Updating system packages..."
apt update && apt upgrade -y

# Install dependencies
echo "[2/10] Installing dependencies..."
apt install -y \
    python3-pip python3-venv \
    postgresql postgresql-contrib \
    nginx \
    redis-server \
    certbot python3-certbot-nginx \
    git curl

# Create app user and directory
echo "[3/10] Creating application directory..."
mkdir -p /var/www/garagesalemap
cd /var/www/garagesalemap

# Create Python virtual environment
echo "[4/10] Setting up Python environment..."
python3 -m venv venv
source venv/bin/activate

# Install Python packages
echo "[5/10] Installing Python packages..."
pip install --upgrade pip
pip install \
    flask flask-sqlalchemy flask-cors \
    stripe werkzeug \
    gunicorn \
    psycopg2-binary \
    requests beautifulsoup4 \
    python-dotenv \
    redis celery

# Create database
echo "[6/10] Creating PostgreSQL database..."
sudo -u postgres psql -c "CREATE DATABASE garagesalemap;" || true
sudo -u postgres psql -c "CREATE USER garagesale WITH PASSWORD 'changeme123';" || true
sudo -u postgres psql -c "GRANT ALL PRIVILEGES ON DATABASE garagesalemap TO garagesale;" || true

# Copy application files
echo "[7/10] Copying application files..."
# (Assumes files are in current directory)

# Create log directory
mkdir -p /var/log/garagesalemap
chown www-data:www-data /var/log/garagesalemap

# Create .env if it doesn't exist
if [ ! -f .env ]; then
    echo "[8/10] Creating .env file..."
    cat > .env << 'ENVEOF'
SECRET_KEY=$(openssl rand -hex 32)
DATABASE_URL=postgresql://garagesale:changeme123@localhost/garagesalemap
ADMIN_EMAIL=admin@garagesalemap.app
ADMIN_PASSWORD=changeme123
STRIPE_SECRET_KEY=sk_test_REPLACE_WITH_REAL_KEY
STRIPE_PRICE_ID=price_REPLACE_WITH_REAL_PRICE
STRIPE_WEBHOOK_SECRET=whsec_REPLACE_WITH_REAL_SECRET
ENVEOF
    echo "IMPORTANT: Edit /var/www/garagesalemap/.env with your Stripe keys!"
fi

# Set permissions
chown -R www-data:www-data /var/www/garagesalemap

# Create systemd service
echo "[9/10] Creating systemd service..."
cat > /etc/systemd/system/garagesalemap.service << 'SERVICEEOF'
[Unit]
Description=GarageSaleMap Flask Application
After=network.target postgresql.service redis.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/garagesalemap
Environment="PATH=/var/www/garagesalemap/venv/bin"

ExecStart=/var/www/garagesalemap/venv/bin/gunicorn \
    --workers 4 \
    --bind 127.0.0.1:5000 \
    --timeout 60 \
    --access-logfile /var/log/garagesalemap/access.log \
    --error-logfile /var/log/garagesalemap/error.log \
    garagesalemap_app:app

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
SERVICEEOF

# Configure Nginx
echo "[10/10] Configuring Nginx..."
cat > /etc/nginx/sites-available/garagesalemap << 'NGINXEOF'
server {
    listen 80;
    server_name garagesalemap.app www.garagesalemap.app;
    
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINXEOF

ln -sf /etc/nginx/sites-available/garagesalemap /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx config
nginx -t

# Start services
echo "Starting services..."
systemctl daemon-reload
systemctl enable garagesalemap
systemctl start garagesalemap
systemctl enable nginx
systemctl restart nginx

echo ""
echo "================================================"
echo "Setup Complete!"
echo "================================================"
echo ""
echo "Next steps:"
echo "1. Edit /var/www/garagesalemap/.env with your Stripe keys"
echo "2. Point your domain to this server's IP"
echo "3. Run: certbot --nginx -d garagesalemap.app -d www.garagesalemap.app"
echo "4. Visit https://garagesalemap.app"
echo ""
echo "Service status:"
systemctl status garagesalemap --no-pager
echo ""
echo "Logs: journalctl -u garagesalemap -f"
echo ""
