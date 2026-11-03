# Contractual Employees Management Software

Developed by IT Center / Wagon Repair Shop / Raipur / SECR

A web application to manage contractor firms working in the workshop, their contract
employees, and daily attendance.

## Features

- **Firms** – name, owner, contact person, mobile, email, address, GST, PAN, labour licence,
  work order no., nature of work, contract start/end dates, active/inactive.
- **Employees** – name, father's name, DOB, gender, mobile, address, firm, employee code
  (auto `CE00001` if blank), designation/trade, date of joining, status.
  - **Aadhaar** number (checked with the official Verhoeff checksum, duplicates blocked) and
    Aadhaar card upload (image/PDF). Aadhaar is masked on the ID card.
  - **Photo** – upload a file, or capture directly from the webcam.
  - **Police verification** – Yes/No; if Yes: certificate no., *valid upto* date, certificate upload.
  - **Qualification** with upload of any number of documents (plus other documents later).
  - **Skill category** – Skilled / Semi-skilled / Unskilled.
  - Printable ID card.
- **Attendance** – mark per firm per day (Present / Absent / Half day / Leave / Weekly off),
  "mark all" buttons, remarks.
- **Monthly register** per firm with totals and days worked; print or export to Excel (CSV).
- **Wages** –
  - *Wage rates & settings*: minimum daily wage per skill category with an *effective from* date
    (add a new row at every revision; past months keep the old rate). Choose whether weekly-off
    and leave days are paid; set EPF/ESI percentages, the EPF ceiling and the ESI limit.
  - *Wage sheet* per firm per month: paid days × daily rate, other allowance and other deduction
    boxes, EPF (on basic, up to the ceiling) and ESI (on gross, if within the limit), net pay,
    plus the contractor's EPF/ESI deposit liability. Flags employees with no rate or whose agreed
    wage is below the minimum. Print or export to Excel (CSV).
  - *Pay slip* for each employee.
  - Employee profile has UAN, ESIC IP no., bank account, IFSC and an optional agreed daily wage.
- **Dashboard** – counts by skill, today's attendance by firm, alerts for police verification
  expired / expiring within 30 days and contracts ending within 30 days.
- **Search & filters** on employees (firm, skill, police verification state, status) and Excel export.
- Login required; multiple user accounts.

## Running

1. Install Python 3.10+ (already installed on this PC).
2. Double-click **`start.bat`** (or run `python app.py`).
3. Open <http://localhost:5000>. On the first visit you create the admin account.

Other computers on the workshop network can open `http://<this-PC's-IP>:5000`.
Note: browsers only allow webcam capture on `localhost` or HTTPS, so from other computers use
"Upload photo" (on a mobile phone this opens the camera directly).

## Dummy (demo) data

To try the dashboard, reports and wage sheet with sample data:

    python demo_data.py            load 6 demo firms, ~128 employees, 75 days of attendance
    python demo_data.py --remove   delete all demo data before entering real data

Demo firms have `DEMO` in their work order number and demo Aadhaar numbers start with
`9999`. Removing demo data never touches real firms, employees or login accounts. The sample
wage rates are marked DEMO too – replace them with the notified minimum wages.

## Data & backup

Everything is stored in the `data` folder:

- `data/contract_employees.db` – the database (SQLite)
- `data/uploads/` – photos, Aadhaar cards, police verification certificates, documents

**Back up the whole `data` folder regularly.** It contains Aadhaar data – keep it on a
protected computer and do not share it.
