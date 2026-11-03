"""Contractual Employees Management Software.
Developed by IT Center, Wagon Repair Shop, Raipur, SECR.

Manages contractor firms, their employees (profile, Aadhaar, photo, police
verification, qualification documents, skill category) and daily attendance.

Run:  python app.py   then open http://localhost:5000
"""
import base64
import calendar
import csv
import hmac
import io
import math
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_from_directory, session, url_for)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Set CEMS_DATA_DIR to keep the database and uploads somewhere other than ./data.
DATA_DIR = os.environ.get("CEMS_DATA_DIR") or os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "contract_employees.db")
KEY_FILE = os.path.join(DATA_DIR, "secret.key")
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
ALLOWED_EXT = {"jpg", "jpeg", "png", "webp", "pdf"}
IMAGE_EXT = {"jpg", "jpeg", "png", "webp"}

APP_NAME = "Contractual Employees Management Software"
DEVELOPED_BY = "IT Center / Wagon Repair Shop / Raipur / SECR"
WORKSHOP = "Wagon Repair Shop, Raipur (SECR)"

SKILL_CATEGORIES = ["Skilled", "Semi-skilled", "Unskilled"]
ATTENDANCE_CODES = {
    "P": "Present",
    "A": "Absent",
    "H": "Half Day",
    "L": "Leave",
    "W": "Weekly Off",
}
DOC_TYPES = ["Qualification", "Experience", "Medical", "ESI / PF", "Bank Passbook", "Other"]

MIN_PASSWORD = 8
LOGIN_MAX_FAILS = 5              # failed logins from one computer ...
LOGIN_LOCK_SECONDS = 15 * 60     # ... within this time lock it out for this long

# The data folder (database + secret.key) was published in the public GitHub repository in
# November 2026. The first start of this version replaces the secret key, which logs everyone
# out and makes any copied login cookie useless, and makes every existing account choose a new
# password, since the password hashes were published too. It runs once per database.
SECURITY_RESET_KEY = "security_reset_2026_11"

app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=25 * 1024 * 1024,         # 25 MB per request
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),  # logged out after 8 hours without use
)


def _load_secret_key():
    os.makedirs(DATA_DIR, exist_ok=True)
    if not os.path.exists(KEY_FILE):
        fd = os.open(KEY_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w") as f:
            f.write(secrets.token_hex(32))
    with open(KEY_FILE) as f:
        return f.read().strip()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS firms (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT UNIQUE NOT NULL,
    owner_name TEXT,
    contact_person TEXT,
    mobile TEXT,
    email TEXT,
    address TEXT,
    gst_no TEXT,
    pan_no TEXT,
    labour_licence_no TEXT,
    work_order_no TEXT,
    work_description TEXT,
    contract_start TEXT,
    contract_end TEXT,
    status TEXT DEFAULT 'Active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    firm_id INTEGER NOT NULL REFERENCES firms(id),
    emp_code TEXT UNIQUE,
    name TEXT NOT NULL,
    father_name TEXT,
    dob TEXT,
    gender TEXT,
    aadhaar_no TEXT UNIQUE,
    aadhaar_file TEXT,
    photo_file TEXT,
    mobile TEXT,
    address TEXT,
    pv_done INTEGER DEFAULT 0,
    pv_cert_no TEXT,
    pv_valid_upto TEXT,
    pv_file TEXT,
    qualification TEXT,
    skill_category TEXT,
    designation TEXT,
    date_of_joining TEXT,
    status TEXT DEFAULT 'Active',
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    doc_type TEXT,
    title TEXT,
    filename TEXT NOT NULL,
    uploaded_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS attendance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    att_date TEXT NOT NULL,
    status TEXT NOT NULL,
    remarks TEXT,
    UNIQUE(employee_id, att_date)
);
CREATE TABLE IF NOT EXISTS wage_rates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    skill_category TEXT NOT NULL,
    daily_rate REAL NOT NULL,
    effective_from TEXT NOT NULL,
    notes TEXT,
    UNIQUE(skill_category, effective_from)
);
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS payroll_adjustments (
    employee_id INTEGER NOT NULL REFERENCES employees(id) ON DELETE CASCADE,
    month TEXT NOT NULL,
    allowance REAL DEFAULT 0,
    deduction REAL DEFAULT 0,
    remarks TEXT,
    PRIMARY KEY (employee_id, month)
);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    user_id INTEGER,
    username TEXT,
    ip TEXT,
    action TEXT NOT NULL,
    details TEXT
);
CREATE INDEX IF NOT EXISTS idx_emp_firm ON employees(firm_id);
CREATE INDEX IF NOT EXISTS idx_att_date ON attendance(att_date);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_log(at);
"""

# Columns added after the first release; init_db adds them to existing databases.
EXTRA_COLUMNS = {
    "users": {"is_admin": "INTEGER DEFAULT 0", "must_change_password": "INTEGER DEFAULT 0"},
    "employees": {
        "uan_no": "TEXT", "esic_no": "TEXT", "bank_name": "TEXT", "bank_account": "TEXT",
        "ifsc": "TEXT", "daily_wage": "REAL",
        # ID card cum gate pass
        "blood_group": "TEXT", "pincode": "TEXT", "gate_pass_no": "TEXT",
        "gp_valid_from": "TEXT", "gp_valid_upto": "TEXT",
        # appointment letter
        "appt_date": "TEXT", "appt_other_allowance": "TEXT", "appt_duties": "TEXT", "appt_other_info": "TEXT",
    },
    "firms": {"pincode": "TEXT", "lin_no": "TEXT", "registration_no": "TEXT", "loa_date": "TEXT"},
    "wage_rates": {"da_rate": "REAL DEFAULT 0"},          # DA/VDA part included in daily_rate
    "payroll_adjustments": {"overtime": "REAL DEFAULT 0"},
}
BLOOD_GROUPS = ["A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"]

PAYROLL_DEFAULTS = {
    "pay_weekly_off": "0",      # pay W days
    "pay_leave": "0",           # pay L days
    "pf_rate": "12",            # employee EPF %
    "pf_employer_rate": "13",   # employer EPF + EPS + EDLI/admin %
    "pf_ceiling": "15000",      # monthly wage ceiling for PF (0 = no ceiling)
    "esi_rate": "0.75",         # employee ESI %
    "esi_employer_rate": "3.25",
    "esi_limit": "21000",       # ESI applies when monthly gross <= this
}


# ---------------------------------------------------------------- database

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH, timeout=10)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    db = sqlite3.connect(DB_PATH)
    try:
        db.executescript(SCHEMA)
        for table, columns in EXTRA_COLUMNS.items():
            existing = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
            for col, typ in columns.items():
                if col not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        db.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                       PAYROLL_DEFAULTS.items())
        # Keep at least one administrator. Before user roles existed every account could do
        # everything; the oldest account (the one made at first-time setup) becomes the admin.
        if not db.execute("SELECT 1 FROM users WHERE is_admin=1").fetchone():
            db.execute("UPDATE users SET is_admin=1 WHERE id=(SELECT MIN(id) FROM users)")
        reset = not db.execute("SELECT 1 FROM settings WHERE key=?", (SECURITY_RESET_KEY,)).fetchone()
        if reset:
            db.execute("UPDATE users SET must_change_password=1")
            db.execute("INSERT INTO settings (key, value) VALUES (?, ?)",
                       (SECURITY_RESET_KEY, datetime.now().isoformat(timespec="seconds")))
        db.commit()
    finally:
        db.close()
    if reset and os.path.exists(KEY_FILE):
        os.remove(KEY_FILE)  # _load_secret_key() makes a new one


# ---------------------------------------------------------------- helpers

_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6], [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8], [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2], [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4], [9, 8, 7, 6, 5, 4, 3, 2, 1, 0]]
_VERHOEFF_P = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9], [1, 5, 7, 6, 2, 8, 3, 0, 9, 4],
    [5, 8, 0, 3, 7, 9, 6, 1, 4, 2], [8, 9, 1, 6, 0, 4, 3, 5, 2, 7],
    [9, 4, 5, 3, 1, 2, 6, 8, 7, 0], [4, 2, 8, 6, 5, 7, 3, 9, 0, 1],
    [2, 7, 9, 3, 8, 0, 6, 4, 1, 5], [7, 0, 4, 6, 9, 1, 3, 2, 5, 8]]


def valid_aadhaar(number):
    """12 digits, not starting with 0/1, passing the Verhoeff checksum UIDAI uses."""
    if not re.fullmatch(r"[2-9]\d{11}", number):
        return False
    c = 0
    for i, digit in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(digit)]]
    return c == 0


def clean(value):
    value = (value or "").strip()
    return value or None


def ext_of(filename):
    return filename.rsplit(".", 1)[-1].lower() if "." in filename else ""


def save_upload(file_storage, subdir):
    """Save an uploaded file; returns stored relative path, or None if no file."""
    if not file_storage or not file_storage.filename:
        return None
    ext = ext_of(file_storage.filename)
    if ext not in ALLOWED_EXT:
        raise ValueError(f"File type .{ext} not allowed. Use JPG, PNG, WEBP or PDF.")
    folder = os.path.join(UPLOAD_DIR, subdir)
    os.makedirs(folder, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(os.path.join(folder, name))
    return f"{subdir}/{name}"


def save_data_url(data_url, subdir):
    """Save a camera capture sent as a base64 data URL."""
    m = re.fullmatch(r"data:image/(jpeg|png|webp);base64,(.+)", data_url or "", re.S)
    if not m:
        return None
    ext = "jpg" if m.group(1) == "jpeg" else m.group(1)
    folder = os.path.join(UPLOAD_DIR, subdir)
    os.makedirs(folder, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    with open(os.path.join(folder, name), "wb") as f:
        f.write(base64.b64decode(m.group(2)))
    return f"{subdir}/{name}"


def delete_upload(rel_path):
    if not rel_path:
        return
    full = os.path.normpath(os.path.join(UPLOAD_DIR, rel_path))
    if full.startswith(os.path.normpath(UPLOAD_DIR)) and os.path.isfile(full):
        os.remove(full)


def parse_date(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (TypeError, ValueError):
        return None


_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def csv_safe(row):
    """Quote text that Excel would otherwise run as a formula (e.g. a name typed as =HYPERLINK(...))."""
    return ["'" + v if isinstance(v, str) and v.startswith(_FORMULA_START) and v != "-" else v for v in row]


def csv_response(rows, filename):
    out = io.StringIO()
    w = csv.writer(out)
    for row in rows:
        w.writerow(csv_safe(row))
    # The byte-order mark makes Excel read the file as UTF-8 (Hindi text, the ₹ sign).
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


def back_url(default="dashboard"):
    """The page the user came from, if it is on this site; otherwise the default page."""
    ref = request.referrer or ""
    return ref if ref.startswith(request.host_url) else url_for(default)


@app.template_filter("mask_aadhaar")
def mask_aadhaar(number):
    if not number:
        return "-"
    return f"XXXX XXXX {number[-4:]}"


@app.template_filter("fmt_aadhaar")
def fmt_aadhaar(number):
    if not number:
        return "-"
    return " ".join(number[i:i + 4] for i in range(0, 12, 4))


@app.template_filter("dmy")
def dmy(value):
    d = parse_date(value) if isinstance(value, str) else value
    return d.strftime("%d-%m-%Y") if d else "-"


@app.template_filter("is_image")
def is_image(path):
    return bool(path) and ext_of(path) in IMAGE_EXT


def pv_state(emp):
    """Police verification state: 'pending', 'expired', 'expiring' (<=30 days) or 'valid'."""
    if not emp["pv_done"]:
        return "pending"
    valid = parse_date(emp["pv_valid_upto"])
    if valid is None:
        return "valid"
    today = date.today()
    if valid < today:
        return "expired"
    if valid <= today + timedelta(days=30):
        return "expiring"
    return "valid"


_ICONS = {
    "dashboard": '<rect width="7" height="9" x="3" y="3" rx="1"/><rect width="7" height="5" x="14" y="3" rx="1"/>'
                 '<rect width="7" height="9" x="14" y="12" rx="1"/><rect width="7" height="5" x="3" y="16" rx="1"/>',
    "building": '<path d="M6 22V4a2 2 0 0 1 2-2h8a2 2 0 0 1 2 2v18Z"/><path d="M6 12H4a2 2 0 0 0-2 2v6a2 2 0 0 0 2 2h2"/>'
                '<path d="M18 9h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2h-2"/><path d="M10 6h4M10 10h4M10 14h4M10 18h4"/>',
    "users": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/>'
             '<path d="M22 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>',
    "user-check": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="m16 11 2 2 4-4"/>',
    "user-x": '<path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="m17 8 5 5M22 8l-5 5"/>',
    "calendar": '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/>',
    "calendar-check": '<rect width="18" height="18" x="3" y="4" rx="2"/><path d="M16 2v4M8 2v4M3 10h18"/><path d="m9 16 2 2 4-4"/>',
    "chart": '<path d="M3 3v18h18"/><path d="M18 17V9M13 17V5M8 17v-3"/>',
    "id-card": '<rect width="20" height="14" x="2" y="5" rx="2"/><circle cx="8" cy="12" r="2"/><path d="M13 10h5M13 14h3"/>',
    "file-text": '<path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v4a2 2 0 0 0 2 2h4"/>'
                 '<path d="M10 9H8M16 13H8M16 17H8"/>',
    "wallet": '<path d="M21 12V7H5a2 2 0 0 1 0-4h14v4"/><path d="M3 5v14a2 2 0 0 0 2 2h16v-5"/><path d="M18 12a2 2 0 0 0 0 4h4v-4Z"/>',
    "shield": '<path d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "logout": '<path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="m16 17 5-5-5-5M21 12H9"/>',
    "menu": '<path d="M4 6h16M4 12h16M4 18h16"/>',
    "search": '<circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/>',
    "plus": '<path d="M5 12h14M12 5v14"/>',
    "train": '<path d="M8 3.1V7a4 4 0 0 0 8 0V3.1"/><path d="m9 15-1-1M15 15l1-1"/>'
             '<path d="M9 19c-2.8 0-5-2.2-5-5v-4a8 8 0 0 1 16 0v4c0 2.8-2.2 5-5 5Z"/><path d="m8 19-2 3M16 19l2 3"/>',
}


def icon(name, size=18):
    return Markup(f'<svg class="icon" width="{size}" height="{size}" viewBox="0 0 24 24" fill="none" '
                  f'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
                  f'aria-hidden="true">{_ICONS[name]}</svg>')


app.jinja_env.globals.update(icon=icon, BLOOD_GROUPS=BLOOD_GROUPS,APP_NAME=APP_NAME,DEVELOPED_BY=DEVELOPED_BY, WORKSHOP=WORKSHOP,
                             pv_state=pv_state,SKILL_CATEGORIES=SKILL_CATEGORIES,
                             ATTENDANCE_CODES=ATTENDANCE_CODES, DOC_TYPES=DOC_TYPES)


# ---------------------------------------------------------------- auth, roles, csrf & audit log

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.get("user"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    """Administrators only: user accounts, audit log, deleting firms and employees."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not g.get("user"):
            return redirect(url_for("login", next=request.path))
        if not g.user["is_admin"]:
            return not_admin()
        return view(*args, **kwargs)
    return wrapped


