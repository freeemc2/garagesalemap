"""
GarageSaleMap.app - Production Flask Application
Features:
- Google-based scraper (multiple sources)
- Customer listing upload
- Multi-language support (English/Spanish)
- Admin backend with search + programmable inputs
- Scalable to hundreds concurrent users
- $7.99/month Stripe subscription
"""
import os
import json
import time
import threading
import logging
from datetime import datetime, timedelta
from functools import wraps

from dotenv import load_dotenv
load_dotenv()

import stripe
from flask import (
    Flask, render_template, request, jsonify,
    session, redirect, url_for, flash
)
from flask_sqlalchemy import SQLAlchemy
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash

# ---------------------------------------------------------------------------
# App Config
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "change-in-production")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///garagesalemap.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SQLALCHEMY_ENGINE_OPTIONS"] = {
    "pool_size": 20,
    "pool_recycle": 3600,
    "pool_pre_ping": True,
}

# Enable CORS for API endpoints
CORS(app)

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY", "")
STRIPE_PRICE_ID = os.environ.get("STRIPE_PRICE_ID", "")

db = SQLAlchemy(app)
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    language = db.Column(db.String(10), default="en")  # en, es
    trial_start = db.Column(db.DateTime, default=datetime.utcnow)
    stripe_customer_id = db.Column(db.String(255))
    stripe_subscription_id = db.Column(db.String(255))
    subscription_status = db.Column(db.String(50), default="trial")
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    
    @property
    def trial_active(self):
        return datetime.utcnow() < self.trial_start + timedelta(days=3)
    
    @property
    def has_access(self):
        return self.trial_active or self.subscription_status == "active"


class Sale(db.Model):
    """Garage/yard/estate sale listing"""
    __tablename__ = "sales"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)  # User-submitted sales
    source = db.Column(db.String(100), index=True)  # Google, Facebook, User, etc.
    title = db.Column(db.String(500), nullable=False)
    url = db.Column(db.String(1000))
    address = db.Column(db.String(500))
    date_text = db.Column(db.String(200))
    description = db.Column(db.Text)
    lat = db.Column(db.Float, index=True)
    lon = db.Column(db.Float, index=True)
    city = db.Column(db.String(200))
    state = db.Column(db.String(10))
    zip_code = db.Column(db.String(10))
    active = db.Column(db.Boolean, default=True, index=True)
    approved = db.Column(db.Boolean, default=True)  # User submissions need approval
    scraped_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    scrape_run_id = db.Column(db.Integer, db.ForeignKey("scrape_run.id"))


class ScrapeRun(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    started_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    finished_at = db.Column(db.DateTime)
    location = db.Column(db.String(255))
    sale_count = db.Column(db.Integer, default=0)
    status = db.Column(db.String(50), default="running", index=True)
    error_msg = db.Column(db.Text)
    scraper_type = db.Column(db.String(50), default="google")  # google, yardsalesearch


class AdminConfig(db.Model):
    """Admin-configurable settings"""
    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    description = db.Column(db.String(500))
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------------------------------------------------------------------------
# Auth Decorators
# ---------------------------------------------------------------------------

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_page"))
        return f(*args, **kwargs)
    return decorated


def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_page"))
        user = User.query.get(session["user_id"])
        if not user or not user.is_admin:
            return redirect(url_for("index"))
        return f(*args, **kwargs)
    return decorated


def access_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if "user_id" not in session:
            return redirect(url_for("login_page"))
        user = User.query.get(session["user_id"])
        if not user or not user.has_access:
            return redirect(url_for("paywall_page"))
        return f(*args, **kwargs)
    return decorated


# ---------------------------------------------------------------------------
# Translation Helper
# ---------------------------------------------------------------------------

TRANSLATIONS = {
    "en": {
        "site_name": "GarageSaleMap",
        "tagline": "Find garage sales, yard sales, and estate sales near you",
        "sign_up": "Sign Up",
        "sign_in": "Sign In",
        "search_location": "Enter your location",
        "submit_sale": "List Your Sale",
        "view_map": "View Map",
        "free_trial": "3 Days Free Trial",
        "then_monthly": "Then $7.99/month",
    },
    "es": {
        "site_name": "MapaDeVentasDeGaraje",
        "tagline": "Encuentra ventas de garaje, ventas de jardín y ventas de patrimonio cerca de ti",
        "sign_up": "Registrarse",
        "sign_in": "Iniciar Sesión",
        "search_location": "Ingrese su ubicación",
        "submit_sale": "Publicar Tu Venta",
        "view_map": "Ver Mapa",
        "free_trial": "3 Días de Prueba Gratis",
        "then_monthly": "Luego $7.99/mes",
    }
}

def get_text(key, lang="en"):
    """Get translated text"""
    return TRANSLATIONS.get(lang, TRANSLATIONS["en"]).get(key, key)


# ---------------------------------------------------------------------------
# Public Routes
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """Landing page"""
    lang = request.args.get("lang", "en")
    if "user_id" in session:
        user = User.query.get(session["user_id"])
        if user and user.has_access:
            return redirect(url_for("map_page"))
        return redirect(url_for("paywall_page"))
    return render_template("landing.html", lang=lang, t=lambda k: get_text(k, lang))


@app.route("/register", methods=["GET", "POST"])
def register_page():
    """User registration"""
    if request.method == "POST":
        data = request.get_json() or request.form
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        
        if not email or not password:
            return jsonify({"error": "Email and password required"}), 400
        
        if User.query.filter_by(email=email).first():
            return jsonify({"error": "Email already registered"}), 400
        
        user = User(
            email=email,
            password_hash=generate_password_hash(password),
            trial_start=datetime.utcnow(),
            subscription_status="trial",
        )
        db.session.add(user)
        db.session.commit()
        
        session["user_id"] = user.id
        log.info(f"New user: {email}")
        
        return jsonify({"redirect": url_for("map_page")})
    
    return render_template("register.html")


@app.route("/login", methods=["GET", "POST"])
def login_page():
    """User login"""
    if request.method == "POST":
        data = request.get_json() or request.form
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            return jsonify({"error": "Invalid credentials"}), 401
        
        session["user_id"] = user.id
        return jsonify({"redirect": url_for("map_page")})
    
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("index"))


