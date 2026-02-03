import csv
import hashlib
import io
import os
import secrets
import smtplib
import time
from datetime import datetime, timedelta
from email.message import EmailMessage

from dotenv import load_dotenv
from flask import (Flask, flash, redirect, render_template, request, send_file,
                   session, url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import re
import requests
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover - optional dependency for pdf export
    canvas = None
    letter = None


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))
DB_PATH = os.path.join(BASE_DIR, "app.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32).hex())
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["WTF_CSRF_TIME_LIMIT"] = 3600
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
if os.environ.get("SESSION_COOKIE_SECURE") == "1":
    app.config["SESSION_COOKIE_SECURE"] = True


db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"
csrf = CSRFProtect(app)
limiter = Limiter(get_remote_address, app=app, default_limits=["200 per day", "50 per hour"])


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="subscriber")
    credits = db.Column(db.Integer, default=10)
    plan = db.Column(db.String(100), default="Colorado Starter")
    fleet_size = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    is_verified = db.Column(db.Boolean, default=False)
    verification_token_hash = db.Column(db.String(64))
    verification_sent_at = db.Column(db.DateTime)

    def set_password(self, password: str) -> None:
        self.password_hash = generate_password_hash(password)

    def check_password(self, password: str) -> bool:
        return check_password_hash(self.password_hash, password)


class ReservationEmail(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guest_name = db.Column(db.String(255))
    booking_id = db.Column(db.String(100))
    plate = db.Column(db.String(20), nullable=False)
    start_date = db.Column(db.DateTime, nullable=False)
    end_date = db.Column(db.DateTime, nullable=False)
    state = db.Column(db.String(50), default="CO")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class Plan(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    price = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


class TollRecord(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    plate = db.Column(db.String(20), nullable=False)
    entry_time = db.Column(db.DateTime, nullable=False)
    exit_time = db.Column(db.DateTime, nullable=False)
    location = db.Column(db.String(255), default="E-470")
    amount = db.Column(db.Float, nullable=False)
    state = db.Column(db.String(50), default="CO")
    created_at = db.Column(db.DateTime, default=datetime.utcnow)


@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))


def ensure_user_columns():
    if not os.path.exists(DB_PATH):
        return
    with db.engine.connect() as conn:
        cols = {row[1] for row in conn.exec_driver_sql("PRAGMA table_info(user)").fetchall()}
        if "is_verified" not in cols:
            conn.exec_driver_sql("ALTER TABLE user ADD COLUMN is_verified BOOLEAN DEFAULT 0")
        if "verification_token_hash" not in cols:
            conn.exec_driver_sql("ALTER TABLE user ADD COLUMN verification_token_hash VARCHAR(64)")
        if "verification_sent_at" not in cols:
            conn.exec_driver_sql("ALTER TABLE user ADD COLUMN verification_sent_at DATETIME")


def initialize_database():
    db.create_all()
    ensure_user_columns()
    if Plan.query.count() == 0:
        db.session.add_all(
            [
                Plan(name="Colorado Starter", price=19),
                Plan(name="Front Range Pro", price=49),
                Plan(name="Nationwide Growth", price=99),
            ]
        )
        db.session.commit()
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if admin_email and admin_password and not User.query.filter_by(role="admin").first():
        admin = User(email=admin_email, role="admin", credits=999)
        admin.set_password(admin_password)
        token, token_hash = generate_verification_token()
        admin.verification_token_hash = token_hash
        admin.verification_sent_at = datetime.utcnow()
        db.session.add(admin)
        db.session.commit()
        send_verification_email(admin.email, token)


@app.route("/")

def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("home.html")


@app.route("/signup", methods=["GET", "POST"])
@limiter.limit("5 per minute")

def signup():
    if request.method == "POST":
        if request.form.get("company"):
            flash("Signup failed.", "error")
            return redirect(url_for("signup"))
        start_ts = session.pop("signup_ts", None)
        now_ts = time.time()
        if not start_ts or now_ts - start_ts < 3 or now_ts - start_ts > 3600:
            flash("Signup failed. Please try again.", "error")
            return redirect(url_for("signup"))

        turnstile_secret = os.environ.get("TURNSTILE_SECRET_KEY")
        turnstile_response = request.form.get("cf-turnstile-response")
        if not turnstile_secret:
            flash("Captcha is not configured.", "error")
            return redirect(url_for("signup"))
        verify = requests.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data={
                "secret": turnstile_secret,
                "response": turnstile_response,
                "remoteip": request.remote_addr,
            },
            timeout=5,
        )
        if not verify.ok or not verify.json().get("success"):
            flash("Captcha verification failed.", "error")
            return redirect(url_for("signup"))

        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        plan = request.form.get("plan", "Colorado Starter")
        fleet_size = int(request.form.get("fleet_size", 1))
        if not Plan.query.filter_by(name=plan).first():
            plan = "Colorado Starter"
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return redirect(url_for("signup"))
        if not is_strong_password(password):
            flash("Password must be at least 8 characters and include a letter and a number.", "error")
            return redirect(url_for("signup"))
        user = User(email=email, plan=plan, fleet_size=fleet_size, credits=10)
        user.set_password(password)
        token, token_hash = generate_verification_token()
        user.verification_token_hash = token_hash
        user.verification_sent_at = datetime.utcnow()
        db.session.add(user)
        db.session.commit()
        send_verification_email(user.email, token)
        flash("Check your email to verify your account before logging in.", "success")
        return redirect(url_for("login"))
    session["signup_ts"] = time.time()
    plans = Plan.query.order_by(Plan.price.asc()).all()
    return render_template(
        "signup.html",
        plans=plans,
        turnstile_site_key=os.environ.get("TURNSTILE_SITE_KEY"),
    )


