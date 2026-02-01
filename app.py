import csv
import io
import os
from datetime import datetime, timedelta

from flask import (Flask, flash, redirect, render_template, request, send_file,
                   url_for)
from flask_login import (LoginManager, UserMixin, current_user, login_required,
                         login_user, logout_user)
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import check_password_hash, generate_password_hash

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
except ImportError:  # pragma: no cover - optional dependency for pdf export
    canvas = None
    letter = None


BASE_DIR = os.path.abspath(os.path.dirname(__file__))
DB_PATH = os.path.join(BASE_DIR, "app.db")

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", os.urandom(32).hex())
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{DB_PATH}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False


db = SQLAlchemy(app)
login_manager = LoginManager(app)
login_manager.login_view = "login"


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(50), default="subscriber")
    credits = db.Column(db.Integer, default=10)
    plan = db.Column(db.String(100), default="Colorado Starter")
    fleet_size = db.Column(db.Integer, default=1)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

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


def initialize_database():
    db.create_all()
    admin_email = os.environ.get("ADMIN_EMAIL")
    admin_password = os.environ.get("ADMIN_PASSWORD")
    if admin_email and admin_password and not User.query.filter_by(role="admin").first():
        admin = User(email=admin_email, role="admin", credits=999)
        admin.set_password(admin_password)
        db.session.add(admin)
        db.session.commit()


@app.route("/")

def home():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("home.html")


@app.route("/signup", methods=["GET", "POST"])

def signup():
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        plan = request.form.get("plan", "Colorado Starter")
        fleet_size = int(request.form.get("fleet_size", 1))
        if User.query.filter_by(email=email).first():
            flash("Email already registered.", "error")
            return redirect(url_for("signup"))
        user = User(email=email, plan=plan, fleet_size=fleet_size, credits=10)
        user.set_password(password)
        db.session.add(user)
        db.session.commit()
        login_user(user)
        return redirect(url_for("dashboard"))
    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])

def login():
    if request.method == "POST":
        email = request.form.get("email", "").lower().strip()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if user and user.check_password(password):
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
        if User.query.filter_by(email=email).first():
            flash("Email already exists.", "error")
        else:
            user = User(
                email=email,
                role=role,
                plan=plan,
                fleet_size=fleet_size,
                credits=credits,
            )
            user.set_password(password)
            db.session.add(user)
            db.session.commit()
            flash("User created.", "success")
    users = User.query.order_by(User.created_at.desc()).all()
    return render_template("admin.html", users=users)



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