# ---------------------------------------------------------------------------
# User Routes
# ---------------------------------------------------------------------------

@app.route("/map")
@access_required
def map_page():
    """Interactive map of sales"""
    user = User.query.get(session["user_id"])
    return render_template("map.html", user=user, lang=user.language)


@app.route("/api/sales")
@access_required
def api_sales():
    """
    Get sales near location.
    Query params: lat, lon, radius_miles (default 25)
    """
    lat = request.args.get("lat", type=float)
    lon = request.args.get("lon", type=float)
    radius = request.args.get("radius", 25, type=int)
    
    if not lat or not lon:
        return jsonify({"error": "lat and lon required"}), 400
    
    # Simple box search (for production, use PostGIS or proper geo queries)
    lat_delta = radius / 69.0  # Approx miles per degree latitude
    lon_delta = radius / (69.0 * abs(math.cos(math.radians(lat))))
    
    sales = Sale.query.filter(
        Sale.active == True,
        Sale.approved == True,
        Sale.lat.between(lat - lat_delta, lat + lat_delta),
        Sale.lon.between(lon - lon_delta, lon + lon_delta),
    ).limit(500).all()
    
    return jsonify([{
        "id": s.id,
        "title": s.title,
        "url": s.url,
        "address": s.address,
        "date": s.date_text,
        "description": s.description,
        "lat": s.lat,
        "lon": s.lon,
        "source": s.source,
    } for s in sales])


@app.route("/submit", methods=["GET", "POST"])
@login_required
def submit_sale():
    """Customer listing upload"""
    user = User.query.get(session["user_id"])
    
    if request.method == "POST":
        data = request.get_json() or request.form
        
        title = data.get("title", "").strip()
        address = data.get("address", "").strip()
        date_text = data.get("date", "").strip()
        description = data.get("description", "").strip()
        
        if not title or not address:
            return jsonify({"error": "Title and address required"}), 400
        
        # Geocode address
        from google_scraper import GoogleSaleScraper
        scraper = GoogleSaleScraper()
        lat, lon = scraper.geocode_address(address)
        
        sale = Sale(
            user_id=user.id,
            source="User",
            title=title,
            address=address,
            date_text=date_text,
            description=description,
            lat=lat,
            lon=lon,
            approved=False,  # Needs admin approval
        )
        db.session.add(sale)
        db.session.commit()
        
        log.info(f"User {user.email} submitted sale: {title}")
        return jsonify({"status": "submitted", "id": sale.id})
    
    return render_template("submit.html", user=user)


@app.route("/api/language", methods=["POST"])
@login_required
def set_language():
    """Change user language preference"""
    data = request.get_json()
    lang = data.get("lang", "en")
    
    if lang not in ["en", "es"]:
        return jsonify({"error": "Invalid language"}), 400
    
    user = User.query.get(session["user_id"])
    user.language = lang
    db.session.commit()
    
    return jsonify({"status": "updated", "lang": lang})


# ---------------------------------------------------------------------------
# Admin Routes
# ---------------------------------------------------------------------------

@app.route("/admin")
@admin_required
def admin_page():
    """Admin dashboard"""
    users = User.query.order_by(User.created_at.desc()).limit(100).all()
    runs = ScrapeRun.query.order_by(ScrapeRun.started_at.desc()).limit(20).all()
    pending_sales = Sale.query.filter_by(approved=False).all()
    config_items = AdminConfig.query.all()
    
    stats = {
        "total_sales": Sale.query.filter_by(active=True, approved=True).count(),
        "total_users": User.query.count(),
        "active_subs": User.query.filter_by(subscription_status="active").count(),
        "mrr": User.query.filter_by(subscription_status="active").count() * 7.99,
        "pending_sales": len(pending_sales),
    }
    
    return render_template("admin.html", 
        users=users, runs=runs, pending_sales=pending_sales,
        config=config_items, stats=stats
    )