@app.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")

def login():
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
            if not user.is_verified:
                token, token_hash = generate_verification_token()
                user.verification_token_hash = token_hash
                user.verification_sent_at = datetime.utcnow()
                db.session.commit()
                send_verification_email(user.email, token)
                flash("Please verify your email. We just sent you a new verification link.", "error")
                return render_template("login.html")
            login_user(user)
            return redirect(url_for("dashboard"))
        flash("Invalid credentials.", "error")
    return render_template("login.html")


@app.route("/logout")
@login_required

def logout():
    logout_user()
    return redirect(url_for("home"))


@app.route("/dashboard")
@login_required

def dashboard():
    start = request.args.get("start")
    end = request.args.get("end")
    query = ReservationEmail.query
    if start:
        try:
            start_date = datetime.fromisoformat(start)
            query = query.filter(ReservationEmail.start_date >= start_date)
        except ValueError:
            flash("Invalid start date filter.", "error")
    if end:
        try:
            end_date = datetime.fromisoformat(end) + timedelta(days=1)
            query = query.filter(ReservationEmail.end_date < end_date)
        except ValueError:
            flash("Invalid end date filter.", "error")
    reservations = query.order_by(ReservationEmail.start_date.desc()).all()

    tolls = TollRecord.query.order_by(TollRecord.entry_time.desc()).all()
    matched = []
    for reservation in reservations:
        matches = [
            toll
            for toll in tolls
            if toll.plate.lower() == reservation.plate.lower()
            and reservation.start_date <= toll.entry_time <= reservation.end_date
        ]
        matched.append(
            {
                "reservation": reservation,
                "tolls": matches,
                "total": sum(t.amount for t in matches),
            }
        )

    return render_template(
        "dashboard.html",
        reservations=reservations,
        matched=matched,
        tolls=tolls,
    )


@app.route("/ingest-email", methods=["POST"])
@login_required

def ingest_email():
    email_body = request.form.get("email_body", "")
    emails = [chunk for chunk in email_body.split("-----") if chunk.strip()]
    ingested = 0
    for chunk in emails:
        parsed = parse_reservation_email(chunk)
        if parsed:
            reservation = ReservationEmail(**parsed)
            db.session.add(reservation)
            ingested += 1
    db.session.commit()
    flash(f"Ingested {ingested} reservation email(s).", "success")
    return redirect(url_for("dashboard"))


@app.route("/sync-tolls", methods=["POST"])
@login_required

def sync_tolls():
    sample = TollRecord(
        plate=request.form.get("plate", "CO1234"),
        entry_time=datetime.utcnow(),
        exit_time=datetime.utcnow(),
        location="E-470 / ExpressToll",
        amount=6.50,
        state="CO",
    )
    db.session.add(sample)
    db.session.commit()
    flash("TollGuru sync queued. Added a Colorado sample toll.", "success")
    return redirect(url_for("dashboard"))


@app.route("/buy-credits", methods=["POST"])
@login_required

def buy_credits():
    added = int(request.form.get("credits", 5))
    current_user.credits += added
    db.session.commit()
    flash(f"Added {added} credits to your account.", "success")
    return redirect(url_for("dashboard"))


@app.route("/export/csv")
@login_required

def export_csv():
    if not consume_credit():
        return redirect(url_for("dashboard"))
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Booking", "Plate", "Start", "End", "Toll total"])
    for item in get_matched_reservations():
        reservation = item["reservation"]
        writer.writerow(
            [
                reservation.booking_id,
                reservation.plate,
                reservation.start_date.strftime("%Y-%m-%d"),
                reservation.end_date.strftime("%Y-%m-%d"),
                f"{item['total']:.2f}",
            ]
        )
    output.seek(0)
    return send_file(
        io.BytesIO(output.getvalue().encode("utf-8")),
        mimetype="text/csv",
        as_attachment=True,
        download_name="turo-tolls.csv",
    )


