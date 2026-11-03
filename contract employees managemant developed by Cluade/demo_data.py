"""Load or remove dummy (demo) data for trying out the dashboard, reports and wage sheet.

    python demo_data.py           load demo firms, employees, attendance and sample wage rates
    python demo_data.py --remove  delete everything this script added (your real data is untouched)

Demo firms are recognised by "DEMO" in their work order number; demo wage rates by
"DEMO" in their notes. Login accounts are never touched.
"""
import random
import sqlite3
import sys
from datetime import date, timedelta

import app as A

DEMO_TAG = "DEMO"

FIRMS = [
    # name, owner, nature of work, employees, attendance quality (0-1), contract ends in days
    ("M/s Chhattisgarh Engineering Works", "Rajesh Agrawal", "Wagon body repair, welding and fabrication", 34, 0.93, 400),
    ("M/s Shree Balaji Enterprises", "Sanjay Sahu", "Wagon painting and stencilling", 22, 0.90, 250),
    ("M/s Mahamaya Manpower Services", "Pradeep Verma", "Material handling, loading and unloading", 28, 0.84, 22),
    ("M/s Bhilai Fabricators", "Anil Deshmukh", "Bogie and coupler repair support", 18, 0.95, 520),
    ("M/s Raipur Facility Services", "Meena Tiwari", "Shop floor cleaning and housekeeping", 16, 0.88, 300),
    ("M/s Om Sai Electricals", "Vikas Chandrakar", "Electrical maintenance of shop machinery", 10, 0.92, 180),
]
TRADES = {
    "Skilled": ["Welder", "Fitter", "Electrician", "Machinist", "Painter"],
    "Semi-skilled": ["Asst. Fitter", "Asst. Welder", "Crane Operator", "Driver", "Asst. Painter"],
    "Unskilled": ["Helper", "Khalasi", "Loader", "Sweeper"],
}
SKILL_MIX = {  # weights per firm index: skilled, semi-skilled, unskilled
    0: (5, 3, 2), 1: (4, 3, 3), 2: (0, 2, 8), 3: (5, 3, 2), 4: (0, 1, 9), 5: (6, 3, 1),
}
QUALS = {"Skilled": ["ITI (Welder)", "ITI (Fitter)", "ITI (Electrician)", "Diploma", "12th + ITI"],
         "Semi-skilled": ["10th", "12th", "ITI"], "Unskilled": ["Below 8th", "8th", "10th"]}
MALE = ["Ramesh", "Suresh", "Mahesh", "Dinesh", "Rakesh", "Santosh", "Manoj", "Ajay", "Vijay", "Sunil", "Anil",
        "Deepak", "Rajkumar", "Shyam", "Mohan", "Gopal", "Lakhan", "Bhupendra", "Tikaram", "Dhaniram", "Ishwar",
        "Kamlesh", "Narendra", "Pawan", "Rohit", "Sachin", "Tulsiram", "Umesh", "Yogesh", "Hemant"]
FEMALE = ["Sunita", "Geeta", "Kiran", "Rekha", "Savitri", "Meera", "Lata", "Anita", "Pushpa", "Kavita"]
SURNAMES = ["Sahu", "Verma", "Yadav", "Patel", "Sinha", "Chandrakar", "Dewangan", "Nishad", "Sonwani", "Dhruw",
            "Netam", "Markam", "Thakur", "Tandon", "Banjare", "Kurre", "Sharma", "Mishra", "Rao", "Kumar"]
AREAS = ["Urkura", "Bhanpuri", "Hirapur", "Tatibandh", "Gudhiyari", "Mowa", "Sarona", "Birgaon", "Khamtarai",
         "Pandri", "Telibandha", "Mandir Hasaud", "Abhanpur", "Tilda"]
BANKS = [("State Bank of India", "SBIN0001234"), ("Punjab National Bank", "PUNB0123400"),
         ("Bank of Baroda", "BARB0RAIPUR"), ("Chhattisgarh Rajya Gramin Bank", "SBIN0RRCHGB")]
