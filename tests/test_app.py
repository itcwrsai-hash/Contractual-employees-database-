"""Tests for the Contractual Employees Management Software.

Run from the project folder:   python -m unittest
They use a temporary data folder, never your real database.
"""
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest

_TMP = tempfile.mkdtemp(prefix="cems-test-")
os.environ["CEMS_DATA_DIR"] = _TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as A  # noqa: E402
import demo_data  # noqa: E402
from flask import Flask  # noqa: E402
from flask.sessions import SecureCookieSessionInterface  # noqa: E402
from werkzeug.security import generate_password_hash  # noqa: E402

A.app.testing = True


def tearDownModule():
    shutil.rmtree(_TMP, ignore_errors=True)


def connect():
    c = sqlite3.connect(A.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def query(sql, params=()):
    c = connect()
    try:
        return c.execute(sql, params).fetchall()
    finally:
        c.close()


def run(sql, params=()):
    c = connect()
    try:
        cur = c.execute(sql, params)
        c.commit()
        return cur.lastrowid
    finally:
        c.close()


def aadhaar(n):
    """A checksum-valid dummy Aadhaar number in the 9999 range."""
    base = f"9999{n:07d}"
    return next(base + d for d in "0123456789" if A.valid_aadhaar(base + d))


def forged_cookie(key, user_id):
    f = Flask("forger")
    f.secret_key = key
    return SecureCookieSessionInterface().get_signing_serializer(f).dumps(
        {"user_id": user_id, "csrf_token": "x"})


class Base(unittest.TestCase):
    def setUp(self):
        A._login_fails.clear()
        c = connect()
        for table in ("audit_log", "attendance", "payroll_adjustments", "documents", "employees", "firms",
                      "wage_rates", "users"):
            c.execute(f"DELETE FROM {table}")
        c.executemany("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE "
                      "SET value=excluded.value", A.PAYROLL_DEFAULTS.items())
        c.commit()
        c.close()
        self.admin_id = self.add_user("admin", "admin-pass-1", admin=True)
        self.client = A.app.test_client()

    # -- helpers
    def add_user(self, name, password, admin=False, must_change=False):
        return run("INSERT INTO users (username, password_hash, is_admin, must_change_password) VALUES (?,?,?,?)",
                   (name, generate_password_hash(password), int(admin), int(must_change)))

    def token(self, client):
        with client.session_transaction() as s:
            if "csrf_token" in s:
                return s["csrf_token"]
        client.get("/login", follow_redirects=True)  # rendering any page creates the form token
        with client.session_transaction() as s:
            return s["csrf_token"]

    def post(self, path, data=None, client=None):
        client = client or self.client
        return client.post(path, data={"csrf_token": self.token(client), **(data or {})})

    def login(self, name="admin", password="admin-pass-1", client=None):
        return self.post("/login", {"username": name, "password": password}, client)

    def add_firm(self, name="Test Firm"):
        return run("INSERT INTO firms (name) VALUES (?)", (name,))

    def add_employee(self, firm_id, n=1, name="Test Worker", daily_wage=None):
        return run("INSERT INTO employees (firm_id, emp_code, name, father_name, aadhaar_no, skill_category, "
                   "daily_wage) VALUES (?,?,?,?,?,'Skilled',?)",
                   (firm_id, f"T{n:05d}", name, "Father Name", aadhaar(n), daily_wage))

    def mark(self, emp_id, month, days, code="P"):
        for d in days:
            run("INSERT INTO attendance (employee_id, att_date, status) VALUES (?,?,?)",
                (emp_id, f"{month}-{d:02d}", code))

    def audit_actions(self):
        return [r["action"] for r in query("SELECT action FROM audit_log ORDER BY id")]


class SecurityResetTest(Base):
    def test_published_key_is_replaced_and_passwords_must_change(self):
        published = "ab" * 32
        with open(A.KEY_FILE, "w") as f:
            f.write(published)
        A.app.secret_key = published  # the old version running with the key from GitHub
        run("DELETE FROM settings WHERE key=?", (A.SECURITY_RESET_KEY,))
        attacker = A.app.test_client()
        attacker.set_cookie("session", forged_cookie(published, self.admin_id))
        # (old login cookies had no "auth" tag, so even with the old key this version refuses them)
        self.assertEqual(attacker.get("/").status_code, 302)

        A.init_db()  # first start of the new version
        A.app.secret_key = A._load_secret_key()
        self.assertNotEqual(A.app.secret_key, published)
        self.assertEqual(query("SELECT must_change_password FROM users")[0][0], 1)

        r = attacker.get("/")
        self.assertEqual(r.status_code, 302)
        self.assertIn("/login", r.headers["Location"])

        key = A.app.secret_key
        A.init_db()  # later starts leave the key alone
        self.assertEqual(A._load_secret_key(), key)

    def test_forced_password_change(self):
        self.add_user("clerk", "clerk-pass-1", must_change=True)
        other = A.app.test_client()
        self.login("clerk", "clerk-pass-1", other)
        self.login("clerk", "clerk-pass-1")
        r = self.client.get("/employees")
        self.assertEqual(r.status_code, 302)
        self.assertTrue(r.headers["Location"].endswith("/account"))

        same = {"current_password": "clerk-pass-1", "password": "clerk-pass-1", "confirm": "clerk-pass-1"}
        self.post("/account", same)
        self.assertEqual(self.client.get("/employees").status_code, 302)  # same password is refused

        short = {"current_password": "clerk-pass-1", "password": "short", "confirm": "short"}
        self.post("/account", short)
        self.assertEqual(self.client.get("/employees").status_code, 302)

        new = {"current_password": "clerk-pass-1", "password": "a-new-pass-9", "confirm": "a-new-pass-9"}
        self.post("/account", new)
        self.assertEqual(self.client.get("/employees").status_code, 200)
        self.assertEqual(query("SELECT must_change_password FROM users WHERE username='clerk'")[0][0], 0)
        # the login on the other computer ended when the password changed
        self.assertEqual(other.get("/employees").status_code, 302)
        self.assertIn("Password changed", self.audit_actions())


class LoginTest(Base):
    def test_lockout_after_repeated_failures(self):
        for _ in range(A.LOGIN_MAX_FAILS):
            self.login(password="wrong-password")
        r = self.login()  # right password, but this computer is locked out
        self.assertEqual(r.status_code, 429)
        self.assertEqual(self.client.get("/").status_code, 302)
        self.assertEqual(self.audit_actions().count("Login failed"), A.LOGIN_MAX_FAILS)

        other = A.app.test_client()
        other.environ_base["REMOTE_ADDR"] = "10.0.0.2"
        self.login(client=other)
        self.assertEqual(other.get("/").status_code, 200)

    def test_deleted_user_is_logged_out(self):
        clerk = self.add_user("clerk", "clerk-pass-1")
        c2 = A.app.test_client()
        self.login("clerk", "clerk-pass-1", c2)
        self.assertEqual(c2.get("/").status_code, 200)
        self.login()
        self.post("/users", {"action": "delete", "user_id": clerk})
        self.assertEqual(c2.get("/").status_code, 302)

    def test_open_redirect_blocked(self):
        for nxt in ("//evil.example", "/\\evil.example"):
            r = self.client.post("/login?next=" + nxt, data={"csrf_token": self.token(self.client),
                                                            "username": "admin", "password": "admin-pass-1"})
            self.assertFalse(r.headers["Location"].startswith(("//", "/\\")), r.headers["Location"])

    def test_security_headers(self):
        r = self.client.get("/login")
        self.assertEqual(r.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(r.headers["X-Frame-Options"], "SAMEORIGIN")

    def test_setup_needs_8_character_password(self):
        run("DELETE FROM users")
        self.post("/setup", {"username": "boss", "password": "1234567", "confirm": "1234567"})
        self.assertEqual(query("SELECT COUNT(*) FROM users")[0][0], 0)
        self.post("/setup", {"username": "boss", "password": "12345678", "confirm": "12345678"})
        self.assertEqual(query("SELECT is_admin FROM users WHERE username='boss'")[0][0], 1)


class RolesTest(Base):
    def setUp(self):
        super().setUp()
        self.firm = self.add_firm()
        self.emp = self.add_employee(self.firm)
        self.add_user("clerk", "clerk-pass-1")
        self.login("clerk", "clerk-pass-1")

    def test_normal_user_cannot_do_admin_things(self):
        self.assertEqual(self.client.get("/users").status_code, 302)
        self.assertEqual(self.client.get("/audit").status_code, 302)
        self.post("/users", {"action": "add", "username": "sneaky", "password": "sneaky-pass-1", "is_admin": "1"})
        self.post(f"/employees/{self.emp}/delete")
        self.post(f"/firms/{self.firm}/delete")  # (also blocked because it has an employee)
        self.post("/wages/settings", {"action": "rate_add", "skill_category": "Skilled",
                                      "effective_from": "2026-04-01", "daily_rate": "1"})
        self.assertFalse(query("SELECT 1 FROM users WHERE username='sneaky'"))
        self.assertTrue(query("SELECT 1 FROM employees WHERE id=?", (self.emp,)))
        self.assertFalse(query("SELECT 1 FROM wage_rates"))
        page = self.client.get("/wages/settings")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Only an administrator", page.data)

    def test_normal_user_can_do_daily_work(self):
        self.assertEqual(self.client.get(f"/employees/{self.emp}").status_code, 200)
        self.post("/attendance", {"firm_id": self.firm, "date": "2026-10-01", f"status_{self.emp}": "P"})
        self.assertTrue(query("SELECT 1 FROM attendance WHERE employee_id=?", (self.emp,)))
        self.assertIn("Attendance saved", self.audit_actions())

    def test_admin_user_management(self):
        self.login()  # as admin now
        self.post("/users", {"action": "role", "user_id": self.admin_id, "is_admin": "0"})
        self.assertEqual(query("SELECT is_admin FROM users WHERE id=?", (self.admin_id,))[0][0], 1)
        self.assertEqual(self.post("/users", {"action": "delete", "user_id": "abc"}).status_code, 302)
        self.post("/users", {"action": "add", "username": "new", "password": "temp-pass-1"})
        self.assertEqual(query("SELECT must_change_password FROM users WHERE username='new'")[0][0], 1)
        clerk = query("SELECT id FROM users WHERE username='clerk'")[0][0]
        self.post("/users", {"action": "reset", "user_id": clerk, "password": "temp-pass-2"})
        self.assertEqual(query("SELECT must_change_password FROM users WHERE id=?", (clerk,))[0][0], 1)


class EmployeeDeleteTest(Base):
    def setUp(self):
        super().setUp()
        self.firm = self.add_firm()
        self.login()

    def test_delete_blocked_when_attendance_exists(self):
        emp = self.add_employee(self.firm)
        self.mark(emp, "2026-10", [1])
        self.post(f"/employees/{emp}/delete")
        self.assertTrue(query("SELECT 1 FROM employees WHERE id=?", (emp,)))
        self.assertTrue(query("SELECT 1 FROM attendance WHERE employee_id=?", (emp,)))

    def test_delete_allowed_for_record_without_history(self):
        emp = self.add_employee(self.firm)
        self.post(f"/employees/{emp}/delete")
        self.assertFalse(query("SELECT 1 FROM employees WHERE id=?", (emp,)))
        self.assertIn("Employee deleted", self.audit_actions())


class ReportsTest(Base):
    def setUp(self):
        super().setUp()
        self.firm = self.add_firm()
        self.login()

    def test_month_must_be_yyyy_mm(self):
        self.assertEqual(self.client.get(f"/attendance/report?firm_id={self.firm}&month=2026-1").status_code, 400)
        self.assertEqual(self.client.get(f"/wages?firm_id={self.firm}&month=2026-13").status_code, 400)
        self.assertEqual(self.client.get(f"/attendance/report?firm_id={self.firm}&month=2026-01").status_code, 200)

    def test_csv_export_escapes_formulas(self):
        self.add_employee(self.firm, name='=HYPERLINK("http://evil.example","Click")')
        text = self.client.get("/employees/export.csv").get_data(as_text=True)
        self.assertIn("'=HYPERLINK", text)
        self.assertIn(",-,", text)  # an empty date stays a plain dash
        self.assertIn("Export", self.audit_actions())

    def test_gate_pass_masks_aadhaar(self):
        emp = self.add_employee(self.firm, n=7)
        number = aadhaar(7)
        page = self.client.get(f"/employees/{emp}/card").get_data(as_text=True)
        self.assertIn(f"XXXX XXXX {number[-4:]}", page)
        self.assertNotIn(number[:4] + " " + number[4:8], page)


class EsiTest(Base):
    """ESI coverage: overtime is left out when checking the ceiling, and coverage lasts for the
    whole contribution period (April–September, October–March)."""

    def setUp(self):
        super().setUp()
        self.firm = self.add_firm()
        self.emp = self.add_employee(self.firm, daily_wage=800)

    def wages(self, month):
        with A.app.app_context():
            rows, _totals, _s = A.compute_wages(self.firm, month)
        return rows[0]

    def test_contribution_periods(self):
        self.assertEqual(A.esi_period_months("2026-02"), ["2025-10", "2025-11", "2025-12", "2026-01", "2026-02"])
        self.assertEqual(A.esi_period_months("2026-04"), ["2026-04"])
        self.assertEqual(A.esi_period_months("2026-09")[0], "2026-04")
        self.assertEqual(A.esi_period_months("2026-10"), ["2026-10"])

    def test_overtime_not_counted_for_ceiling(self):
        self.mark(self.emp, "2025-10", range(1, 26))  # 25 days x 800 = 20,000
        run("INSERT INTO payroll_adjustments (employee_id, month, overtime) VALUES (?, '2025-10', 2000)", (self.emp,))
        r = self.wages("2025-10")
        self.assertEqual(r["earned"], 22000)
        self.assertTrue(r["esi_covered"])
        self.assertEqual(r["esi"], 165)  # 0.75% of 22,000 incl. overtime

    def test_coverage_continues_to_end_of_period(self):
        self.mark(self.emp, "2025-10", range(1, 26))  # 20,000: covered from October
        self.mark(self.emp, "2025-11", range(1, 28))  # 21,600: above the ceiling, still covered
        self.assertEqual(self.wages("2025-11")["esi"], 162)

    def test_not_covered_when_above_ceiling_from_start_of_period(self):
        self.mark(self.emp, "2026-04", range(1, 28))  # 21,600 in the first month of a period
        r = self.wages("2026-04")
        self.assertFalse(r["esi_covered"])
        self.assertEqual(r["esi"], 0)

    def test_net_pay_adds_up(self):
        self.mark(self.emp, "2025-10", range(1, 20))
        r = self.wages("2025-10")
        self.assertAlmostEqual(r["net"], r["earned"] - r["pf"] - r["esi"] - r["deduction"])


class AuditTest(Base):
    def test_changes_are_logged_without_aadhaar(self):
        self.login()
        self.post("/firms/new", {"name": "Audit Firm"})
        firm = query("SELECT id FROM firms WHERE name='Audit Firm'")[0][0]
        self.post(f"/firms/{firm}/edit", {"name": "Audit Firm", "mobile": "9876543210"})
        number = aadhaar(42)
        self.post("/employees/new", {"firm_id": firm, "name": "Ravi", "father_name": "Mohan",
                                     "aadhaar_no": number, "skill_category": "Unskilled"})
        self.assertTrue(query("SELECT 1 FROM employees WHERE aadhaar_no=?", (number,)))
        details = {r["action"]: r["details"] for r in query("SELECT action, details FROM audit_log")}
        self.assertIn("Firm added", details)
        self.assertIn("mobile", details["Firm edited"])
        self.assertIn("Ravi", details["Employee added"])
        self.assertFalse(any(number in (d or "") for d in details.values()))
        page = self.client.get("/audit")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Employee added", page.data)


class QrVerifyTest(Base):
    """The QR code on the gate pass opens a public page that says whether the pass is valid."""

    ADDRESS = "http://192.168.1.20:5000"

    def setUp(self):
        super().setUp()
        self.firm = self.add_firm()
        self.emp = self.add_employee(self.firm, n=9)
        run("UPDATE employees SET mobile='9876500000', address='Ward 5, Hidden Street', pv_done=1, "
            "pv_valid_upto='2099-01-01' WHERE id=?", (self.emp,))
        self.login()
        self.public = A.app.test_client()  # someone at the gate, not logged in

    def qr_token(self):
        return query("SELECT verify_token FROM employees WHERE id=?", (self.emp,))[0][0]

    def set_address(self, value=ADDRESS):
        return self.post("/settings/verify-url", {"verify_base_url": value})

    def check(self, token=None):
        return self.public.get(f"/v/{token or self.qr_token()}")

    def test_qr_printed_once_address_is_set(self):
        page = self.client.get(f"/employees/{self.emp}/card").get_data(as_text=True)
        self.assertNotIn('class="qr"', page)  # opened as localhost: a phone could not reach it
        self.assertIn("without the QR code", page)
        self.set_address(self.ADDRESS + "/")
        page = self.client.get(f"/employees/{self.emp}/card").get_data(as_text=True)
        self.assertIn('class="qr"', page)
        self.assertIn(self.ADDRESS, page)
        self.assertRegex(self.qr_token(), r"^[A-Za-z0-9_-]{16}$")
        self.assertEqual(query("SELECT value FROM settings WHERE key='verify_base_url'")[0][0], self.ADDRESS)

    def test_bad_address_rejected(self):
        for bad in ("javascript:alert(1)", "http://x/<b>", "ftp://192.168.1.20", "http://user:pw@host"):
            self.set_address(bad)
            self.assertFalse(query("SELECT 1 FROM settings WHERE key='verify_base_url'"), bad)

    def test_valid_pass_shows_only_card_details(self):
        self.client.get(f"/employees/{self.emp}")  # creates the QR code
        r = self.check()
        page = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("✔ VALID", page)
        self.assertIn("Test Worker", page)
        self.assertEqual(r.headers["Cache-Control"], "no-store")
        for private in (aadhaar(9), aadhaar(9)[-4:], "9876500000", "Hidden Street", "Father Name"):
            self.assertNotIn(private, page)

    def test_left_firm_or_expired_is_not_valid(self):
        self.client.get(f"/employees/{self.emp}")
        run("UPDATE employees SET status='Inactive' WHERE id=?", (self.emp,))
        self.assertIn("NOT VALID", self.check().get_data(as_text=True))
        run("UPDATE employees SET status='Active', gp_valid_upto='2020-01-01' WHERE id=?", (self.emp,))
        page = self.check().get_data(as_text=True)
        self.assertIn("NOT VALID", page)
        self.assertIn("expired on 01-01-2020", page)
        run("UPDATE employees SET gp_valid_upto=NULL WHERE id=?", (self.emp,))
        run("UPDATE firms SET status='Inactive' WHERE id=?", (self.firm,))
        self.assertIn("NOT VALID", self.check().get_data(as_text=True))

    def test_police_verification_expired_is_a_warning(self):
        self.client.get(f"/employees/{self.emp}")
        run("UPDATE employees SET pv_valid_upto='2020-01-01' WHERE id=?", (self.emp,))
        self.assertIn("VALID – WARNING", self.check().get_data(as_text=True))

    def test_unknown_code_is_not_valid(self):
        for token in ("A" * 16, "short", "../../etc"):
            r = self.public.get(f"/v/{token}")
            self.assertEqual(r.status_code, 404, token)
        self.assertIn("NOT VALID", self.public.get("/v/" + "A" * 16).get_data(as_text=True))

    def test_reissue_cancels_old_cards(self):
        self.client.get(f"/employees/{self.emp}")
        old = self.qr_token()
        self.post(f"/employees/{self.emp}/reissue-qr")
        new = self.qr_token()
        self.assertNotEqual(old, new)
        self.assertEqual(self.check(old).status_code, 404)
        self.assertEqual(self.check(new).status_code, 200)
        self.assertIn("Gate pass QR reissued", self.audit_actions())

        self.add_user("clerk", "clerk-pass-1")
        clerk = A.app.test_client()
        self.login("clerk", "clerk-pass-1", clerk)
        self.post(f"/employees/{self.emp}/reissue-qr", client=clerk)
        self.assertEqual(self.qr_token(), new)  # only an administrator can reissue

    def test_photo_is_public_only_through_the_qr_code(self):
        os.makedirs(os.path.join(A.UPLOAD_DIR, "photos"), exist_ok=True)
        with open(os.path.join(A.UPLOAD_DIR, "photos", "qrtest.jpg"), "wb") as f:
            f.write(b"\xff\xd8\xff fake jpeg")
        run("UPDATE employees SET photo_file='photos/qrtest.jpg' WHERE id=?", (self.emp,))
        self.client.get(f"/employees/{self.emp}")
        r = self.public.get(f"/v/{self.qr_token()}/photo")
        self.assertEqual(r.status_code, 200)
        r.close()
        self.assertEqual(self.public.get("/files/photos/qrtest.jpg").status_code, 302)  # needs login

    def test_user_who_must_change_password_can_still_check(self):
        self.client.get(f"/employees/{self.emp}")
        self.add_user("newbie", "newbie-pass-1", must_change=True)
        newbie = A.app.test_client()
        self.login("newbie", "newbie-pass-1", newbie)
        self.assertEqual(newbie.get(f"/v/{self.qr_token()}").status_code, 200)

    def test_confirm_dialogs_escape_names(self):
        run("UPDATE employees SET name=? WHERE id=?", ("D'Souza \"Ram\"", self.emp))
        page = self.client.get(f"/employees/{self.emp}").get_data(as_text=True)
        self.assertIn("D&#39;Souza &#34;Ram&#34;", page)
        self.assertNotIn("confirm('", page)


class SmokeTest(Base):
    """Every page opens with the demo data loaded, for an administrator and a normal user."""

    def test_pages_open(self):
        c = connect()
        demo_data.load(c)
        c.close()
        firm = query("SELECT MIN(id) FROM firms")[0][0]
        emp = query("SELECT MIN(id) FROM employees WHERE firm_id=?", (firm,))[0][0]
        month = query("SELECT substr(MAX(att_date), 1, 7) FROM attendance")[0][0]
        pages = ["/", "/firms", f"/firms/{firm}", f"/firms/{firm}/edit", "/firms/new", "/employees",
                 "/employees/new", f"/employees/{emp}", f"/employees/{emp}/edit", f"/employees/{emp}/card",
                 f"/employees/{emp}/appointment", f"/firms/{firm}/gate-passes", f"/firms/{firm}/appointment-letters",
                 "/reports/id-cards?firm_id=%d" % firm, "/reports/appointment-letters?firm_id=%d" % firm,
                 f"/attendance?firm_id={firm}", f"/attendance/report?firm_id={firm}&month={month}",
                 f"/wages?firm_id={firm}&month={month}", f"/wages/payslip/{emp}?month={month}",
                 f"/wages/payslips/{firm}?month={month}", "/wages/settings", "/search?q=Sahu",
                 "/search/suggest?q=Sa", "/account", "/employees/export.csv",
                 f"/wages?firm_id={firm}&month={month}&export=csv",
                 f"/attendance/report?firm_id={firm}&month={month}&export=csv"]
        self.login()
        for p in pages + ["/users", "/audit"]:
            self.assertEqual(self.client.get(p).status_code, 200, p)
        self.add_user("clerk", "clerk-pass-1")
        clerk = A.app.test_client()
        self.login("clerk", "clerk-pass-1", clerk)
        for p in pages:
            self.assertEqual(clerk.get(p).status_code, 200, p)


if __name__ == "__main__":
    unittest.main()