@app.route("/export/pdf")
@login_required

def export_pdf():
    if not consume_credit():
        return redirect(url_for("dashboard"))
    if canvas is None:
        flash("PDF export requires reportlab installed.", "error")
        return redirect(url_for("dashboard"))
    buffer = io.BytesIO()
    pdf = canvas.Canvas(buffer, pagesize=letter)
    pdf.setFont("Helvetica-Bold", 14)
    pdf.drawString(50, 750, "Turo Toll Reconciliation Invoice")
    pdf.setFont("Helvetica", 10)
    y = 720
    for item in get_matched_reservations():
        reservation = item["reservation"]
        line = (
            f"{reservation.booking_id} | {reservation.plate} | "
            f"{reservation.start_date:%Y-%m-%d} - "
            f"{reservation.end_date:%Y-%m-%d} | ${item['total']:.2f}"
        )
        pdf.drawString(50, y, line)
        y -= 16
        if y < 80:
            pdf.showPage()
            y = 750
    pdf.showPage()
    pdf.save()
    buffer.seek(0)
    return send_file(
        buffer,
        mimetype="application/pdf",
        as_attachment=True,
        download_name="turo-invoice.pdf",
    )


@app.route("/admin", methods=["GET", "POST"])
@login_required

def admin():
    if current_user.role != "admin":
        flash("Admin access required.", "error")
        return redirect(url_for("dashboard"))
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        role = request.form.get("role", "subscriber")
        plan = request.form.get("plan", "Colorado Starter")
        fleet_size = int(request.form.get("fleet_size", 1))
        credits = int(request.form.get("credits", 10))
        if not Plan.query.filter_by(name=plan).first():
            plan = "Colorado Starter"
        if User.query.filter_by(email=email).first():
            flash("Email already exists.", "error")
        else:
            if not is_strong_password(password):
                flash("Password must be at least 8 characters and include a letter and a number.", "error")
                return redirect(url_for("admin"))
            user = User(
                email=email,
                role=role,
                plan=plan,
                fleet_size=fleet_size,
                credits=credits,
            )
            user.set_password(password)
            token, token_hash = generate_verification_token()
            user.verification_token_hash = token_hash
            user.verification_sent_at = datetime.utcnow()
            db.session.add(user)
            db.session.commit()
            send_verification_email(user.email, token)
            flash("User created.", "success")
    users = User.query.order_by(User.created_at.desc()).all()
    plans = Plan.query.order_by(Plan.price.asc()).all()
    return render_template(
        "admin.html",
        users=users,
        plans=plans,
        main_class="container-fluid",
    )


@app.route("/admin/update/<int:user_id>", methods=["POST"])
@login_required
def admin_update_user(user_id):
    if current_user.role != "admin":
        flash("Admin access required.", "error")
        return redirect(url_for("dashboard"))
    user = User.query.get_or_404(user_id)

    email = request.form.get("email", "").lower().strip()
    role = request.form.get("role", "subscriber")
    plan = request.form.get("plan", "Colorado Starter")
    try:
        fleet_size = int(request.form.get("fleet_size", user.fleet_size))
        credits = int(request.form.get("credits", user.credits))
    except ValueError:
        flash("Fleet size and credits must be numbers.", "error")
        return redirect(url_for("admin"))

    if not email:
        flash("Email is required.", "error")
        return redirect(url_for("admin"))
    email_changed = email != user.email
    if email_changed and User.query.filter_by(email=email).first():
        flash("Email already exists.", "error")
        return redirect(url_for("admin"))

    user.email = email
    if not Plan.query.filter_by(name=plan).first():
        plan = "Colorado Starter"
    user.role = role
    user.plan = plan
    user.fleet_size = max(fleet_size, 1)
    user.credits = max(credits, 0)
    if email_changed:
        token, token_hash = generate_verification_token()
        user.is_verified = False
        user.verification_token_hash = token_hash
        user.verification_sent_at = datetime.utcnow()
        send_verification_email(user.email, token)
    db.session.commit()
    flash("User updated.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/delete/<int:user_id>", methods=["POST"])
@login_required
def admin_delete_user(user_id):
    if current_user.role != "admin":
        flash("Admin access required.", "error")
        return redirect(url_for("dashboard"))
    if current_user.id == user_id:
        flash("You cannot delete your own admin account.", "error")
        return redirect(url_for("admin"))
    user = User.query.get_or_404(user_id)
    db.session.delete(user)
    db.session.commit()
    flash("User deleted.", "success")
    return redirect(url_for("admin"))


