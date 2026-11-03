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
    Aadhaar card upload (image/PDF). Aadhaar is masked (`XXXX XXXX 1234`) on the ID card / gate pass.
  - **Photo** – upload a file, or capture directly from the webcam.
  - **Police verification** – Yes/No; if Yes: certificate no., *valid upto* date, certificate upload.
  - **Qualification** with upload of any number of documents (plus other documents later).
  - **Skill category** – Skilled / Semi-skilled / Unskilled.
  - Printable ID card / gate pass (with a QR code to check it – see below) and appointment letter.
- **Attendance** – mark per firm per day (Present / Absent / Half day / Leave / Weekly off),
  "mark all" buttons, remarks.
- **Monthly register** per firm with totals and days worked; print or export to Excel (CSV).
- **Wages** –
  - *Wage rates & settings*: minimum daily wage per skill category with an *effective from* date
    (add a new row at every revision; past months keep the old rate). Choose whether weekly-off
    and leave days are paid; set EPF/ESI percentages, the EPF ceiling and the ESI wage ceiling.
  - *Wage sheet* per firm per month: paid days × daily rate, other allowance, overtime and other
    deduction boxes, EPF (on basic, up to the ceiling) and ESI, net pay, plus the contractor's
    EPF/ESI deposit liability. Flags employees with no rate or whose agreed wage is below the
    minimum. Print or export to Excel (CSV).
  - *ESI coverage* follows the ESI Act: an employee is covered when their wages for a month,
    **leaving out overtime**, are within the ceiling, and then stays covered until the end of that
    contribution period (1 April–30 September or 1 October–31 March) even if wages later go above
    it. The contribution is on all gross earnings, overtime included, rounded up to the next rupee.
  - *Pay slip* (Form V) for each employee.
  - Employee profile has UAN, ESIC IP no., bank account, IFSC and an optional agreed daily wage.
- **Dashboard** – counts by skill, today's attendance by firm, alerts for police verification
  expired / expiring within 30 days and contracts ending within 30 days.
- **Search & filters** on employees (firm, skill, police verification state, status) and Excel export.
- **Users, roles and audit log** – see below.

## Checking a gate pass with the QR code

The back of every gate pass has a QR code. Scan it with any phone camera: it opens a page that shows the
employee's photo, name, firm and gate pass validity, with a big **VALID** (green), **VALID – WARNING**
(orange: police verification expired or not done) or **NOT VALID** (red) banner. It checks the live records,
so a card shows NOT VALID as soon as the employee is set *Inactive*, the firm is *Inactive*, or the gate
pass validity date has passed. The page shows the date and time of the check, so an old screenshot is easy
to spot. It never shows Aadhaar, address, mobile or bank details, and no login is needed to open it.

- **The phone must be able to reach this PC**, so connect it to the workshop network (Wi-Fi). Phones on
  mobile data cannot open it unless the program is put on the internet (not recommended without HTTPS).
- **Set the address before printing.** On *ID cards / Gate passes*, an administrator enters the address
  phones use, e.g. `http://192.168.1.20:5000`. Give this PC a **fixed IP address** first: the address is
  printed inside every QR code, so if it changes, the cards must be reprinted. When the program is opened as
  `localhost`, gate passes print without a QR code until the address is set.
- **Lost or withdrawn card:** on the employee's page an administrator clicks **Reissue QR (lost card)**.
  Every card printed before then shows NOT VALID; print a new gate pass.
- Each QR code holds a random 16-character code, so links cannot be guessed or made up. Security staff
  should still check that the phone's address bar shows the workshop address, not some other website.

## Running

1. Install Python 3.10+ (already installed on this PC).
2. Double-click **`start.bat`** (or run `python app.py`). The first time, it installs Flask,
   Waitress (the web server) and Segno (QR codes) from `requirements.txt`; this needs internet once.
3. Open <http://localhost:5000>. On the first visit you create the administrator account.

Other computers on the workshop network can open `http://<this-PC's-IP>:5000`.
Note: browsers only allow webcam capture on `localhost` or HTTPS, so from other computers use
"Upload photo" (on a mobile phone this opens the camera directly).

## Updating to a new version

1. Close the black server window (stop the program) and **back up the `data` folder**.
2. Copy the new program files (`app.py`, `demo_data.py`, `start.bat`, `requirements.txt`,
   `templates`, `static`, `tests`, …) **over** the old ones in the same folder.
   **Keep your existing `data` folder** – it holds all your records. Do not run the new version
   from a different folder: it would start with an empty database.
3. Delete nothing else. Start the program again with `start.bat`.

**Security update (November 2026).** The `data` folder, including the database and the secret
key, was once published in the public GitHub repository. The first time this version starts it
replaces the secret key, which logs everybody out and makes any copied login cookie useless,
and every account that already existed must choose a **new** password at its next login
(re-using the old one is refused). This happens only once.

## Users, roles and security

- **Administrators** can manage user accounts, see the **audit log**, delete firms and
  employees, and change wage rates and payroll settings. **Users** can do everything else
  (firms, employees, documents, attendance, wage sheets, printing and exports).
- The account made at first-time setup is an administrator. When upgrading from a version without
  roles, the oldest account becomes the administrator and other accounts become users; an
  administrator can change this on the **Users** page.
- Passwords must be at least 8 characters. A user added by an administrator, or whose password
  an administrator resets, must choose their own password at the next login. Change your own
  password under **My account**. Changing or resetting a password, or deleting an account, logs
  that account out on every computer.
- After 5 wrong passwords from one computer, that computer must wait 15 minutes. A login ends
  after 8 hours without use.
- **Employees with attendance or wage entries cannot be deleted**, because that would change past
  attendance registers and wage sheets: set their Status to *Inactive* instead. Deleting is only
  for a record entered by mistake.
- The **audit log** (Admin → Audit log) records logins and failed logins, changes to firms,
  employees, documents, attendance, wage sheets, wage rates and settings, CSV exports and user
  account changes – who, when and from which computer. It lists *which* fields changed, never
  Aadhaar or bank numbers.
- CSV exports are protected against "formula injection" (text such as `=HYPERLINK(...)` typed
  into a name or remark is exported as plain text).
- The program uses plain HTTP on the workshop network. For HTTPS (encrypted connections, which
  also allow the webcam from other computers) it must be put behind a web server with a
  certificate; ask the IT Center.

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
- `data/secret.key` – the key that protects logins; anyone who has it can log in without a password

**Back up the whole `data` folder regularly.** It contains Aadhaar and bank data – keep it on a
protected computer and do not share it.

**Never put the `data` folder on GitHub or anywhere public.** The `.gitignore` file keeps it out
of git commits, but it does not protect you when you upload files through the GitHub website –
never include the `data` folder there. To keep the data outside the program folder, set the
environment variable `CEMS_DATA_DIR` to another folder (e.g. `D:\CEMS-data`) before starting.

## Tests

From the program folder:

    python -m unittest

The tests use a temporary folder and never touch your real `data` folder.
