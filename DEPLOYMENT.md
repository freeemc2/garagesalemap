# GarageSaleMap.app - Production Deployment Guide

## Quick Start

```bash
# 1. Clone to your server
git clone https://github.com/freeemc2/garagesalemap.git garagesalemap
cd garagesalemap

# 2. Run setup script
chmod +x setup_production.sh
./setup_production.sh

# 3. Configure environment
cp .env.example .env
nano .env  # Add your Stripe keys, admin password, etc.

# 4. Start the service
systemctl start garagesalemap
systemctl enable garagesalemap
```

## Domain Setup

**You have 3 domains:**
- GarageSaleMap.app (primary)
- YardSaleMap.app (backup)
- YardSaleMap.net (backup)

**DNS Configuration:**
Point all 3 domains to your server IP:
```
A Record: @     → 103.195.100.158
A Record: www   → 103.195.100.158
```

**SSL Certificates (Let's Encrypt):**
```bash
certbot --nginx -d garagesalemap.app -d www.garagesalemap.app
certbot --nginx -d yardsalemap.app -d www.yardsalemap.app
certbot --nginx -d yardsalemap.net -d www.yardsalemap.net
```

## Environment Variables

Create `.env` file:
```bash
# Flask
SECRET_KEY=your-super-secret-key-here-change-this
DATABASE_URL=postgresql://garagesale:password@localhost/garagesalemap

# Stripe
STRIPE_SECRET_KEY=sk_live_xxxxx
STRIPE_PRICE_ID=price_xxxxx
STRIPE_WEBHOOK_SECRET=whsec_xxxxx

# Admin
ADMIN_EMAIL=admin@garagesalemap.app
ADMIN_PASSWORD=change-this-secure-password

# Optional
SENTRY_DSN=https://xxxxx@sentry.io/xxxxx  # Error tracking
```

## Production Architecture

```
                    ┌──────────────┐
                    │   Nginx      │
                    │  (SSL/CDN)   │
                    └──────┬───────┘
                           │
                    ┌──────┴───────┐
                    │  Gunicorn    │
                    │  (4 workers) │
                    └──────┬───────┘
                           │
      ┌────────────────────┼────────────────────┐
      │                    │                    │
┌─────┴────┐        ┌──────┴──────┐      ┌─────┴─────┐
│ Flask    │        │ PostgreSQL  │      │  Redis    │
│ App      │        │  Database   │      │  Cache    │
└──────────┘        └─────────────┘      └───────────┘
      │
┌─────┴─────────────────────────────────────┐
│  Background Workers (Celery)              │
│  - Google scraper runs every 6 hours      │
│  - Geocoding queue                        │
│  - Email notifications                    │
└───────────────────────────────────────────┘
```

## Nginx Configuration

```nginx
# /etc/nginx/sites-available/garagesalemap

upstream garagesalemap {
    server 127.0.0.1:5000;
    server 127.0.0.1:5001;
    server 127.0.0.1:5002;
    server 127.0.0.1:5003;
}

# Redirect all to HTTPS
server {
    listen 80;
    server_name garagesalemap.app www.garagesalemap.app yardsalemap.app www.yardsalemap.app yardsalemap.net www.yardsalemap.net;
    return 301 https://garagesalemap.app$request_uri;
}

# Main HTTPS server
server {
    listen 443 ssl http2;
    server_name garagesalemap.app www.garagesalemap.app;
    
    ssl_certificate /etc/letsencrypt/live/garagesalemap.app/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/garagesalemap.app/privkey.pem;
    
    # SSL best practices
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_prefer_server_ciphers on;
    ssl_ciphers 'ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384';
    
    # Security headers
    add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
    
    # Rate limiting
    limit_req_zone $binary_remote_addr zone=api:10m rate=100r/m;
    limit_req zone=api burst=20;
    
    # Static files
    location /static {
        alias /var/www/garagesalemap/static;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }
    
    # Proxy to Flask
    location / {
        proxy_pass http://garagesalemap;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        
        # Timeouts for long-running scraper requests
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }
}

# Redirect backup domains to primary
server {
    listen 443 ssl http2;
    server_name yardsalemap.app www.yardsalemap.app yardsalemap.net www.yardsalemap.net;
    
    ssl_certificate /etc/letsencrypt/live/yardsalemap.app/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/yardsalemap.app/privkey.pem;
    
    return 301 https://garagesalemap.app$request_uri;
}
```

## Systemd Service

```ini
# /etc/systemd/system/garagesalemap.service

[Unit]
Description=GarageSaleMap Flask Application
After=network.target postgresql.service redis.service

[Service]
User=www-data
Group=www-data
WorkingDirectory=/var/www/garagesalemap
Environment="PATH=/var/www/garagesalemap/venv/bin"

# Run 4 workers for concurrency
ExecStart=/var/www/garagesalemap/venv/bin/gunicorn \
    --workers 4 \
    --bind 127.0.0.1:5000 \
    --bind 127.0.0.1:5001 \
    --bind 127.0.0.1:5002 \
    --bind 127.0.0.1:5003 \
    --timeout 60 \
    --access-logfile /var/log/garagesalemap/access.log \
    --error-logfile /var/log/garagesalemap/error.log \
    garagesalemap_app:app

Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

## Database Setup

```bash
# Install PostgreSQL
sudo apt install postgresql postgresql-contrib

# Create database and user
sudo -u postgres psql
CREATE DATABASE garagesalemap;
CREATE USER garagesale WITH PASSWORD 'secure_password_here';
GRANT ALL PRIVILEGES ON DATABASE garagesalemap TO garagesale;

# Enable PostGIS for geo queries (optional but recommended)
\c garagesalemap
CREATE EXTENSION postgis;
```

## Scaling for Hundreds of Concurrent Users

**Current capacity:** ~400 concurrent users

**To scale to 1000+ users:**

1. **Add more Gunicorn workers:**
   ```
   workers = (2 × CPU cores) + 1
   For 8-core server: --workers 17
   ```

2. **Enable caching with Redis:**
   ```python
   # Cache sale listings for 5 minutes
   from flask_caching import Cache
   cache = Cache(app, config={'CACHE_TYPE': 'redis'})
   ```

3. **Database connection pooling:**
   ```
   SQLALCHEMY_POOL_SIZE = 20
   SQLALCHEMY_MAX_OVERFLOW = 40
   ```

4. **Use CDN for static assets:**
   - CloudFlare (free tier)
   - Serve images/CSS/JS from CDN

5. **Background job queue (Celery):**
   ```bash
   # Move scraping to background workers
   celery -A tasks worker --loglevel=info --concurrency=4
   ```

## Monitoring & Maintenance

**Health checks:**
```bash
# Check service status
curl https://garagesalemap.app/health

# Monitor logs
tail -f /var/log/garagesalemap/error.log
journalctl -u garagesalemap -f
```

**Database maintenance:**
```sql
-- Vacuum old data weekly
VACUUM ANALYZE sales;

-- Archive old sales (>90 days)
UPDATE sales SET active = false 
WHERE scraped_at < NOW() - INTERVAL '90 days';
```

**Backup strategy:**
```bash
# Daily database backup
pg_dump garagesalemap | gzip > backup-$(date +%Y%m%d).sql.gz

# Weekly offsite backup to S3
aws s3 cp backup-*.sql.gz s3://garagesalemap-backups/
```

## Scraper Schedule

**Automatic scraping (via cron):**
```cron
# Every 6 hours, scrape major cities
0 */6 * * * curl -X POST https://garagesalemap.app/admin/scrape \
    -H "Authorization: Bearer $ADMIN_TOKEN" \
    -d '{"location": "Fort Myers, FL", "radius": 50}'

# Different cities throughout the day
0 0 * * * # Miami, FL
0 6 * * * # Tampa, FL
0 12 * * * # Orlando, FL
0 18 * * * # Jacksonville, FL
```

## Stripe Webhook Setup

1. Add webhook endpoint in Stripe Dashboard:
   ```
   https://garagesalemap.app/stripe/webhook
   ```

2. Subscribe to events:
   - `customer.subscription.created`
   - `customer.subscription.deleted`
   - `customer.subscription.updated`
   - `invoice.payment_succeeded`
   - `invoice.payment_failed`

3. Copy webhook secret to `.env`

## Going Live Checklist

- [ ] DNS records configured
- [ ] SSL certificates installed
- [ ] Environment variables set
- [ ] Database created and migrated
- [ ] Stripe account in live mode
- [ ] Admin account created
- [ ] Initial scrape completed (>1000 sales)
- [ ] Health check passing
- [ ] Error tracking configured (Sentry)
- [ ] Backup system tested
- [ ] Load testing passed (100+ concurrent)
- [ ] Analytics installed (Google Analytics)
- [ ] Privacy policy published
- [ ] Terms of service published

## Post-Launch

**Week 1:**
- Monitor error logs hourly
- Respond to support emails within 1 hour
- Track sign-up conversion rate
- Adjust scraper frequency based on traffic

**Month 1:**
- Analyze user feedback
- Add most-requested features
- Optimize scraper sources
- A/B test pricing ($6.99 vs $7.99 vs $9.99)

**To $5k MRR (625 subscribers):**
- Facebook ad campaign ($500/month budget)
- SEO optimization (rank for "garage sales near [city]")
- Referral program (1 month free per 3 referrals)
- Partnership with estate sale companies

## Support & Documentation

**Admin panel:** https://garagesalemap.app/admin
**API docs:** https://garagesalemap.app/api/docs
**Status page:** https://status.garagesalemap.app

**Questions?** admin@garagesalemap.app