@app.route("/admin/plan/<int:plan_id>", methods=["POST"])
@login_required
def admin_update_plan(plan_id):
    if current_user.role != "admin":
        flash("Admin access required.", "error")
        return redirect(url_for("dashboard"))
    plan = Plan.query.get_or_404(plan_id)
    try:
        price = int(request.form.get("price", plan.price))
    except ValueError:
        flash("Plan price must be a whole number.", "error")
        return redirect(url_for("admin"))
    if price < 0:
        flash("Plan price cannot be negative.", "error")
        return redirect(url_for("admin"))
    plan.price = price
    db.session.commit()
    flash("Plan updated.", "success")
    return redirect(url_for("admin"))


@app.route("/verify-email")
def verify_email():
    token = request.args.get("token", "")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    user = User.query.filter_by(verification_token_hash=token_hash).first()
    if not user:
        flash("Invalid or expired verification link.", "error")
        return redirect(url_for("login"))
    if user.is_verified:
        flash("Account already verified. Please log in.", "success")
        return redirect(url_for("login"))
    if user.verification_sent_at and datetime.utcnow() - user.verification_sent_at > timedelta(hours=24):
        flash("Verification link expired. Please contact support.", "error")
        return redirect(url_for("login"))
    user.is_verified = True
    user.verification_token_hash = None
    user.verification_sent_at = None
    db.session.commit()
    flash("Email verified. You can now log in.", "success")
    return redirect(url_for("login"))


def generate_verification_token():
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    return token, token_hash


def send_verification_email(to_email: str, token: str) -> None:
    verify_url = url_for("verify_email", token=token, _external=True)
    subject = "Verify your Turo Toll Reconcile account"
    body = (
        "Please verify your account by clicking the link below:\n\n"
        f"{verify_url}\n\n"
        "This link expires in 24 hours."
    )
    send_email(to_email, subject, body)


def send_email(to_email: str, subject: str, body: str) -> None:
    host = os.environ.get("SMTP_HOST")
    user = os.environ.get("SMTP_USER")
    password = os.environ.get("SMTP_PASSWORD")
    port = int(os.environ.get("SMTP_PORT", "587"))
    use_tls = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    from_email = os.environ.get("SMTP_FROM", user or "no-reply@example.com")

    if not host or not user or not password:
        print(f"[DEV EMAIL] To: {to_email}\nSubject: {subject}\n\n{body}\n")
        return

    message = EmailMessage()
    message["From"] = from_email
    message["To"] = to_email
    message["Subject"] = subject
    message.set_content(body)

    with smtplib.SMTP(host, port) as server:
        if use_tls:
            server.starttls()
        server.login(user, password)
        server.send_message(message)


def is_strong_password(password: str) -> bool:
    if len(password) < 8:
        return False
    if not re.search(r"[A-Za-z]", password):
        return False
    if not re.search(r"\d", password):
        return False
    return True



def parse_reservation_email(body: str):
    booking_id = extract_value(body, "Booking ID") or extract_value(body, "Reservation")
    guest_name = extract_value(body, "Guest")
    plate = extract_value(body, "Plate") or "CO1234"
    start = extract_value(body, "Start") or extract_value(body, "Pickup")
    end = extract_value(body, "End") or extract_value(body, "Return")
    try:
        start_date = datetime.fromisoformat(start)
        end_date = datetime.fromisoformat(end)
    except (TypeError, ValueError):
        return None
    return {
        "guest_name": guest_name,
        "booking_id": booking_id,
        "plate": plate,
        "start_date": start_date,
        "end_date": end_date,
        "state": "CO",
    }



def extract_value(body: str, label: str):
    for line in body.splitlines():
        if line.lower().startswith(label.lower()):
            parts = line.split(":", 1)
            if len(parts) == 2:
                return parts[1].strip()
    return None



def get_matched_reservations():
    reservations = ReservationEmail.query.order_by(ReservationEmail.start_date.desc()).all()
    tolls = TollRecord.query.order_by(TollRecord.entry_time.desc()).all()
    matched = []
    for reservation in reservations:
        matches = [
            toll
            for toll in tolls
            if toll.plate.lower() == reservation.plate.lower()
            and reservation.start_date <= toll.entry_time <= reservation.end_date
        ]
        matched.append(
            {
                "reservation": reservation,
                "tolls": matches,
                "total": sum(t.amount for t in matches),
            }
        )
    return matched



def consume_credit():
    if current_user.credits <= 0:
        flash("You are out of credits. Please purchase more to export.", "error")
        return False
    current_user.credits -= 1
    db.session.commit()
    flash("Export queued. One credit used.", "success")
    return True


with app.app_context():
    initialize_database()

if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG") == "1"
    app.run(debug=debug_mode, host="0.0.0.0", port=5000)