@app.route("/admin/scrape", methods=["POST"])
@admin_required
def admin_scrape():
    """Trigger scrape job"""
    data = request.get_json()
    location = data.get("location", "Fort Myers, FL")
    radius = data.get("radius", 25)
    scraper_type = data.get("scraper", "google")  # google or yardsalesearch
    
    run = ScrapeRun(
        location=location,
        status="running",
        scraper_type=scraper_type,
    )
    db.session.add(run)
    db.session.commit()
    run_id = run.id
    
    def do_scrape():
        with app.app_context():
            try:
                if scraper_type == "google":
                    from google_scraper import GoogleSaleScraper
                    scraper = GoogleSaleScraper()
                    sales = scraper.scrape_location(location, radius)
                else:
                    from scrapers_final import scrape_all
                    sales = scrape_all()
                
                count = 0
                for sale_obj in sales:
                    # Check for duplicates
                    if sale_obj.url and Sale.query.filter_by(url=sale_obj.url).first():
                        continue
                    
                    # Geocode if missing
                    if not sale_obj.lat and sale_obj.address:
                        scraper = GoogleSaleScraper()
                        sale_obj.lat, sale_obj.lon = scraper.geocode_address(sale_obj.address)
                    
                    sale = Sale(
                        source=sale_obj.source,
                        title=sale_obj.title,
                        url=sale_obj.url,
                        address=sale_obj.address,
                        date_text=sale_obj.date_text,
                        description=sale_obj.description,
                        lat=sale_obj.lat,
                        lon=sale_obj.lon,
                        scrape_run_id=run_id,
                        approved=True,
                    )
                    db.session.add(sale)
                    count += 1
                
                db.session.commit()
                
                run_obj = ScrapeRun.query.get(run_id)
                run_obj.sale_count = count
                run_obj.status = "done"
                run_obj.finished_at = datetime.utcnow()
                db.session.commit()
                
                log.info(f"Scrape #{run_id} done: {count} sales")
                
            except Exception as e:
                log.error(f"Scrape #{run_id} failed: {e}")
                run_obj = ScrapeRun.query.get(run_id)
                run_obj.status = "error"
                run_obj.error_msg = str(e)[:500]
                run_obj.finished_at = datetime.utcnow()
                db.session.commit()
    
    threading.Thread(target=do_scrape, daemon=True).start()
    return jsonify({"status": "started", "run_id": run_id})


@app.route("/admin/sales/<int:sale_id>/approve", methods=["POST"])
@admin_required
def admin_approve_sale(sale_id):
    """Approve user-submitted sale"""
    sale = Sale.query.get_or_404(sale_id)
    sale.approved = True
    db.session.commit()
    return jsonify({"status": "approved"})


@app.route("/admin/config", methods=["GET", "POST"])
@admin_required
def admin_config():
    """Programmable backend settings"""
    if request.method == "POST":
        data = request.get_json()
        key = data.get("key")
        value = data.get("value")
        description = data.get("description", "")
        
        config = AdminConfig.query.filter_by(key=key).first()
        if config:
            config.value = value
            config.description = description
            config.updated_at = datetime.utcnow()
        else:
            config = AdminConfig(key=key, value=value, description=description)
            db.session.add(config)
        
        db.session.commit()
        return jsonify({"status": "updated"})
    
    config_items = AdminConfig.query.all()
    return jsonify([{
        "key": c.key,
        "value": c.value,
        "description": c.description,
        "updated": c.updated_at.isoformat(),
    } for c in config_items])


# ---------------------------------------------------------------------------
# Health & Init
# ---------------------------------------------------------------------------

@app.route("/health")
def health():
    return jsonify({
        "status": "ok",
        "users": User.query.count(),
        "sales": Sale.query.filter_by(active=True, approved=True).count(),
    })


def init_db():
    with app.app_context():
        db.create_all()
        
        # Create admin user
        admin_email = os.environ.get("ADMIN_EMAIL", "admin@garagesalemap.app")
        admin_password = os.environ.get("ADMIN_PASSWORD", "changeme123")
        
        if not User.query.filter_by(is_admin=True).first():
            admin = User(
                email=admin_email,
                password_hash=generate_password_hash(admin_password),
                is_admin=True,
                subscription_status="active",
            )
            db.session.add(admin)
            db.session.commit()
            log.info(f"Admin created: {admin_email}")


init_db()

if __name__ == "__main__":
    # Production: use gunicorn with multiple workers
    # gunicorn -w 4 -b 0.0.0.0:5000 app:app
    app.run(host="0.0.0.0", port=5000, debug=False)