SAMPLE_RATES = {"Skilled": (1035.0, 166.0), "Semi-skilled": (868.0, 138.0), "Unskilled": (783.0, 124.0)}  # total, DA
PINCODES = ["492001", "492004", "492007", "492008", "492009", "492010", "492099", "493111"]


def demo_aadhaar(used):
    """A checksum-valid number in the 9999xxxx range, so it is obviously dummy."""
    while True:
        body = "9999" + "".join(random.choice("0123456789") for _ in range(7))
        for d in "0123456789":
            n = body + d
            if A.valid_aadhaar(n) and n not in used:
                used.add(n)
                return n


def remove(db):
    firm_ids = [r[0] for r in db.execute("SELECT id FROM firms WHERE work_order_no LIKE ?", (f"%{DEMO_TAG}%",))]
    emp_ids = [r[0] for r in db.execute(
        f"SELECT id FROM employees WHERE firm_id IN ({','.join('?' * len(firm_ids))})", firm_ids)] if firm_ids else []
    if emp_ids:
        marks = ",".join("?" * len(emp_ids))
        for table in ("attendance", "documents", "payroll_adjustments"):
            db.execute(f"DELETE FROM {table} WHERE employee_id IN ({marks})", emp_ids)
        db.execute(f"DELETE FROM employees WHERE id IN ({marks})", emp_ids)
    if firm_ids:
        db.execute(f"DELETE FROM firms WHERE id IN ({','.join('?' * len(firm_ids))})", firm_ids)
    rates = db.execute("DELETE FROM wage_rates WHERE notes LIKE ?", (f"%{DEMO_TAG}%",)).rowcount
    db.commit()
    print(f"Removed {len(firm_ids)} demo firms, {len(emp_ids)} demo employees and {rates} demo wage rates.")