def not_admin():
    flash("Only an administrator can do that.", "error")
    return redirect(back_url())


def auth_tag(password_hash):
    """Ties a login to the password it was made with, so changing or resetting a password
    (or deleting the account) ends every other login of that account."""
    return hmac.new(app.secret_key.encode(), password_hash.encode(), "sha256").hexdigest()[:24]


def password_problem(password, confirm=None):
    if len(password) < MIN_PASSWORD:
        return f"The password must be at least {MIN_PASSWORD} characters."
    if confirm is not None and password != confirm:
        return "The passwords do not match."
    return None


def audit(action, details="", user=None):
    """Record who did what, and when. Never put Aadhaar, bank details or passwords in `details`."""
    user = user or g.get("user")
    db = get_db()
    db.execute("INSERT INTO audit_log (at, user_id, username, ip, action, details) VALUES (?, ?, ?, ?, ?, ?)",
               (datetime.now().strftime("%Y-%m-%d %H:%M:%S"), user["id"] if user else None,
                user["username"] if user else None, request.remote_addr, action, details or None))
    db.commit()


def changed_fields(old, new, fields):
    """Names of the fields whose value differs (for the audit log; values are not recorded)."""
    def norm(v):
        return "" if v is None else str(v)
    return [f for f in fields if norm(old[f]) != norm(new[f])]


@app.before_request
def guard():
    if request.endpoint == "static":
        return
    db = get_db()
    has_user = db.execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if not has_user and request.endpoint != "setup":
        return redirect(url_for("setup"))
    if request.method == "POST":
        token = request.form.get("csrf_token") or ""
        if not token or not hmac.compare_digest(token.encode(), str(session.get("csrf_token", "")).encode()):
            abort(400, "Form expired. Please go back, reload the page and try again.")
    g.user = None
    if session.get("user_id"):
        user = db.execute("SELECT * FROM users WHERE id=?", (session["user_id"],)).fetchone()
        if user is None or not hmac.compare_digest(str(session.get("auth", "")).encode(),
                                                   auth_tag(user["password_hash"]).encode()):
            session.clear()  # the account was deleted or its password changed: log in again
            return
        g.user = user
        if user["must_change_password"] and request.endpoint not in ("account", "logout"):
            return redirect(url_for("account"))


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    return resp


@app.context_processor
def inject_globals():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    user = g.get("user")
    return {"csrf_token": session["csrf_token"], "today": date.today().isoformat(),
            "current_user": user, "is_admin": bool(user and user["is_admin"])}


