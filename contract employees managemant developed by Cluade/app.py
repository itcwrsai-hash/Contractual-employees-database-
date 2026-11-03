"""Contractual Employees Management Software.
Developed by IT Center, Wagon Repair Shop, Raipur, SECR.

Manages contractor firms, their employees (profile, Aadhaar, photo, police
verification, qualification documents, skill category) and daily attendance.

Run:  python app.py   then open http://localhost:5000
"""
import base64
import calendar
import csv
import io
import math
import os
import re
import secrets
import sqlite3
import uuid
from datetime import date, datetime, timedelta
from functools import wraps

from flask import (Flask, Response, abort, flash, g, redirect, render_template,
                   request, send_from_directory, session, url_for)
from markupsafe import Markup
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "contract_employees.db")
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

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 25 * 1024 * 1024  # 25 MB per request


def _load_secret_key():
    os.makedirs(DATA_DIR, exist_ok=True)
    key_file = os.path.join(DATA_DIR, "secret.key")
    if not os.path.exists(key_file):
        with open(key_file, "w") as f:
            f.write(secrets.token_hex(32))
    with open(key_file) as f:
        return f.read().strip()


app.secret_key = _load_secret_key()

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
CREATE INDEX IF NOT EXISTS idx_emp_firm ON employees(firm_id);
CREATE INDEX IF NOT EXISTS idx_att_date ON attendance(att_date);
"""

# Columns added after the first release; init_db adds them to existing databases.
EXTRA_COLUMNS = {
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
        g.db = sqlite3.connect(DB_PATH)
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
    with sqlite3.connect(DB_PATH) as db:
        db.executescript(SCHEMA)
        for table, columns in EXTRA_COLUMNS.items():
            existing = {r[1] for r in db.execute(f"PRAGMA table_info({table})")}
            for col, typ in columns.items():
                if col not in existing:
                    db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        db.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                       PAYROLL_DEFAULTS.items())


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


# ---------------------------------------------------------------- auth & csrf

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("user_id"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.before_request
def guard():
    if request.endpoint == "static":
        return
    has_user = get_db().execute("SELECT 1 FROM users LIMIT 1").fetchone()
    if not has_user and request.endpoint != "setup":
        return redirect(url_for("setup"))
    if request.method == "POST":
        token = request.form.get("csrf_token")
        if not token or token != session.get("csrf_token"):
            abort(400, "Form expired. Please go back, reload the page and try again.")


@app.context_processor
def inject_csrf():
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(16)
    return {"csrf_token": session["csrf_token"], "today": date.today().isoformat()}


@app.route("/setup", methods=["GET", "POST"])
def setup():
    db = get_db()
    if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
        return redirect(url_for("login"))
    if request.method == "POST":
        username = clean(request.form.get("username"))
        password = request.form.get("password") or ""
        if not username or len(password) < 6:
            flash("Enter a username and a password of at least 6 characters.", "error")
        elif password != request.form.get("confirm"):
            flash("Passwords do not match.", "error")
        else:
            db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                       (username, generate_password_hash(password)))
            db.commit()
            flash("Admin account created. Please log in.", "success")
            return redirect(url_for("login"))
    return render_template("setup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        user = get_db().execute("SELECT * FROM users WHERE username = ?",
                                (request.form.get("username", "").strip(),)).fetchone()
        if user and check_password_hash(user["password_hash"], request.form.get("password", "")):
            session.clear()
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            nxt = request.args.get("next", "")
            return redirect(nxt if nxt.startswith("/") and not nxt.startswith("//") else url_for("dashboard"))
        flash("Invalid username or password.", "error")
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/users", methods=["GET", "POST"])
@login_required
def users():
    db = get_db()
    if request.method == "POST":
        action = request.form.get("action")
        if action == "add":
            username = clean(request.form.get("username"))
            password = request.form.get("password") or ""
            if not username or len(password) < 6:
                flash("Enter a username and a password of at least 6 characters.", "error")
            else:
                try:
                    db.execute("INSERT INTO users (username, password_hash) VALUES (?, ?)",
                               (username, generate_password_hash(password)))
                    db.commit()
                    flash(f"User '{username}' added.", "success")
                except sqlite3.IntegrityError:
                    flash("That username already exists.", "error")
        elif action == "password":
            password = request.form.get("password") or ""
            if len(password) < 6:
                flash("Password must be at least 6 characters.", "error")
            else:
                db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                           (generate_password_hash(password), session["user_id"]))
                db.commit()
                flash("Your password has been changed.", "success")
        elif action == "rename":
            uid = request.form.get("user_id", type=int)
            username = clean(request.form.get("username"))
            if not username:
                flash("Enter the new username.", "error")
            elif not db.execute("SELECT 1 FROM users WHERE id=?", (uid,)).fetchone():
                flash("User not found.", "error")
            else:
                try:
                    db.execute("UPDATE users SET username=? WHERE id=?", (username, uid))
                    db.commit()
                    if uid == session["user_id"]:
                        session["username"] = username
                    flash(f"Username changed to '{username}'.", "success")
                except sqlite3.IntegrityError:
                    flash("That username already exists.", "error")
        elif action == "delete":
            uid = int(request.form.get("user_id", 0))
            if uid == session["user_id"]:
                flash("You cannot delete your own account.", "error")
            else:
                db.execute("DELETE FROM users WHERE id = ?", (uid,))
                db.commit()
                flash("User deleted.", "success")
        return redirect(url_for("users"))
    rows = db.execute("SELECT id, username, created_at FROM users ORDER BY username").fetchall()
    return render_template("users.html", users=rows)


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
@login_required
def firm_delete(firm_id):
    db = get_db()
    if db.execute("SELECT 1 FROM employees WHERE firm_id=? LIMIT 1", (firm_id,)).fetchone():
        flash("This firm still has employees. Move or delete them first, or mark the firm Inactive.",
              "error")
        return redirect(url_for("firm_view", firm_id=firm_id))
    db.execute("DELETE FROM firms WHERE id=?", (firm_id,))
    db.commit()
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
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Emp Code", "Name", "Father Name", "Firm", "DOB", "Gender", "Aadhaar No", "Mobile",
                "Address", "Designation", "Skill Category", "Qualification", "Date of Joining",
                "Police Verification", "PV Certificate No", "PV Valid Upto", "Status",
                "UAN", "ESIC IP No", "Bank", "Account No", "IFSC", "Agreed Daily Wage"])
    for e in rows:
        w.writerow([e["emp_code"], e["name"], e["father_name"], e["firm_name"], dmy(e["dob"]),
                    e["gender"], f"'{e['aadhaar_no']}" if e["aadhaar_no"] else "", e["mobile"],
                    e["address"], e["designation"], e["skill_category"], e["qualification"],
                    dmy(e["date_of_joining"]), "Yes" if e["pv_done"] else "No", e["pv_cert_no"],
                    dmy(e["pv_valid_upto"]) if e["pv_done"] else "", e["status"],
                    f"'{e['uan_no']}" if e["uan_no"] else "", f"'{e['esic_no']}" if e["esic_no"] else "",
                    e["bank_name"], f"'{e['bank_account']}" if e["bank_account"] else "", e["ifsc"],
                    e["daily_wage"] or ""])
    return Response("﻿" + out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=employees.csv"})


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
@login_required
def employee_delete(emp_id):
    db = get_db()
    emp = db.execute("SELECT * FROM employees WHERE id=?", (emp_id,)).fetchone() or abort(404)
    docs = db.execute("SELECT filename FROM documents WHERE employee_id=?", (emp_id,)).fetchall()
    db.execute("DELETE FROM employees WHERE id=?", (emp_id,))
    db.commit()
    for path in [emp["photo_file"], emp["aadhaar_file"], emp["pv_file"]] + [d["filename"] for d in docs]:
        delete_upload(path)
    flash(f"Employee {emp['name']} deleted.", "success")
    return redirect(url_for("employees"))


@app.route("/employees/<int:emp_id>/documents", methods=["POST"])
@login_required
def document_upload(emp_id):
    db = get_db()
    db.execute("SELECT 1 FROM employees WHERE id=?", (emp_id,)).fetchone() or abort(404)
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
        flash(f"{count} document(s) uploaded.", "success")
    elif not request.files.getlist("files") or not any(f.filename for f in request.files.getlist("files")):
        flash("Choose at least one file to upload.", "error")
    return redirect(url_for("employee_view", emp_id=emp_id) + "#documents")


@app.route("/documents/<int:doc_id>/delete", methods=["POST"])
@login_required
def document_delete(doc_id):
    db = get_db()
    doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone() or abort(404)
    db.execute("DELETE FROM documents WHERE id=?", (doc_id,))
    db.commit()
    delete_upload(doc["filename"])
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
        saved = 0
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
                db.execute("DELETE FROM attendance WHERE employee_id=? AND att_date=?",
                           (eid, att_date.isoformat()))
        db.commit()
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
    """'YYYY-MM' -> (year, month, [1..days_in_month]); 400 on a bad value."""
    try:
        year, mon = map(int, month.split("-"))
        return year, mon, list(range(1, calendar.monthrange(year, mon)[1] + 1))
    except (ValueError, calendar.IllegalMonthError):
        abort(400)


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
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow([f"Attendance register - {firm['name']} - {calendar.month_name[mon]} {year}"])
        w.writerow(["Emp Code", "Name", "Father Name", "Designation", "Skill"] + days +
                   ["Present", "Half Day", "Absent", "Leave", "Weekly Off", "Days Worked"])
        for r in report:
            e = r["emp"]
            t = r["totals"]
            w.writerow([e["emp_code"], e["name"], e["father_name"], e["designation"],
                        e["skill_category"]] + r["codes"] +
                       [t["P"], t["H"], t["A"], t["L"], t["W"], r["worked"]])
        fname = f"attendance_{re.sub(r'[^A-Za-z0-9]+', '_', firm['name'])}_{month}.csv"
        return Response("﻿" + out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={fname}"})

    weekdays = [calendar.weekday(year, mon, d) for d in days]
    return render_template("attendance_report.html", firms=firm_list, firm_id=firm_id, firm=firm,
                           month=month, days=days, weekdays=weekdays, report=report,
                           month_label=f"{calendar.month_name[mon]} {year}")


# ---------------------------------------------------------------- wages

def get_settings():
    s = dict(PAYROLL_DEFAULTS)
    s.update({r["key"]: r["value"] for r in get_db().execute("SELECT key, value FROM settings")})
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


def compute_wages(firm_id, month):
    year, mon, days = month_days(month)
    s = get_settings()
    rates = rate_table()
    units = {"P": 1, "H": 0.5, "W": 1 if s["pay_weekly_off"] else 0, "L": 1 if s["pay_leave"] else 0}
    last_day = f"{month}-{days[-1]:02d}"
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
        gross = round(gross, 2)
        da_amt = round(da_amt, 2)
        a = adj.get(e["id"])
        allowance = a["allowance"] if a else 0
        overtime = (a["overtime"] or 0) if a else 0
        deduction = a["deduction"] if a else 0
        earned = gross + allowance + overtime
        pf_base = min(gross, s["pf_ceiling"]) if s["pf_ceiling"] else gross
        pf = rupees(pf_base * s["pf_rate"] / 100)
        pf_er = rupees(pf_base * s["pf_employer_rate"] / 100)
        esi = esi_er = 0
        if 0 < earned <= s["esi_limit"]:
            esi = math.ceil(round(earned * s["esi_rate"] / 100, 4))
            esi_er = math.ceil(round(earned * s["esi_employer_rate"] / 100, 4))
        min_rate_end, da_rate_end = rate_parts_on(rates, e["skill_category"], last_day)
        rate_end = e["daily_wage"] or min_rate_end
        da_rate_end = 0 if e["daily_wage"] else da_rate_end
        rows.append({
            "emp": e, "totals": reg["totals"], "paid_days": paid_days,
            "rate": rate_end, "min_rate": min_rate_end,
            "basic_rate": (rate_end - da_rate_end) if rate_end else None, "da_rate": da_rate_end,
            "missing_rate": missing_rate and paid_days > 0,
            "below_min": bool(e["daily_wage"]) and gross < gross_min - 0.005,
            "gross": gross, "basic_amt": round(gross - da_amt, 2), "da_amt": da_amt,
            "allowance": allowance, "overtime": overtime, "earned": round(earned, 2),
            "pf": pf, "esi": esi, "deduction": deduction,
            "total_deduction": pf + esi + deduction,
            "net": round(earned - pf - esi - deduction, 2),
            "pf_er": pf_er, "esi_er": esi_er,
            "remarks": a["remarks"] if a else "",
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
        if errors:
            flash(f"{errors} row(s) had an invalid amount and were not saved.", "error")
        else:
            flash("Wage sheet saved.", "success")
        return redirect(url_for("wages", firm_id=firm_id, month=month))

    rows, totals, s = compute_wages(firm_id, month) if firm else ([], {}, get_settings())
    month_label = f"{calendar.month_name[mon]} {year}"

    if request.args.get("export") == "csv" and firm:
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow([f"Wage sheet - {firm['name']} - {month_label}"])
        w.writerow(["Emp Code", "Name", "Father Name", "Skill", "Designation", "UAN", "ESIC IP No",
                    "Bank Account", "IFSC", "Present", "Half Day", "Weekly Off", "Leave", "Paid Days",
                    "Daily Rate", "Basic", "DA", "Basic + DA", "Other Allowance", "Overtime", "Gross Earned",
                    "EPF (Employee)", "ESI (Employee)", "Other Deduction", "Total Deduction", "Net Payable",
                    "EPF (Employer)", "ESI (Employer)", "Remarks"])
        for r in rows:
            e, t = r["emp"], r["totals"]
            w.writerow([e["emp_code"], e["name"], e["father_name"], e["skill_category"], e["designation"],
                        f"'{e['uan_no']}" if e["uan_no"] else "", f"'{e['esic_no']}" if e["esic_no"] else "",
                        f"'{e['bank_account']}" if e["bank_account"] else "", e["ifsc"],
                        t["P"], t["H"], t["W"], t["L"], r["paid_days"], r["rate"] or "", r["basic_amt"],
                        r["da_amt"], r["gross"], r["allowance"], r["overtime"], r["earned"], r["pf"], r["esi"],
                        r["deduction"], r["total_deduction"], r["net"], r["pf_er"], r["esi_er"], r["remarks"]])
        w.writerow(["", "TOTAL"] + [""] * 11 + [totals["paid_days"], "", totals["basic_amt"], totals["da_amt"],
                   totals["gross"], totals["allowance"], totals["overtime"],
                   totals["earned"], totals["pf"], totals["esi"], totals["deduction"],
                   totals["total_deduction"], totals["net"], totals["pf_er"], totals["esi_er"], ""])
        fname = f"wages_{re.sub(r'[^A-Za-z0-9]+', '_', firm['name'])}_{month}.csv"
        return Response("﻿" + out.getvalue(), mimetype="text/csv",
                        headers={"Content-Disposition": f"attachment; filename={fname}"})

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
    db = get_db()
    if request.method == "POST":
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
                flash(f"{skill} rate ₹{rate:g}/day from {eff:%d-%m-%Y} saved.", "success")
        elif action == "rate_delete":
            db.execute("DELETE FROM wage_rates WHERE id=?", (request.form.get("rate_id", type=int),))
            db.commit()
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
            db.executemany("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) "
                           "DO UPDATE SET value=excluded.value", new.items())
            db.commit()
            flash("Payroll settings saved.", "success")
        return redirect(url_for("wage_settings"))
    rates = db.execute("SELECT * FROM wage_rates ORDER BY skill_category, effective_from DESC").fetchall()
    table = rate_table()
    current = {sk: rate_on(table, sk, date.today().isoformat()) for sk in SKILL_CATEGORIES}
    return render_template("wage_settings.html", rates=rates, s=get_settings(), current=current)


@app.errorhandler(413)
def too_large(_e):
    flash("Upload too large. Each request may be at most 25 MB.", "error")
    return redirect(request.referrer or url_for("dashboard"))


init_db()

if __name__ == "__main__":
    # host 0.0.0.0 lets other computers on the workshop LAN open the app.
    app.run(host="0.0.0.0", port=5000, debug=False)