def load(db):
    if db.execute("SELECT 1 FROM firms WHERE work_order_no LIKE ?", (f"%{DEMO_TAG}%",)).fetchone():
        sys.exit("Demo data is already loaded. Run 'python demo_data.py --remove' first to reload it.")
    random.seed(2026)
    today = date.today()
    start = today - timedelta(days=75)
    used = {r[0] for r in db.execute("SELECT aadhaar_no FROM employees WHERE aadhaar_no IS NOT NULL")}
    next_code = 1 + (db.execute("SELECT MAX(id) FROM employees").fetchone()[0] or 0)

    for skill, (rate, da) in SAMPLE_RATES.items():
        if not db.execute("SELECT 1 FROM wage_rates WHERE skill_category=?", (skill,)).fetchone():
            db.execute("INSERT INTO wage_rates (skill_category, daily_rate, da_rate, effective_from, notes) "
                       "VALUES (?,?,?,?,?)", (skill, rate, da, "2024-04-01",
                        f"{DEMO_TAG} sample rate - replace with the notified minimum wage"))

    n_emp = n_att = 0
    for fi, (name, owner, work, size, quality, ends_in) in enumerate(FIRMS):
        cur = db.execute(
            "INSERT INTO firms (name, owner_name, contact_person, mobile, email, address, gst_no, pan_no, "
            "labour_licence_no, work_order_no, work_description, contract_start, contract_end, status, "
            "pincode, lin_no, registration_no, loa_date) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?, 'Active',?,?,?,?)",
            (name, owner, random.choice(MALE) + " " + random.choice(SURNAMES), f"9{random.randint(100000000, 999999999)}",
             f"contact{fi + 1}@example.com", f"{random.randint(1, 200)}, {random.choice(AREAS)}, Raipur (C.G.)",
             f"22ABCDE{1000 + fi}F1Z{fi}", f"ABCDE{1000 + fi}F", f"ALC/RPR/{2024 + fi % 2}/{300 + fi}",
             f"WRS-RPR/{DEMO_TAG}/{today.year}/{fi + 1:02d}", work, "2025-04-01",
             (today + timedelta(days=ends_in)).isoformat(), random.choice(PINCODES),
             f"1-{random.randint(10**9, 10**10 - 1)}-{random.randint(100, 999)}",
             f"UDYAM-CG-14-{random.randint(10**6, 10**7 - 1):07d}", "2025-03-15"))
        firm_id = cur.lastrowid
        for k in range(size):
            skill = random.choices(list(TRADES), SKILL_MIX[fi])[0]
            female = fi == 4 and random.random() < 0.5 or random.random() < 0.05
            surname = random.choice(SURNAMES)
            first = random.choice(FEMALE if female else MALE)
            joined = start - timedelta(days=random.randint(30, 900))
            if k >= size - 2:  # a couple of recent joiners
                joined = today - timedelta(days=random.randint(5, 25))
            r = random.random()
            if r < 0.10:
                pv = (0, None, None)
            else:
                days = (random.randint(-40, -1) if r < 0.17 else random.randint(1, 30) if r < 0.27
                        else random.randint(31, 900))
                pv = (1, f"SP/RPR/PV/{random.randint(1000, 9999)}", (today + timedelta(days=days)).isoformat())
            bank, ifsc = random.choice(BANKS)
            status = "Inactive" if k == 0 and fi in (1, 2) else "Active"
            emp = db.execute(
                "INSERT INTO employees (firm_id, emp_code, name, father_name, dob, gender, aadhaar_no, mobile, "
                "address, pv_done, pv_cert_no, pv_valid_upto, qualification, skill_category, designation, "
                "date_of_joining, status, uan_no, esic_no, bank_name, bank_account, ifsc, blood_group, pincode) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (firm_id, f"CE{next_code:05d}", f"{first} {surname}", f"{random.choice(MALE)} {surname}",
                 (today - timedelta(days=random.randint(20 * 365, 55 * 365))).isoformat(),
                 "Female" if female else "Male", demo_aadhaar(used), f"9{random.randint(100000000, 999999999)}",
                 f"Ward {random.randint(1, 70)}, {random.choice(AREAS)}, Raipur (C.G.)", *pv,
                 random.choice(QUALS[skill]), skill, random.choice(TRADES[skill]), joined.isoformat(), status,
                 f"10{random.randint(10**9, 10**10 - 1)}", f"{random.randint(10**9, 10**10 - 1)}",
                 bank, f"{random.randint(10**10, 10**11 - 1)}", ifsc,
                 random.choices(A.BLOOD_GROUPS, [30, 2, 32, 2, 8, 1, 24, 1])[0], random.choice(PINCODES)))
            eid = emp.lastrowid
            next_code += 1
            n_emp += 1

            # attendance from joining date (or demo start) to today
            reliability = min(0.99, max(0.6, random.gauss(quality, 0.05)))
            d = max(start, joined)
            while d <= today:
                # today: only the first four firms have marked attendance so far
                if d == today and fi >= 4:
                    break
                if status == "Inactive" and d > today - timedelta(days=30):
                    break
                if d.weekday() == 6:
                    code = "W"
                else:
                    x = random.random()
                    code = ("P" if x < reliability else "H" if x < reliability + (1 - reliability) * 0.3
                            else "L" if x < reliability + (1 - reliability) * 0.5 else "A")
                db.execute("INSERT INTO attendance (employee_id, att_date, status) VALUES (?,?,?)",
                           (eid, d.isoformat(), code))
                n_att += 1
                d += timedelta(days=1)

            # some allowances / deductions last month
            if random.random() < 0.2:
                last_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
                db.execute("INSERT INTO payroll_adjustments (employee_id, month, allowance, overtime, deduction, "
                           "remarks) VALUES (?,?,?,?,?,?)",
                           (eid, last_month, random.choice([0, 0, 500, 750]), random.choice([0, 640, 960, 1280]),
                            random.choice([0, 0, 1000, 2000]), random.choice(["Advance recovery", "Arrears", None])))
    db.commit()
    print(f"Loaded {len(FIRMS)} demo firms, {n_emp} demo employees and {n_att} attendance entries.")
    print("Sample wage rates were added only for skill categories that had no rate yet.")


if __name__ == "__main__":
    A.init_db()
    conn = sqlite3.connect(A.DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    if "--remove" in sys.argv:
        remove(conn)
    else:
        load(conn)
    conn.close()