@app.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return redirect(url_for("login"))
    if request.method == "POST":
        username = clean(request.form.get("username"))
        password = request.form.get("password") or ""
        problem = password_problem(password, request.form.get("confirm") or "") if username else "Enter a username."
        if problem:
            flash(problem, "error")
        else:
            cur = db.execute("INSERT INTO users (username, password_hash, is_admin) VALUES (?, ?, 1)",
                             (username, generate_password_hash(password)))
            db.commit()
            audit("Admin account created", "First-time setup", user={"id": cur.lastrowid, "username": username})
            flash("Admin account created. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("setup.html")


_login_fails = {}  # computer (IP address) -> times of its recent failed logins
_login_fails_lock = threading.Lock()


def login_wait_seconds(ip):
    """How long this computer must wait before it may try to log in again (0 = it may now)."""
    now = time.monotonic()
    with _login_fails_lock:
        fails = [t for t in _login_fails.get(ip, []) if now - t < LOGIN_LOCK_SECONDS]
        if fails:
            _login_fails[ip] = fails
        else:
            _login_fails.pop(ip, None)
        if len(fails) < LOGIN_MAX_FAILS:
            return 0
        return LOGIN_LOCK_SECONDS - (now - fails[-LOGIN_MAX_FAILS])


def record_login_fail(ip):
    with _login_fails_lock:
        _login_fails.setdefault(ip, []).append(time.monotonic())


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = request.remote_addr or "?"
        username = (request.form.get("username") or "").strip()
        wait = login_wait_seconds(ip)
        if wait:
            flash(f"Too many failed logins from this computer. Try again in {math.ceil(wait / 60)} minute(s).",
                  "error")
            return render_template("login.html"), 429
        user = get_db().execute("SELECT * FROM users WHERE username = ?", (username,)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            with _login_fails_lock:
                _login_fails.pop(ip, None)
            session.clear()
            session.permanent = True  # expires after PERMANENT_SESSION_LIFETIME without use
            session["user_id"] = user["id"]
            session["auth"] = auth_tag(user["password_hash"])
            audit("Login", user=user)
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        record_login_fail(ip)
        audit("Login failed", f"Username tried: {username[:60]}")
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    if g.get("user"):
        audit("Logout")
    session.clear()
    return redirect(url_for("login"))


@app.route("/account", methods=["GET", "POST"])
@login_required
def account():
    """Change your own password (also where a forced password change happens)."""
    user = g.user
    if request.method == "POST":
        new = request.form.get("password") or ""
        problem = password_problem(new, request.form.get("confirm") or "")
        if not check_password_hash(user["password_hash"], request.form.get("current_password") or ""):
            flash("Your current password is not correct.", "error")
        elif problem:
            flash(problem, "error")
        elif check_password_hash(user["password_hash"], new):
            flash("Choose a new password, different from your current one.", "error")
        else:
            new_hash = generate_password_hash(new)
            db = get_db()
            db.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?", (new_hash, user["id"]))
            db.commit()
            session["auth"] = auth_tag(new_hash)  # keep this login; others of this account end
            audit("Password changed")
            flash("Your password has been changed.", "success")
            return redirect(url_for("dashboard"))
        return redirect(url_for("account"))
    return render_template("account.html", must_change=bool(user["must_change_password"]))


@app.route("/users", methods=["GET", "POST"])
@admin_required
def users():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        uid = request.form.get("user_id", type=int)
        target = db.execute("SELECT * FROM users WHERE id=?", (uid,)).fetchone() if uid else None
        if action == "add":
            username = clean(request.form.get("username"))
            make_admin = 1 if request.form.get("is_admin") == "1" else 0
            problem = password_problem(request.form.get("password") or "") if username else "Enter a username."
            if problem:
                flash(problem, "error")
            else:
                try:
                    db.execute("INSERT INTO users (username, password_hash, is_admin, must_change_password) "
                               "VALUES (?, ?, ?, 1)",
                               (username, generate_password_hash(request.form.get("password")), make_admin))
                    db.commit()
                    audit("User added", f"{username} ({'administrator' if make_admin else 'user'})")
                    flash(f"User '{username}' added. They will choose their own password when they first log in.",
                          "success")
                except sqlite3.IntegrityError:
                    flash("That username already exists.", "error")
        elif target is None:
            flash("User not found.", "error")
        elif action == "rename":
            username = clean(request.form.get("username"))
            if not username:
                flash("Enter the new username.", "error")
            else:
                try:
                    db.execute("UPDATE users SET username=? WHERE id=?", (username, uid))
                    db.commit()
                    audit("User renamed", f"{target['username']} → {username}")
                    flash(f"Username changed to '{username}'.", "success")
                except sqlite3.IntegrityError:
                    flash("That username already exists.", "error")
        elif uid == g.user["id"]:
            flash("You cannot change your own role, or reset or delete your own account. "
                  "To change your password use My account.", "error")
        elif action == "role":
            make_admin = 1 if request.form.get("is_admin") == "1" else 0
            db.execute("UPDATE users SET is_admin=? WHERE id=?", (make_admin, uid))
            db.commit()
            audit("Admin rights given" if make_admin else "Admin rights removed", target["username"])
            flash(f"'{target['username']}' is now {'an administrator' if make_admin else 'a normal user'}.", "success")
        elif action == "reset":
            problem = password_problem(request.form.get("password") or "")
            if problem:
                flash(problem, "error")
            else:
                db.execute("UPDATE users SET password_hash=?, must_change_password=1 WHERE id=?",
                           (generate_password_hash(request.form.get("password")), uid))
                db.commit()
                audit("Password reset", target["username"])
                flash(f"Password of '{target['username']}' reset. They must choose a new one when they log in.",
                      "success")
        elif action == "delete":
            db.execute("DELETE FROM users WHERE id = ?", (uid,))
            db.commit()
            audit("User deleted", target["username"])
            flash("User deleted.", "success")
        return redirect(url_for("users"))
    rows = db.execute("SELECT id, username, is_admin, must_change_password, created_at FROM users "
                      "ORDER BY username").fetchall()
    return render_template("users.html", users=rows)


@app.route("/audit")
@admin_required
def audit_log():
    q = (request.args.get("q") or "").strip()
    sql, params = "SELECT * FROM audit_log", []
    if q:
        sql += " WHERE username LIKE ? OR action LIKE ? OR details LIKE ? OR ip LIKE ? OR at LIKE ?"
        params = [f"%{q}%"] * 5
    rows = get_db().execute(sql + " ORDER BY id DESC LIMIT 500", params).fetchall()
    return render_template("audit.html", rows=rows, q=q)


@app.route("/files/<path:rel_path>")
@login_required
def files(rel_path):
    return send_from_directory(UPLOAD_DIR, rel_path)


# ---------------------------------------------------------------- global search

def search_all(q, emp_limit=None, firm_limit=None):
    """Search employees and firms by anything a user might type: names, codes, Aadhaar, mobile,
    UAN/ESIC, gate pass no., firm id, LOA/work order, GST/PAN/LIN/registration no."""
    q = (q or "").strip()
    if not q:
        return [], []
    db = get_db()
    like = f"%{q}%"
    digits = re.sub(r"[\s-]", "", q)
    digit_like = f"%{digits}%" if digits.isdigit() and len(digits) >= 4 else None

    emp_sql = ("SELECT e.id, e.emp_code, e.name, e.father_name, e.aadhaar_no, e.mobile, e.designation, "
               "e.skill_category, e.status, e.photo_file, e.firm_id, f.name firm_name FROM employees e "
               "JOIN firms f ON f.id=e.firm_id WHERE e.name LIKE :l OR e.father_name LIKE :l OR e.emp_code LIKE :l "
               "OR e.mobile LIKE :l OR e.designation LIKE :l OR e.gate_pass_no LIKE :l OR e.uan_no LIKE :l "
               "OR e.esic_no LIKE :l OR f.name LIKE :l")
    params = {"l": like, "q": q, "p": f"{q}%"}
    if digit_like:
        emp_sql += " OR e.aadhaar_no LIKE :d OR e.mobile LIKE :d OR e.uan_no LIKE :d OR e.esic_no LIKE :d"
        params["d"] = digit_like
    # best matches first: exact code, then names starting with the text, then active staff
    emp_sql += (" ORDER BY (e.emp_code = :q COLLATE NOCASE) DESC, (e.name LIKE :p) DESC, "
                "(e.status = 'Active') DESC, e.name")
    if emp_limit:
        emp_sql += f" LIMIT {int(emp_limit)}"
    employees = db.execute(emp_sql, params).fetchall()

    firm_sql = ("SELECT f.*, (SELECT COUNT(*) FROM employees e WHERE e.firm_id=f.id AND e.status='Active') "
                "emp_count FROM firms f WHERE f.name LIKE :l OR f.owner_name LIKE :l OR f.contact_person LIKE :l "
                "OR f.work_order_no LIKE :l OR f.gst_no LIKE :l OR f.pan_no LIKE :l OR f.lin_no LIKE :l "
                "OR f.registration_no LIKE :l OR f.labour_licence_no LIKE :l OR f.mobile LIKE :l")
    params["id"] = int(q) if q.isdigit() else -1
    if q.isdigit():
        firm_sql += " OR f.id = :id"
    firm_sql += " ORDER BY (f.id = :id) DESC, (f.name LIKE :p) DESC, f.name"  # exact firm ID first
    if firm_limit:
        firm_sql += f" LIMIT {int(firm_limit)}"
    firms_found = db.execute(firm_sql, params).fetchall()
    return employees, firms_found


@app.route("/search")
@login_required
def search():
    q = (request.args.get("q") or "").strip()
    employees, firms_found = search_all(q)
    if len(employees) == 1 and not firms_found:
        return redirect(url_for("employee_view", emp_id=employees[0]["id"]))
    if len(firms_found) == 1 and not employees:
        return redirect(url_for("firm_view", firm_id=firms_found[0]["id"]))
    return render_template("search.html", q=q, employees=employees, firms=firms_found)


@app.route("/search/suggest")
@login_required
def search_suggest():
    """Live suggestions for the search box in the top bar (JSON)."""
    q = (request.args.get("q") or "").strip()
    if len(q) < 2 and not q.isdigit():  # a single digit is allowed so short firm IDs can be found
        return {"employees": [], "firms": []}
    employees, firms_found = search_all(q, emp_limit=7, firm_limit=4)
    return {
        "employees": [{
            "url": url_for("employee_view", emp_id=e["id"]), "name": e["name"], "code": e["emp_code"] or "",
            "sub": " · ".join(x for x in [e["firm_name"], e["designation"], mask_aadhaar(e["aadhaar_no"])
                                          if e["aadhaar_no"] else None] if x),
            "photo": url_for("files", rel_path=e["photo_file"]) if e["photo_file"] else None,
            "inactive": e["status"] != "Active",
        } for e in employees],
        "firms": [{
            "url": url_for("firm_view", firm_id=f["id"]), "name": f["name"], "code": f"Firm ID {f['id']}",
            "sub": " · ".join(x for x in [f["owner_name"], f["work_order_no"], f"{f['emp_count']} active employees"] if x),
            "inactive": f["status"] != "Active",
        } for f in firms_found],
    }


# ---------------------------------------------------------------- dashboard

@app.route("/")
@login_required
def dashboard():
    db = get_db()
    today = date.today()
    soon = (today + timedelta(days=30)).isoformat()
    stats = {
        "firms": db.execute("SELECT COUNT(*) FROM firms WHERE status='Active'").fetchone()[0],
        "employees": db.execute("SELECT COUNT(*) FROM employees WHERE status='Active'").fetchone()[0],
        "pv_pending": db.execute(
            "SELECT COUNT(*) FROM employees WHERE status='Active' AND pv_done=0").fetchone()[0],
        "pv_expired": db.execute(
            "SELECT COUNT(*) FROM employees WHERE status='Active' AND pv_done=1 "
            "AND pv_valid_upto IS NOT NULL AND pv_valid_upto < ?", (today.isoformat(),)).fetchone()[0],
    }
    skills = {s: 0 for s in SKILL_CATEGORIES}
    for row in db.execute("SELECT skill_category, COUNT(*) c FROM employees "
                          "WHERE status='Active' GROUP BY skill_category"):
        if row["skill_category"] in skills:
            skills[row["skill_category"]] = row["c"]
    stats["pv_expiring"] = db.execute(
        "SELECT COUNT(*) FROM employees WHERE status='Active' AND pv_done=1 "
        "AND pv_valid_upto BETWEEN ? AND ?", (today.isoformat(), soon)).fetchone()[0]
    stats["pv_valid"] = stats["employees"] - stats["pv_pending"] - stats["pv_expired"] - stats["pv_expiring"]

    # Attendance for the chosen day (defaults to today).
    att_date = parse_date(request.args.get("date")) or today
    if att_date > today:
        att_date = today
    att_today = {code: 0 for code in ATTENDANCE_CODES}
    for row in db.execute("SELECT a.status, COUNT(*) c FROM attendance a JOIN employees e "
                          "ON e.id=a.employee_id WHERE a.att_date=? AND e.status='Active' "
                          "GROUP BY a.status", (att_date.isoformat(),)):
        att_today[row["status"]] = row["c"]
    att_today["unmarked"] = stats["employees"] - sum(att_today.values())

    # Per firm: active strength by skill, and attendance on att_date.
    firm_chart = []
    for f in db.execute(
            "SELECT f.id, f.name, "
            "SUM(e.skill_category='Skilled') skilled, SUM(e.skill_category='Semi-skilled') semi, "
            "SUM(e.skill_category='Unskilled') unskilled, COUNT(e.id) active, "
            "SUM(a.status='P') p, SUM(a.status='H') h, SUM(a.status='A') a, "
            "SUM(a.status IN ('L','W')) lw "
            "FROM firms f LEFT JOIN employees e ON e.firm_id=f.id AND e.status='Active' "
            "LEFT JOIN attendance a ON a.employee_id=e.id AND a.att_date=? "
            "WHERE f.status='Active' GROUP BY f.id ORDER BY active DESC, f.name",
            (att_date.isoformat(),)):
        row = {k: (f[k] or 0) for k in f.keys() if k not in ("id", "name")}
        row.update(id=f["id"], name=f["name"])
        row["unmarked"] = row["active"] - row["p"] - row["h"] - row["a"] - row["lw"]
        firm_chart.append(row)

    # Workshop-wide daily presence for the last 30 days.
    start = today - timedelta(days=29)
    by_day = {r["att_date"]: r for r in db.execute(
        "SELECT a.att_date, SUM(a.status='P') p, SUM(a.status='H') h, SUM(a.status='A') a "
        "FROM attendance a JOIN employees e ON e.id=a.employee_id "
        "WHERE a.att_date BETWEEN ? AND ? GROUP BY a.att_date", (start.isoformat(), today.isoformat()))}
    trend = []
    for i in range(30):
        d = (start + timedelta(days=i)).isoformat()
        r = by_day.get(d)
        if r and not (r["p"] + r["h"] + r["a"]):
            r = None  # only weekly off / leave marked (e.g. Sunday): not a working day
        trend.append({"date": d, "present": (r["p"] + r["h"]) if r else None, "absent": r["a"] if r else None})

    pv_alerts = db.execute(
        "SELECT e.*, f.name firm_name FROM employees e JOIN firms f ON f.id=e.firm_id "
        "WHERE e.status='Active' AND e.pv_done=1 AND e.pv_valid_upto IS NOT NULL "
        "AND e.pv_valid_upto <= ? ORDER BY e.pv_valid_upto LIMIT 20", (soon,)).fetchall()
    contract_alerts = db.execute(
        "SELECT * FROM firms WHERE status='Active' AND contract_end IS NOT NULL "
        "AND contract_end <= ? ORDER BY contract_end", (soon,)).fetchall()
    return render_template("dashboard.html", stats=stats, skills=skills, att_today=att_today,
                           att_date=att_date.isoformat(), pv_alerts=pv_alerts,
                           contract_alerts=contract_alerts, firm_chart=firm_chart, trend=trend)


# ---------------------------------------------------------------- firms

FIRM_FIELDS = ["name", "owner_name", "contact_person", "mobile", "email", "address", "gst_no",
               "pan_no", "labour_licence_no", "work_order_no", "work_description",
               "contract_start", "contract_end", "status", "pincode", "lin_no", "registration_no",
               "loa_date"]


@app.route("/firms")
@login_required
def firms():
    q = (request.args.get("q") or "").strip()
    sql = ("SELECT f.*, (SELECT COUNT(*) FROM employees e WHERE e.firm_id=f.id AND e.status='Active') "
           "emp_count FROM firms f")
    params = []
    if q:
        sql += " WHERE f.name LIKE ? OR f.owner_name LIKE ? OR f.work_order_no LIKE ?"
        params = [f"%{q}%"] * 3
    rows = get_db().execute(sql + " ORDER BY f.name", params).fetchall()
    return render_template("firms.html", firms=rows, q=q)


def firm_form_data():
    data = {f: clean(request.form.get(f)) for f in FIRM_FIELDS}
    data["status"] = data["status"] or "Active"
    errors = []
    if not data["name"]:
        errors.append("Firm name is required.")
    if data["mobile"] and not re.fullmatch(r"[6-9]\d{9}", data["mobile"]):
        errors.append("Mobile number must be 10 digits starting with 6-9.")
    if data["gst_no"]:
        data["gst_no"] = data["gst_no"].upper()
    if data["pan_no"]:
        data["pan_no"] = data["pan_no"].upper()
    if data["pincode"] and not re.fullmatch(r"\d{6}", data["pincode"]):
        errors.append("PIN code must be 6 digits.")
    return data, errors


@app.route("/firms/new", methods=["GET", "POST"])
@app.route("/firms/<int:firm_id>/edit", methods=["GET", "POST"])
@login_required
def firm_form(firm_id=None):
    db = get_db()
    firm = None
    if firm_id:
        firm = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone() or abort(404)
    if request.method == "POST":
        data, errors = firm_form_data()
        if not errors:
            try:
                if firm:
                    sets = ", ".join(f"{f}=?" for f in FIRM_FIELDS)
                    db.execute(f"UPDATE firms SET {sets} WHERE id=?",
                               [data[f] for f in FIRM_FIELDS] + [firm_id])
                else:
                    cur = db.execute(
                        f"INSERT INTO firms ({', '.join(FIRM_FIELDS)}) VALUES "
                        f"({', '.join('?' * len(FIRM_FIELDS))})", [data[f] for f in FIRM_FIELDS])
                    firm_id = cur.lastrowid
                db.commit()
                if firm:
                    changed = changed_fields(firm, data, FIRM_FIELDS)
                    audit("Firm edited", f"{data['name']} (firm ID {firm_id}) – changed: "
                                         f"{', '.join(changed) or 'nothing'}")
                else:
                    audit("Firm added", f"{data['name']} (firm ID {firm_id})")
                flash("Firm saved.", "success")
                return redirect(url_for("firm_view", firm_id=firm_id))
            except sqlite3.IntegrityError:
                errors.append("A firm with this name already exists.")
        for e in errors:
            flash(e, "error")
        return render_template("firm_form.html", firm=data, editing=bool(firm))
    return render_template("firm_form.html", firm=firm or {}, editing=bool(firm))


@app.route("/firms/<int:firm_id>")
@login_required
def firm_view(firm_id):
    db = get_db()
    firm = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone() or abort(404)
    emps = db.execute("SELECT * FROM employees WHERE firm_id=? ORDER BY status, name",
                      (firm_id,)).fetchall()
    return render_template("firm_view.html", firm=firm, employees=emps)


@app.route("/firms/<int:firm_id>/delete", methods=["POST"])
@admin_required
def firm_delete(firm_id):
    db = get_db()
    firm = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone() or abort(404)
    if db.execute("SELECT 1 FROM employees WHERE firm_id=? LIMIT 1", (firm_id,)).fetchone():
        flash("This firm still has employees. Move or delete them first, or mark the firm Inactive.",
              "error")
        return redirect(url_for("firm_view", firm_id=firm_id))
    db.execute("DELETE FROM firms WHERE id=?", (firm_id,))
    db.commit()
    audit("Firm deleted", f"{firm['name']} (firm ID {firm_id})")
    flash("Firm deleted.", "success")
    return redirect(url_for("firms"))


# ---------------------------------------------------------------- employees

EMP_FIELDS = ["firm_id", "emp_code", "name", "father_name", "dob", "gender", "aadhaar_no",
              "mobile", "address", "pv_done", "pv_cert_no", "pv_valid_upto", "qualification",
              "skill_category", "designation", "date_of_joining", "status",
              "uan_no", "esic_no", "bank_name", "bank_account", "ifsc", "daily_wage",
              "blood_group", "pincode", "gate_pass_no", "gp_valid_from", "gp_valid_upto",
              "appt_date", "appt_other_allowance", "appt_duties", "appt_other_info"]


def employee_query(args):
    sql = ("SELECT e.*, f.name firm_name FROM employees e JOIN firms f ON f.id=e.firm_id WHERE 1=1")
    params = []
    if args.get("firm_id"):
        sql += " AND e.firm_id=?"
        params.append(args["firm_id"])
    if args.get("skill"):
        sql += " AND e.skill_category=?"
        params.append(args["skill"])
    status = args.get("status", "Active")
    if status:
        sql += " AND e.status=?"
        params.append(status)
    pv = args.get("pv")
    today = date.today().isoformat()
    if pv == "pending":
        sql += " AND e.pv_done=0"
    elif pv == "expired":
        sql += " AND e.pv_done=1 AND e.pv_valid_upto < ?"
        params.append(today)
    elif pv == "expiring":
        sql += " AND e.pv_done=1 AND e.pv_valid_upto BETWEEN ? AND ?"
        params += [today, (date.today() + timedelta(days=30)).isoformat()]
    elif pv == "valid":
        sql += " AND e.pv_done=1 AND (e.pv_valid_upto IS NULL OR e.pv_valid_upto >= ?)"
        params.append(today)
    q = (args.get("q") or "").strip()
    if q:
        sql += (" AND (e.name LIKE ? OR e.father_name LIKE ? OR e.emp_code LIKE ? "
                "OR e.mobile LIKE ? OR e.aadhaar_no LIKE ?)")
        params += [f"%{q}%"] * 5
    return get_db().execute(sql + " ORDER BY f.name, e.name", params).fetchall()


@app.route("/employees")
@login_required
def employees():
    args = request.args.to_dict()
    args.setdefault("status", "Active")
    rows = employee_query(args)
    firm_list = get_db().execute("SELECT id, name FROM firms ORDER BY name").fetchall()
    return render_template("employees.html", employees=rows, firms=firm_list, args=args)


@app.route("/employees/export.csv")
@login_required
def employees_export():
    args = request.args.to_dict()
    args.setdefault("status", "Active")
    rows = employee_query(args)
    out = [["Emp Code", "Name", "Father Name", "Firm", "DOB", "Gender", "Aadhaar No", "Mobile",
            "Address", "Designation", "Skill Category", "Qualification", "Date of Joining",
            "Police Verification", "PV Certificate No", "PV Valid Upto", "Status",
            "UAN", "ESIC IP No", "Bank", "Account No", "IFSC", "Agreed Daily Wage"]]
    for e in rows:
        out.append([e["emp_code"], e["name"], e["father_name"], e["firm_name"], dmy(e["dob"]),
                    e["gender"], f"'{e['aadhaar_no']}" if e["aadhaar_no"] else "", e["mobile"],
                    e["address"], e["designation"], e["skill_category"], e["qualification"],
                    dmy(e["date_of_joining"]), "Yes" if e["pv_done"] else "No", e["pv_cert_no"],
                    dmy(e["pv_valid_upto"]) if e["pv_done"] else "", e["status"],
                    f"'{e['uan_no']}" if e["uan_no"] else "", f"'{e['esic_no']}" if e["esic_no"] else "",
                    e["bank_name"], f"'{e['bank_account']}" if e["bank_account"] else "", e["ifsc"],
                    e["daily_wage"] or ""])
    filters = ", ".join(f"{k}={v}" for k, v in sorted(args.items()) if v)
    audit("Export", f"Employee list CSV with Aadhaar and bank details: {len(rows)} employees"
                    f"{' (' + filters + ')' if filters else ''}")
    return csv_response(out, "employees.csv")


def employee_form_data():
    data = {f: clean(request.form.get(f)) for f in EMP_FIELDS}
    data["pv_done"] = 1 if request.form.get("pv_done") == "1" else 0
    data["status"] = data["status"] or "Active"
    if data["aadhaar_no"]:
        data["aadhaar_no"] = re.sub(r"\D", "", data["aadhaar_no"])
    if data["emp_code"]:
        data["emp_code"] = data["emp_code"].upper()
    if not data["pv_done"]:
        data["pv_cert_no"] = data["pv_valid_upto"] = None
    errors = []
    if not data["name"]:
        errors.append("Employee name is required.")
    if not data["firm_id"]:
        errors.append("Select the firm.")
    if not data["father_name"]:
        errors.append("Father's name is required.")
    if not data["aadhaar_no"]:
        errors.append("Aadhaar number is required.")
    elif not valid_aadhaar(data["aadhaar_no"]):
        errors.append("Aadhaar number is not valid (must be 12 digits and pass the Aadhaar checksum).")
    if data["mobile"] and not re.fullmatch(r"[6-9]\d{9}", data["mobile"]):
        errors.append("Mobile number must be 10 digits starting with 6-9.")
    if data["skill_category"] not in SKILL_CATEGORIES:
        errors.append("Select the skill category (Skilled / Semi-skilled / Unskilled).")
    if data["pv_done"] and not data["pv_valid_upto"]:
        errors.append("Enter the police verification validity date.")
    for col in ("uan_no", "esic_no", "bank_account"):
        if data[col]:
            data[col] = re.sub(r"\s", "", data[col])
    if data["uan_no"] and not re.fullmatch(r"\d{12}", data["uan_no"]):
        errors.append("UAN must be 12 digits.")
    if data["esic_no"] and not re.fullmatch(r"\d{10}|\d{17}", data["esic_no"]):
        errors.append("ESIC IP number must be 10 (or 17) digits.")
    if data["bank_account"] and not re.fullmatch(r"\d{9,18}", data["bank_account"]):
        errors.append("Bank account number must be 9 to 18 digits.")
    if data["ifsc"]:
        data["ifsc"] = data["ifsc"].upper()
        if not re.fullmatch(r"[A-Z]{4}0[A-Z0-9]{6}", data["ifsc"]):
            errors.append("IFSC code is not valid (e.g. SBIN0001234).")
    if data["blood_group"] and data["blood_group"] not in BLOOD_GROUPS:
        errors.append("Select a valid blood group.")
    if data["pincode"] and not re.fullmatch(r"\d{6}", data["pincode"]):
        errors.append("PIN code must be 6 digits.")
    if data["gate_pass_no"]:
        data["gate_pass_no"] = data["gate_pass_no"].upper()
    if data["gp_valid_from"] and data["gp_valid_upto"] and data["gp_valid_upto"] < data["gp_valid_from"]:
        errors.append("Gate pass 'valid upto' cannot be before 'valid from'.")
    if data["daily_wage"]:
        try:
            data["daily_wage"] = round(float(data["daily_wage"]), 2)
            if data["daily_wage"] <= 0:
                raise ValueError
        except ValueError:
            errors.append("Agreed daily wage must be a positive amount.")
    return data, errors


@app.route("/employees/new", methods=["GET", "POST"])
@app.route("/employees/<int:emp_id>/edit", methods=["GET", "POST"])
@login_required
def employee_form(emp_id=None):
    db = get_db()
    emp = None
    if emp_id:
        emp = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone() or abort(404)
    firm_list = db.execute("SELECT id, name FROM firms WHERE status='Active' OR id=? ORDER BY name",
                           (emp["firm_id"] if emp else 0,)).fetchall()
    if request.method == "POST":
        data, errors = employee_form_data()
        new_files = []
        if not errors:
            try:
                photo = (save_data_url(request.form.get("photo_data"), "photos")
                         or save_upload(request.files.get("photo_file"), "photos"))
                aadhaar = save_upload(request.files.get("aadhaar_file"), "aadhaar")
                pv = save_upload(request.files.get("pv_file"), "police") if data["pv_done"] else None
                new_files = [p for p in (photo, aadhaar, pv) if p]
                qual_docs = []
                for fs in request.files.getlist("qual_files"):
                    path = save_upload(fs, "documents")
                    if path:
                        qual_docs.append((path, fs.filename))
                        new_files.append(path)
            except ValueError as exc:
                for p in new_files:
                    delete_upload(p)
                errors.append(str(exc))
        if not errors:
            values = dict(data)
            if emp:
                values["photo_file"] = photo or emp["photo_file"]
                values["aadhaar_file"] = aadhaar or emp["aadhaar_file"]
                values["pv_file"] = pv or (emp["pv_file"] if data["pv_done"] else None)
            else:
                values.update(photo_file=photo, aadhaar_file=aadhaar, pv_file=pv)
            cols = list(values.keys())
            try:
                if emp:
                    sets = ", ".join(f"{c}=?" for c in cols)
                    db.execute(f"UPDATE employees SET {sets}, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                               [values[c] for c in cols] + [emp_id])
                else:
                    cur = db.execute(f"INSERT INTO employees ({', '.join(cols)}) VALUES "
                                     f"({', '.join('?' * len(cols))})", [values[c] for c in cols])
                    emp_id = cur.lastrowid
                    if not values["emp_code"]:
                        db.execute("UPDATE employees SET emp_code=? WHERE id=?",
                                   (f"CE{emp_id:05d}", emp_id))
                for path, original in qual_docs:
                    db.execute("INSERT INTO documents (employee_id, doc_type, title, filename) "
                               "VALUES (?, 'Qualification', ?, ?)", (emp_id, original, path))
                db.commit()
            except sqlite3.IntegrityError as exc:
                db.rollback()
                for p in new_files:
                    delete_upload(p)
                msg = str(exc)
                if "aadhaar_no" in msg:
                    errors.append("Another employee is already registered with this Aadhaar number.")
                elif "emp_code" in msg:
                    errors.append("This employee code is already used.")
                else:
                    errors.append("Could not save: " + msg)
            else:
                # Replace old files only after the new record is committed.
                if emp:
                    for col, new in (("photo_file", photo), ("aadhaar_file", aadhaar), ("pv_file", pv)):
                        if new and emp[col]:
                            delete_upload(emp[col])
                    if not data["pv_done"] and emp["pv_file"]:
                        delete_upload(emp["pv_file"])
                code = db.execute("SELECT emp_code FROM employees WHERE id=?", (emp_id,)).fetchone()[0]
                who = f"{code} {values['name']} (id {emp_id})"
                files = [label for label, new in (("photo", photo), ("Aadhaar card", aadhaar),
                                                  ("police certificate", pv)) if new]
                files += [f"{len(qual_docs)} qualification document(s)"] if qual_docs else []
                if emp:
                    changed = changed_fields(emp, values, EMP_FIELDS) + files
                    audit("Employee edited", f"{who} – changed: {', '.join(changed) or 'nothing'}")
                else:
                    audit("Employee added", who + (f" – uploaded: {', '.join(files)}" if files else ""))
                flash("Employee saved.", "success")
                return redirect(url_for("employee_view", emp_id=emp_id))
        for e in errors:
            flash(e, "error")
        merged = dict(emp) if emp else {}
        merged.update(data)
        return render_template("employee_form.html", emp=merged, firms=firm_list, editing=bool(emp))
    preset = {"firm_id": request.args.get("firm_id")} if not emp else emp
    return render_template("employee_form.html", emp=preset, firms=firm_list, editing=bool(emp))


@app.route("/employees/<int:emp_id>")
@login_required
def employee_view(emp_id):
    db = get_db()
    emp = db.execute("SELECT e.*, f.name firm_name FROM employees e JOIN firms f ON f.id=e.firm_id "
                     "WHERE e.id=?", (emp_id,)).fetchone() or abort(404)
    docs = db.execute("SELECT * FROM documents WHERE employee_id=? ORDER BY uploaded_at DESC",
                      (emp_id,)).fetchall()
    month = date.today().strftime("%Y-%m")
    att = {code: 0 for code in ATTENDANCE_CODES}
    for row in db.execute("SELECT status, COUNT(*) c FROM attendance WHERE employee_id=? "
                          "AND substr(att_date,1,7)=? GROUP BY status", (emp_id, month)):
        att[row["status"]] = row["c"]
    return render_template("employee_view.html", emp=emp, docs=docs, att=att, month=month)


# ---------------------------------------------------------------- printable documents

DOC_SQL = ("SELECT e.*, f.name firm_name, f.address firm_address, f.pincode firm_pincode, "
           "f.mobile firm_mobile, f.email firm_email, f.work_order_no, f.loa_date, f.contract_start, "
           "f.contract_end, f.lin_no, f.registration_no, f.labour_licence_no, f.work_description "
           "FROM employees e JOIN firms f ON f.id=e.firm_id ")


def gate_pass_context(e):
    joined = e["date_of_joining"] or ""
    start = max(joined, e["contract_start"] or "") or None
    return {"e": e, "no": e["gate_pass_no"] or e["emp_code"],
            "valid_from": e["gp_valid_from"] or start,
            "valid_upto": e["gp_valid_upto"] or e["contract_end"]}


def appointment_context(e, rates, s):
    on = e["appt_date"] or e["date_of_joining"] or date.today().isoformat()
    rate, da = rate_parts_on(rates, e["skill_category"], on)
    if rate is None:  # joined before the first rate entered: show the rate in force today
        rate, da = rate_parts_on(rates, e["skill_category"], date.today().isoformat())
    if e["daily_wage"]:
        wages = f"₹ {e['daily_wage']:.2f} per day (consolidated, as agreed)"
    elif rate is None:
        wages = "As per the notified minimum wages"
    elif da:
        wages = (f"Basic ₹ {rate - da:.2f} + DA ₹ {da:.2f} = ₹ {rate:.2f} per day "
                 f"(minimum wages for {e['skill_category']})")
    else:
        wages = f"₹ {rate:.2f} per day (minimum wages for {e['skill_category']})"
    epf = f"EPFO: Applicable – UAN {e['uan_no']}" if e["uan_no"] else "EPFO: Applicable – UAN to be generated"
    esic = (f"ESIC: Applicable – IP No. {e['esic_no']}" if e["esic_no"]
            else f"ESIC: As per eligibility (wages up to ₹ {s['esi_limit']:,.0f} per month)")
    return {
        "e": e, "letter_date": on, "wages": wages, "social": [epf, esic],
        "allowance": e["appt_other_allowance"] or "Nil",
        "duties": e["appt_duties"] or e["work_description"] or e["designation"] or "-",
        "maternity": ("Applicable as per Chapter VI of the Code on Social Security, 2020"
                      if e["gender"] == "Female" else "Not applicable"),
        "other": e["appt_other_info"] or "-",
    }


def print_employees(firm_id=None, emp_id=None):
    """Employees to print: one (emp_id), a firm's active staff (firm_id), or ?ids=1,2,3 picked on Reports."""
    db = get_db()
    if emp_id:
        rows = db.execute(DOC_SQL + "WHERE e.id=?", (emp_id,)).fetchall()
        if not rows:
            abort(404)
        return rows
    if firm_id:
        db.execute("SELECT 1 FROM firms WHERE id=?", (firm_id,)).fetchone() or abort(404)
        return db.execute(DOC_SQL + "WHERE e.firm_id=? AND e.status='Active' ORDER BY e.name",
                          (firm_id,)).fetchall()
    ids = [int(i) for i in request.args.getlist("ids") if i.isdigit()]
    if not ids:
        abort(400, "Select at least one employee.")
    marks = ",".join("?" * len(ids))
    return db.execute(DOC_SQL + f"WHERE e.id IN ({marks}) ORDER BY f.name, e.name", ids).fetchall()


DOC_KINDS = {
    "id-cards": ("ID cards / Gate passes", "employee_card"),
    "appointment-letters": ("Appointment letters", "appointment_letter"),
}


@app.route("/reports/<kind>")
@login_required
def print_documents(kind):
    """Pick a firm and employees, then print their ID cards or appointment letters."""
    if kind not in DOC_KINDS:
        abort(404)
    title, endpoint = DOC_KINDS[kind]
    db = get_db()
    firm_list = db.execute("SELECT id, name FROM firms ORDER BY status, name").fetchall()
    firm_id = request.args.get("firm_id", type=int)
    status = request.args.get("status", "Active")
    q = (request.args.get("q") or "").strip()
    sql = ("SELECT e.id, e.emp_code, e.name, e.father_name, e.designation, e.skill_category, e.status, "
           "e.photo_file, e.date_of_joining, f.name firm_name FROM employees e JOIN firms f ON f.id=e.firm_id "
           "WHERE 1=1")
    params = []
    if firm_id:
        sql += " AND e.firm_id=?"
        params.append(firm_id)
    if status:
        sql += " AND e.status=?"
        params.append(status)
    if q:
        sql += " AND (e.name LIKE ? OR e.emp_code LIKE ? OR e.father_name LIKE ?)"
        params += [f"%{q}%"] * 3
    rows = db.execute(sql + " ORDER BY f.name, e.name", params).fetchall() if (firm_id or q) else []
    return render_template("print_documents.html", kind=kind, title=title, endpoint=endpoint, firms=firm_list,
                           firm_id=firm_id, status=status, q=q, rows=rows)


@app.route("/employees/<int:emp_id>/card")
@app.route("/firms/<int:firm_id>/gate-passes")
@app.route("/print/gate-passes")
@login_required
def employee_card(emp_id=None, firm_id=None):
    rows = print_employees(firm_id, emp_id)
    return render_template("gate_pass.html", passes=[gate_pass_context(e) for e in rows],
                           back=request.referrer)


@app.route("/employees/<int:emp_id>/appointment")
@app.route("/firms/<int:firm_id>/appointment-letters")
@app.route("/print/appointment-letters")
@login_required
def appointment_letter(emp_id=None, firm_id=None):
    rows = print_employees(firm_id, emp_id)
    rates, s = rate_table(), get_settings()
    return render_template("appointment_letter.html", letters=[appointment_context(e, rates, s) for e in rows],
                           back=request.referrer)


@app.route("/employees/<int:emp_id>/delete", methods=["POST"])
@admin_required
def employee_delete(emp_id):
    """Only for records entered by mistake. Someone with attendance or wage entries stays in the
    database (set Inactive), because deleting them would change past registers and wage sheets."""
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone() or abort(404)
    n_att = db.execute("SELECT COUNT(*) FROM attendance WHERE employee_id=?", (emp_id,)).fetchone()[0]
    n_pay = db.execute("SELECT COUNT(*) FROM payroll_adjustments WHERE employee_id=?", (emp_id,)).fetchone()[0]
    if n_att or n_pay:
        flash(f"{emp['name']} cannot be deleted: they have {n_att} attendance record(s) and {n_pay} wage "
              f"sheet entr{'y' if n_pay == 1 else 'ies'}, which would disappear from past attendance registers "
              f"and wage sheets. Edit the employee and set Status to Inactive instead.", "error")
        return redirect(url_for("employee_view", emp_id=emp_id))
    docs = db.execute("SELECT filename FROM documents WHERE employee_id=?", (emp_id,)).fetchall()
    db.execute("DELETE FROM employees WHERE id=?", (emp_id,))
    db.commit()
    for path in [emp["photo_file"], emp["aadhaar_file"], emp["pv_file"]] + [d["filename"] for d in docs]:
        delete_upload(path)
    audit("Employee deleted", f"{emp['emp_code']} {emp['name']} (id {emp_id})")
    flash(f"Employee {emp['name']} deleted.", "success")
    return redirect(url_for("employees"))


@app.route("/employees/<int:emp_id>/documents", methods=["POST"])
@login_required
def document_upload(emp_id):
    db = get_db()
    emp = db.execute("SELECT emp_code, name FROM employees WHERE id=?", (emp_id,)).fetchone() or abort(404)
    doc_type = request.form.get("doc_type") or "Other"
    title = clean(request.form.get("title"))
    count = 0
    try:
        for fs in request.files.getlist("files"):
            path = save_upload(fs, "documents")
            if path:
                db.execute("INSERT INTO documents (employee_id, doc_type, title, filename) "
                           "VALUES (?, ?, ?, ?)", (emp_id, doc_type, title or fs.filename, path))
                count += 1
        db.commit()
    except ValueError as exc:
        db.commit()
        flash(str(exc), "error")
    if count:
        audit("Documents uploaded", f"{emp['emp_code']} {emp['name']} (id {emp_id}) – {count} × {doc_type}")
        flash(f"{count} document(s) uploaded.", "success")
    elif not request.files.getlist("files") or not any(f.filename for f in request.files.getlist("files")):
        flash("Choose at least one file to upload.", "error")
    return redirect(url_for("employee_view", emp_id=emp_id) + "#documents")


@app.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
def document_delete(doc_id):
    db = get_db()
    doc = db.execute("SELECT d.*, e.emp_code, e.name FROM documents d JOIN employees e ON e.id=d.employee_id "
                     "WHERE d.id=?", (doc_id,)).fetchone() or abort(404)
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    db.commit()
    delete_upload(doc["filename"])
    audit("Document deleted", f"{doc['emp_code']} {doc['name']} (id {doc['employee_id']}) – "
                              f"{doc['doc_type']}: {doc['title'] or 'untitled'}")
    flash("Document deleted.", "success")
    return redirect(url_for("employee_view", emp_id=doc["employee_id"]) + "#documents")


# ---------------------------------------------------------------- attendance

@app.route("/attendance", methods=["GET", "POST"])
@login_required
def attendance():
    db = get_db()
    firm_list = db.execute("SELECT id, name FROM firms WHERE status='Active' ORDER BY name").fetchall()
    firm_id = request.values.get("firm_id", type=int)
    att_date = parse_date(request.values.get("date")) or date.today()
    if att_date > date.today():
        flash("Attendance cannot be marked for a future date.", "error")
        att_date = date.today()

    if request.method == "POST" and firm_id:
        emp_ids = [r["id"] for r in db.execute(
            "SELECT id FROM employees WHERE firm_id=? AND status='Active'", (firm_id,))]
        saved = cleared = 0
        for eid in emp_ids:
            status = request.form.get(f"status_{eid}")
            remarks = clean(request.form.get(f"remarks_{eid}"))
            if status in ATTENDANCE_CODES:
                db.execute("INSERT INTO attendance (employee_id, att_date, status, remarks) "
                           "VALUES (?, ?, ?, ?) ON CONFLICT(employee_id, att_date) DO UPDATE "
                           "SET status=excluded.status, remarks=excluded.remarks",
                           (eid, att_date.isoformat(), status, remarks))
                saved += 1
            elif status == "":
                cleared += db.execute("DELETE FROM attendance WHERE employee_id=? AND att_date=?",
                                      (eid, att_date.isoformat())).rowcount
        db.commit()
        firm = db.execute("SELECT name FROM firms WHERE id=?", (firm_id,)).fetchone()
        audit("Attendance saved", f"{firm['name'] if firm else 'firm ID ' + str(firm_id)} – {att_date:%d-%m-%Y} – "
                                  f"{saved} marked" + (f", {cleared} cleared" if cleared else ""))
        flash(f"Attendance saved for {saved} employee(s) on {att_date:%d-%m-%Y}.", "success")
        return redirect(url_for("attendance", firm_id=firm_id, date=att_date.isoformat()))

    rows = []
    if firm_id:
        rows = db.execute(
            "SELECT e.id, e.emp_code, e.name, e.designation, e.skill_category, e.photo_file, "
            "a.status, a.remarks FROM employees e LEFT JOIN attendance a "
            "ON a.employee_id=e.id AND a.att_date=? WHERE e.firm_id=? AND e.status='Active' "
            "ORDER BY e.name", (att_date.isoformat(), firm_id)).fetchall()
    return render_template("attendance.html", firms=firm_list, firm_id=firm_id,
                           att_date=att_date.isoformat(), rows=rows)


def month_days(month):
    """'YYYY-MM' -> (year, month, [1..days_in_month]); 400 on anything else, e.g. '2026-1'
    (attendance dates are stored as YYYY-MM-DD, so a short month would silently match nothing)."""
    m = re.fullmatch(r"([1-9]\d{3})-(0[1-9]|1[0-2])", month or "")
    if not m:
        abort(400, "Month must be in YYYY-MM format, e.g. 2026-04.")
    year, mon = int(m.group(1)), int(m.group(2))
    return year, mon, list(range(1, calendar.monthrange(year, mon)[1] + 1))


def month_register(firm_id, month, days):
    """Per-employee attendance codes for a firm's month: active staff plus anyone marked that month."""
    db = get_db()
    emps = db.execute(
        "SELECT DISTINCT e.* FROM employees e LEFT JOIN attendance a ON a.employee_id=e.id "
        "AND substr(a.att_date,1,7)=? WHERE e.firm_id=? AND (e.status='Active' OR a.id IS NOT NULL) "
        "ORDER BY e.name", (month, firm_id)).fetchall()
    marks = {}
    for r in db.execute("SELECT a.employee_id, a.att_date, a.status FROM attendance a "
                        "JOIN employees e ON e.id=a.employee_id "
                        "WHERE e.firm_id=? AND substr(a.att_date,1,7)=?", (firm_id, month)):
        marks[(r["employee_id"], int(r["att_date"][8:10]))] = r["status"]
    report = []
    for e in emps:
        codes = [marks.get((e["id"], d), "") for d in days]
        totals = {c: codes.count(c) for c in ATTENDANCE_CODES}
        report.append({"emp": e, "codes": codes, "totals": totals,
                       "worked": totals["P"] + 0.5 * totals["H"]})
    return report


@app.route("/attendance/report")
@login_required
def attendance_report():
    db = get_db()
    firm_list = db.execute("SELECT id, name FROM firms ORDER BY name").fetchall()
    firm_id = request.args.get("firm_id", type=int)
    month = request.args.get("month") or date.today().strftime("%Y-%m")
    year, mon, days = month_days(month)
    report = []
    firm = None
    if firm_id:
        firm = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone()
        report = month_register(firm_id, month, days)

    if request.args.get("export") == "csv" and firm:
        out = [[f"Attendance register - {firm['name']} - {calendar.month_name[mon]} {year}"],
               ["Emp Code", "Name", "Father Name", "Designation", "Skill"] + days +
               ["Present", "Half Day", "Absent", "Leave", "Weekly Off", "Days Worked"]]
        for r in report:
            e = r["emp"]
            t = r["totals"]
            out.append([e["emp_code"], e["name"], e["father_name"], e["designation"],
                        e["skill_category"]] + r["codes"] +
                       [t["P"], t["H"], t["A"], t["L"], t["W"], r["worked"]])
        audit("Export", f"Attendance register CSV – {firm['name']} – {month}")
        return csv_response(out, f"attendance_{re.sub(r'[^A-Za-z0-9]+', '_', firm['name'])}_{month}.csv")

    weekdays = [calendar.weekday(year, mon, d) for d in days]
    return render_template("attendance_report.html", firms=firm_list, firm_id=firm_id, firm=firm,
                           month=month, days=days, weekdays=weekdays, report=report,
                           month_label=f"{calendar.month_name[mon]} {year}")


# ---------------------------------------------------------------- wages

def get_settings():
    s = dict(PAYROLL_DEFAULTS)
    s.update({r["key"]: r["value"] for r in get_db().execute("SELECT key, value FROM settings")
              if r["key"] in PAYROLL_DEFAULTS})
    return {k: float(v) for k, v in s.items()}


def rate_table():
    """{skill: [(effective_from, daily_rate, da_part), ...]} sorted by date."""
    table = {s: [] for s in SKILL_CATEGORIES}
    for r in get_db().execute("SELECT * FROM wage_rates ORDER BY effective_from"):
        table.setdefault(r["skill_category"], []).append(
            (r["effective_from"], r["daily_rate"], r["da_rate"] or 0))
    return table


def rate_parts_on(table, skill, day_iso):
    """(daily_rate, da_part) in force on a day, or (None, 0) if no rate is set."""
    found = (None, 0)
    for eff, value, da in table.get(skill, []):
        if eff <= day_iso:
            found = (value, da)
    return found


def rate_on(table, skill, day_iso):
    return rate_parts_on(table, skill, day_iso)[0]


def rupees(x):
    """Round to the nearest rupee, halves up."""
    return math.floor(x + 0.5)


def esi_period_months(month):
    """Months of the ESI contribution period (April–September or October–March) up to `month`."""
    year, mon = int(month[:4]), int(month[5:7])
    y, m = (year, 4) if 4 <= mon <= 9 else (year, 10) if mon >= 10 else (year - 1, 10)
    months = []
    while (y, m) <= (year, mon):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def wage_earnings(firm_id, month, s, rates):
    """Each employee's earnings on a firm's wage sheet for a month, before EPF and ESI."""
    year, mon, days = month_days(month)
    units = {"P": 1, "H": 0.5, "W": 1 if s["pay_weekly_off"] else 0, "L": 1 if s["pay_leave"] else 0}
    adj = {r["employee_id"]: r for r in get_db().execute(
        "SELECT * FROM payroll_adjustments WHERE month=?", (month,))}
    rows = []
    for reg in month_register(firm_id, month, days):
        e = reg["emp"]
        paid_days = gross = gross_min = da_amt = 0
        missing_rate = False
        for d, code in zip(days, reg["codes"]):
            u = units.get(code, 0)
            if not u:
                continue
            paid_days += u
            min_rate, da = rate_parts_on(rates, e["skill_category"], f"{month}-{d:02d}")
            if min_rate is None:
                missing_rate, min_rate = True, 0
            gross += u * (e["daily_wage"] or min_rate)
            gross_min += u * min_rate
            if not e["daily_wage"]:  # an agreed wage is consolidated, no separate DA
                da_amt += u * da
        a = adj.get(e["id"])
        rows.append({
            "emp": e, "totals": reg["totals"], "paid_days": paid_days, "gross": round(gross, 2),
            "gross_min": gross_min, "da_amt": round(da_amt, 2), "missing_rate": missing_rate,
            "allowance": a["allowance"] if a else 0, "overtime": (a["overtime"] or 0) if a else 0,
            "deduction": a["deduction"] if a else 0, "remarks": a["remarks"] if a else "",
        })
    return rows


def esi_eligible(rows, limit):
    """Employees whose wages for the month, leaving out overtime, are within the ESI ceiling."""
    return {r["emp"]["id"] for r in rows if 0 < r["gross"] + r["allowance"] <= limit}


def compute_wages(firm_id, month):
    year, mon, days = month_days(month)
    s = get_settings()
    rates = rate_table()
    last_day = f"{month}-{days[-1]:02d}"
    rows = wage_earnings(firm_id, month, s, rates)

    # ESI (ESI Act, section 2(9)): an employee is covered while their wages, excluding overtime,
    # are within the ceiling, and once covered stays covered until the end of that contribution
    # period (April–September / October–March) even if wages later go above it. The contribution
    # itself is on everything earned, overtime included. An ESI ceiling of 0 switches ESI off.
    covered = set()
    if s["esi_limit"] > 0:
        for earlier in esi_period_months(month)[:-1]:
            covered |= esi_eligible(wage_earnings(firm_id, earlier, s, rates), s["esi_limit"])
        covered |= esi_eligible(rows, s["esi_limit"])

    for r in rows:
        e = r["emp"]
        gross, da_amt = r["gross"], r["da_amt"]
        earned = gross + r["allowance"] + r["overtime"]
        pf_base = min(gross, s["pf_ceiling"]) if s["pf_ceiling"] else gross
        pf = rupees(pf_base * s["pf_rate"] / 100)
        pf_er = rupees(pf_base * s["pf_employer_rate"] / 100)
        esi = esi_er = 0
        if e["id"] in covered and earned > 0:
            esi = math.ceil(round(earned * s["esi_rate"] / 100, 4))
            esi_er = math.ceil(round(earned * s["esi_employer_rate"] / 100, 4))
        min_rate_end, da_rate_end = rate_parts_on(rates, e["skill_category"], last_day)
        rate_end = e["daily_wage"] or min_rate_end
        da_rate_end = 0 if e["daily_wage"] else da_rate_end
        r.update({
            "rate": rate_end, "min_rate": min_rate_end,
            "basic_rate": (rate_end - da_rate_end) if rate_end else None, "da_rate": da_rate_end,
            "missing_rate": r["missing_rate"] and r["paid_days"] > 0,
            "below_min": bool(e["daily_wage"]) and gross < r.pop("gross_min") - 0.005,
            "basic_amt": round(gross - da_amt, 2), "earned": round(earned, 2),
            "pf": pf, "esi": esi, "esi_covered": e["id"] in covered,
            "total_deduction": pf + esi + r["deduction"],
            "net": round(earned - pf - esi - r["deduction"], 2),
            "pf_er": pf_er, "esi_er": esi_er,
        })
    keys = ["paid_days", "gross", "basic_amt", "da_amt", "allowance", "overtime", "earned", "pf", "esi",
            "deduction", "total_deduction", "net", "pf_er", "esi_er"]
    totals = {k: round(sum(r[k] for r in rows), 2) for k in keys}
    return rows, totals, s


@app.route("/wages", methods=["GET", "POST"])
@login_required
def wages():
    db = get_db()
    firm_list = db.execute("SELECT id, name FROM firms ORDER BY name").fetchall()
    firm_id = request.values.get("firm_id", type=int)
    month = request.values.get("month") or date.today().strftime("%Y-%m")
    year, mon, _days = month_days(month)
    firm = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone() if firm_id else None

    if request.method == "POST" and firm:
        errors = 0
        for reg in month_register(firm_id, month, _days):
            eid = reg["emp"]["id"]
            try:
                allowance = float(request.form.get(f"allowance_{eid}") or 0)
                overtime = float(request.form.get(f"overtime_{eid}") or 0)
                deduction = float(request.form.get(f"deduction_{eid}") or 0)
                if allowance < 0 or overtime < 0 or deduction < 0:
                    raise ValueError
            except ValueError:
                errors += 1
                continue
            remarks = clean(request.form.get(f"remarks_{eid}"))
            if allowance or overtime or deduction or remarks:
                db.execute("INSERT INTO payroll_adjustments (employee_id, month, allowance, overtime, deduction, "
                           "remarks) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(employee_id, month) DO UPDATE SET "
                           "allowance=excluded.allowance, overtime=excluded.overtime, "
                           "deduction=excluded.deduction, remarks=excluded.remarks",
                           (eid, month, allowance, overtime, deduction, remarks))
            else:
                db.execute("DELETE FROM payroll_adjustments WHERE employee_id=? AND month=?", (eid, month))
        db.commit()
        audit("Wage sheet saved", f"{firm['name']} – {month}" + (f" – {errors} invalid row(s) skipped" if errors else ""))
        if errors:
            flash(f"{errors} row(s) had an invalid amount and were not saved.", "error")
        else:
            flash("Wage sheet saved.", "success")
        return redirect(url_for("wages", firm_id=firm_id, month=month))

    rows, totals, s = compute_wages(firm_id, month) if firm else ([], {}, get_settings())
    month_label = f"{calendar.month_name[mon]} {year}"

    if request.args.get("export") == "csv" and firm:
        out = [[f"Wage sheet - {firm['name']} - {month_label}"],
               ["Emp Code", "Name", "Father Name", "Skill", "Designation", "UAN", "ESIC IP No",
                "Bank Account", "IFSC", "Present", "Half Day", "Weekly Off", "Leave", "Paid Days",
                "Daily Rate", "Basic", "DA", "Basic + DA", "Other Allowance", "Overtime", "Gross Earned",
                "EPF (Employee)", "ESI (Employee)", "Other Deduction", "Total Deduction", "Net Payable",
                "EPF (Employer)", "ESI (Employer)", "Remarks"]]
        for r in rows:
            e, t = r["emp"], r["totals"]
            out.append([e["emp_code"], e["name"], e["father_name"], e["skill_category"], e["designation"],
                        f"'{e['uan_no']}" if e["uan_no"] else "", f"'{e['esic_no']}" if e["esic_no"] else "",
                        f"'{e['bank_account']}" if e["bank_account"] else "", e["ifsc"],
                        t["P"], t["H"], t["W"], t["L"], r["paid_days"], r["rate"] or "", r["basic_amt"],
                        r["da_amt"], r["gross"], r["allowance"], r["overtime"], r["earned"], r["pf"], r["esi"],
                        r["deduction"], r["total_deduction"], r["net"], r["pf_er"], r["esi_er"], r["remarks"]])
        out.append(["", "TOTAL"] + [""] * 11 + [totals["paid_days"], "", totals["basic_amt"], totals["da_amt"],
                   totals["gross"], totals["allowance"], totals["overtime"],
                   totals["earned"], totals["pf"], totals["esi"], totals["deduction"],
                   totals["total_deduction"], totals["net"], totals["pf_er"], totals["esi_er"], ""])
        audit("Export", f"Wage sheet CSV with bank details – {firm['name']} – {month}")
        return csv_response(out, f"wages_{re.sub(r'[^A-Za-z0-9]+', '_', firm['name'])}_{month}.csv")

    no_rates = not db.execute("SELECT 1 FROM wage_rates LIMIT 1").fetchone()
    return render_template("wages.html", firms=firm_list, firm_id=firm_id, firm=firm, month=month,
                           month_label=month_label, rows=rows, totals=totals, s=s, no_rates=no_rates)


@app.route("/wages/payslip/<int:emp_id>")
@app.route("/wages/payslips/<int:firm_id>")
@login_required
def payslip(emp_id=None, firm_id=None):
    """Form V wage slip – one employee, or every employee on a firm's wage sheet."""
    db = get_db()
    month = request.args.get("month") or date.today().strftime("%Y-%m")
    year, mon, days = month_days(month)
    if emp_id:
        emp = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone() or abort(404)
        firm_id = emp["firm_id"]
    firm = db.execute("SELECT * FROM firms WHERE id=?", (firm_id,)).fetchone() or abort(404)
    rows, _totals, s = compute_wages(firm_id, month)
    if emp_id:
        rows = [r for r in rows if r["emp"]["id"] == emp_id]
    if not rows:
        flash("No wage record for that month.", "error")
        return redirect(url_for("wages", firm_id=firm_id, month=month))
    return render_template("payslip.html", rows=rows, firm=firm, s=s, back=request.referrer,
                           period=f"{month}-01", period_end=f"{month}-{days[-1]:02d}",
                           month_label=f"{calendar.month_name[mon]} {year}")


@app.route("/wages/settings", methods=["GET", "POST"])
@login_required
def wage_settings():
    """Everyone can see the rates and settings; only an administrator can change them."""
    db = get_db()
    if request.method == "POST":
        if not g.user["is_admin"]:
            return not_admin()
        action = request.form.get("action")
        if action == "rate_add":
            skill = request.form.get("skill_category")
            eff = parse_date(request.form.get("effective_from"))
            try:
                rate = round(float(request.form.get("daily_rate") or 0), 2)
                da = round(float(request.form.get("da_rate") or 0), 2)
            except ValueError:
                rate, da = 0, -1
            if skill not in SKILL_CATEGORIES or not eff or rate <= 0:
                flash("Choose a skill category, an effective date and a positive daily rate.", "error")
            elif da < 0 or da >= rate:
                flash("The DA part must be 0 or more and less than the total daily rate.", "error")
            else:
                db.execute("INSERT INTO wage_rates (skill_category, daily_rate, da_rate, effective_from, notes) "
                           "VALUES (?, ?, ?, ?, ?) ON CONFLICT(skill_category, effective_from) DO UPDATE "
                           "SET daily_rate=excluded.daily_rate, da_rate=excluded.da_rate, notes=excluded.notes",
                           (skill, rate, da, eff.isoformat(), clean(request.form.get("notes"))))
                db.commit()
                audit("Wage rate saved", f"{skill} ₹{rate:g}/day (DA ₹{da:g}) from {eff:%d-%m-%Y}")
                flash(f"{skill} rate ₹{rate:g}/day from {eff:%d-%m-%Y} saved.", "success")
        elif action == "rate_delete":
            old = db.execute("SELECT * FROM wage_rates WHERE id=?", (request.form.get("rate_id", type=int),)).fetchone()
            if old:
                db.execute("DELETE FROM wage_rates WHERE id=?", (old["id"],))
                db.commit()
                audit("Wage rate deleted", f"{old['skill_category']} ₹{old['daily_rate']:g}/day from "
                                           f"{dmy(old['effective_from'])}")
            flash("Rate deleted.", "success")
        elif action == "settings":
            new = {"pay_weekly_off": "1" if request.form.get("pay_weekly_off") else "0",
                   "pay_leave": "1" if request.form.get("pay_leave") else "0"}
            try:
                for key in ("pf_rate", "pf_employer_rate", "pf_ceiling", "esi_rate",
                            "esi_employer_rate", "esi_limit"):
                    value = float(request.form.get(key) or 0)
                    if value < 0:
                        raise ValueError
                    new[key] = f"{value:g}"
            except ValueError:
                flash("All percentages and limits must be numbers (0 or more).", "error")
                return redirect(url_for("wage_settings"))
            before = {k: f"{v:g}" for k, v in get_settings().items()}
            db.executemany("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) "
                           "DO UPDATE SET value=excluded.value", new.items())
            db.commit()
            changes = [f"{k} {before[k]} → {v}" for k, v in new.items() if before.get(k) != v]
            audit("Payroll settings changed", ", ".join(changes) or "no change")
            flash("Payroll settings saved.", "success")
        return redirect(url_for("wage_settings"))
    rates = db.execute("SELECT * FROM wage_rates ORDER BY skill_category, effective_from DESC").fetchall()
    table = rate_table()
    current = {sk: rate_on(table, sk, date.today().isoformat()) for sk in SKILL_CATEGORIES}
    return render_template("wage_settings.html", rates=rates, s=get_settings(), current=current)


@app.errorhandler(413)
def too_large(_e):
    flash("Upload too large. Each request may be at most 25 MB.", "error")
    return redirect(back_url())


init_db()
app.secret_key = _load_secret_key()  # after init_db, which may replace a published key

if __name__ == "__main__":
    # host 0.0.0.0 lets other computers on the workshop LAN open the app.
    try:
        from waitress import serve
    except ImportError:
        print("waitress is not installed, so Flask's development server is used.\n"
              "Run:  python -m pip install -r requirements.txt")
        app.run(host="0.0.0.0", port=5000, debug=False)
    else:
        print("Contractual Employees Management Software is running.\n"
              "Open http://localhost:5000 (other computers: http://<this PC's IP>:5000).\n"
              "Close this window to stop it.")
        serve(app, host="0.0.0.0", port=5000, threads=8)
